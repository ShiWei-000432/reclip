"""ReClip — self-hosted media downloader.

Extended build. On top of the upstream single-file design this adds:

  * a download queue with a bounded worker pool
  * live progress reporting parsed from yt-dlp
  * cancellable jobs (queued or running)
  * optional bearer-token authentication
  * runtime-configurable proxy and cookie sources
  * TTL-based reaping of finished jobs and their files

Everything stays dependency-free: Flask + yt-dlp + ffmpeg, no build step.
"""
import collections
import glob
import hmac
import json
import os
import queue
import re
import subprocess
import threading
import time
import uuid

from flask import Flask, jsonify, render_template, request, send_file

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DOWNLOAD_DIR = os.path.join(BASE_DIR, "downloads")
SETTINGS_PATH = os.path.join(BASE_DIR, "settings.json")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

app = Flask(__name__)

# yt-dlp writes one file per selected format while working on a merge, e.g.
#   <job_id>.f30016.mp4   (video-only)
#   <job_id>.f30280.m4a   (audio-only)
# and only removes them once ffmpeg has produced the real <job_id>.mp4.
# If ffmpeg is unavailable the merge never happens and these are left behind,
# so they must never be mistaken for a finished download.
INTERMEDIATE_RE = re.compile(
    r"\.f[^.\\/]+\.(mp4|m4a|mp3|webm|opus|aac|mka|flac|wav)$", re.IGNORECASE
)

# yt-dlp emits one of these per progress tick when told to (see build_command).
# No separator between the prefix and the first field: a leading "|" would
# split into an empty first element and shift every value by one.
PROGRESS_PREFIX = "@@RECLIP_PROGRESS@@"
PROGRESS_FIELDS = (
    "%(progress.downloaded_bytes)s|%(progress.total_bytes)s|"
    "%(progress.total_bytes_estimate)s|%(progress.speed)s|%(progress.eta)s"
)
PROGRESS_TEMPLATE = "download:" + PROGRESS_PREFIX + PROGRESS_FIELDS

ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

# --------------------------------------------------------------------- settings

DEFAULT_SETTINGS = {
    "concurrency": 3,            # simultaneous yt-dlp processes
    "timeout": 300,              # per-job wall clock limit, seconds
    "proxy": "",                 # e.g. http://127.0.0.1:7890
    "cookies_from_browser": "",  # e.g. chrome, edge, firefox
    "cookies_file": "",          # path to a Netscape-format cookies.txt
    "job_ttl": 3600,             # how long finished jobs+files are kept
}

_settings_lock = threading.Lock()


def load_settings():
    values = dict(DEFAULT_SETTINGS)
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as fh:
            stored = json.load(fh)
        if isinstance(stored, dict):
            values.update({k: v for k, v in stored.items() if k in DEFAULT_SETTINGS})
    except (OSError, ValueError):
        pass
    return values


def save_settings(values):
    try:
        with open(SETTINGS_PATH, "w", encoding="utf-8") as fh:
            json.dump(values, fh, indent=2, ensure_ascii=False)
    except OSError:
        pass


settings = load_settings()

# The token lives in the environment, not in settings.json, so a container
# deployment never has to persist a secret to disk. Empty means auth is off.
TOKEN = os.environ.get("RECLIP_TOKEN", "").strip()

# ------------------------------------------------------------------------ state

jobs = {}
jobs_lock = threading.Lock()
download_queue = queue.Queue()
workers = {}
workers_lock = threading.Lock()


# ------------------------------------------------------------------- utilities

def parse_ytdlp_json(stdout):
    """Parse yt-dlp JSON output.

    With ``-j`` yt-dlp prints one JSON object per line. Some extractors
    emit multiple videos even with ``--no-playlist``, so stdout contains
    several objects and a plain ``json.loads`` raises "Extra data".
    Return the first valid object.
    """
    for line in stdout.splitlines():
        line = line.strip()
        if line:
            try:
                return json.loads(line)
            except ValueError:
                continue
    raise ValueError("yt-dlp returned no data")


def find_output(job_id, expected_ext):
    """Return the finished download for ``job_id``, or None.

    Prefer the exact ``<job_id><ext>`` file that yt-dlp produces after
    merging/extracting, and refuse to fall back to a per-format
    intermediate. Selecting an intermediate would hand the caller a video
    with no audio track, or an untranscoded .m4a in place of an .mp3.
    """
    exact = os.path.join(DOWNLOAD_DIR, f"{job_id}{expected_ext}")
    if os.path.exists(exact):
        return exact

    candidates = [
        f for f in glob.glob(os.path.join(DOWNLOAD_DIR, f"{job_id}.*"))
        if f.endswith(expected_ext)
        and not INTERMEDIATE_RE.search(os.path.basename(f))
    ]
    return candidates[0] if candidates else None


def job_files(job_id):
    return glob.glob(os.path.join(DOWNLOAD_DIR, f"{job_id}.*"))


def remove_job_files(job_id, keep=None):
    """Best-effort removal of a job's leftovers. Never raises."""
    try:
        remaining = job_files(job_id)
    except Exception:
        return
    for path in remaining:
        if keep and os.path.normcase(path) == os.path.normcase(keep):
            continue
        try:
            os.remove(path)
        except Exception:
            pass


def cleanup_async(job_id, keep=None):
    """Delete leftovers off the calling thread.

    Cleanup is cosmetic. It must never gate a job's outcome, and it must
    never be able to stall a worker: a delete can block for an unbounded time
    when antivirus, a search indexer or a policy layer holds the file, and
    the worker's slot is worth far more than the freed bytes. Running it on a
    throwaway daemon thread keeps both guarantees.
    """
    threading.Thread(
        target=remove_job_files, args=(job_id,), kwargs={"keep": keep}, daemon=True
    ).start()


def media_options():
    """Proxy / cookie flags shared by every yt-dlp invocation."""
    opts = []
    proxy = (settings.get("proxy") or "").strip()
    if proxy:
        opts += ["--proxy", proxy]
    cookies_file = (settings.get("cookies_file") or "").strip()
    cookies_browser = (settings.get("cookies_from_browser") or "").strip()
    if cookies_file:
        opts += ["--cookies", cookies_file]
    elif cookies_browser:
        opts += ["--cookies-from-browser", cookies_browser]
    return opts


def build_command(job, out_template):
    cmd = [
        "yt-dlp",
        "--no-playlist",
        "--newline",
        "--progress-template", PROGRESS_TEMPLATE,
        "-o", out_template,
    ]
    if job["format"] == "audio":
        cmd += ["-x", "--audio-format", "mp3"]
    elif job.get("format_id"):
        cmd += ["-f", f"{job['format_id']}+bestaudio/best",
                "--merge-output-format", "mp4"]
    else:
        cmd += ["-f", "bestvideo+bestaudio/best",
                "--merge-output-format", "mp4"]
    cmd += media_options()
    cmd.append(job["url"])
    return cmd


def update_progress(job, payload):
    """Turn one PROGRESS_TEMPLATE line into a dict on the job."""
    parts = payload.lstrip("|").split("|")
    if len(parts) < 5:
        return

    def num(text):
        try:
            return float(text)
        except (TypeError, ValueError):
            return None

    downloaded = num(parts[0])
    total = num(parts[1]) or num(parts[2])
    percent = None
    if downloaded is not None and total:
        percent = round(downloaded / total * 100, 1)
    job["progress"] = {
        "downloaded": downloaded,
        "total": total,
        "percent": percent,
        "speed": num(parts[3]),
        "eta": num(parts[4]),
    }


# ----------------------------------------------------------------- worker pool

def worker_loop():
    while True:
        job_id = download_queue.get()
        try:
            if job_id is None:          # sentinel: retire this worker
                return
            with jobs_lock:
                job = jobs.get(job_id)
                if job is None or job["status"] != "queued":
                    continue            # cancelled while it sat in the queue
                job["status"] = "downloading"
            run_download(job_id)
        except Exception as exc:        # a worker must never die
            with jobs_lock:
                job = jobs.get(job_id)
                if job is not None:
                    job["status"] = "error"
                    job["error"] = f"Internal error: {exc}"
        finally:
            download_queue.task_done()


def ensure_workers():
    """Grow or shrink the pool so it matches settings['concurrency']."""
    try:
        want = max(1, min(8, int(settings.get("concurrency", 3))))
    except (TypeError, ValueError):
        want = 3

    with workers_lock:
        for name in [n for n, t in workers.items() if not t.is_alive()]:
            workers.pop(name, None)
        current = len(workers)

        if current < want:
            for _ in range(want - current):
                name = f"reclip-worker-{uuid.uuid4().hex[:6]}"
                thread = threading.Thread(target=worker_loop, name=name, daemon=True)
                workers[name] = thread
                thread.start()
        elif current > want:
            for name in list(workers)[: current - want]:
                download_queue.put(None)
                workers.pop(name, None)

        return len(workers)


def reaper_loop():
    """Drop finished jobs and their files once they age past job_ttl."""
    while True:
        time.sleep(30)
        try:
            ttl = max(60, int(settings.get("job_ttl", 3600)))
        except (TypeError, ValueError):
            ttl = 3600
        cutoff = time.time() - ttl
        with jobs_lock:
            stale = [
                jid for jid, job in jobs.items()
                if job["status"] in ("done", "error", "cancelled")
                and job.get("created_at", 0) < cutoff
            ]
            for jid in stale:
                job = jobs.pop(jid, None)
                if job is not None:
                    cleanup_async(jid)


# ------------------------------------------------------------------ download

def run_download(job_id):
    job = jobs[job_id]
    out_template = os.path.join(DOWNLOAD_DIR, f"{job_id}.%(ext)s")
    cmd = build_command(job, out_template)

    try:
        timeout = max(30, int(settings.get("timeout", 300)))
    except (TypeError, ValueError):
        timeout = 300

    proc = None
    watchdog = None
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        job["proc"] = proc

        def on_timeout():
            job["timed_out"] = True
            try:
                proc.terminate()
            except OSError:
                pass

        watchdog = threading.Timer(timeout, on_timeout)
        watchdog.daemon = True
        watchdog.start()

        tail = collections.deque(maxlen=8)
        for raw in proc.stdout:
            line = raw.rstrip("\r\n")
            if line.startswith(PROGRESS_PREFIX):
                update_progress(job, line[len(PROGRESS_PREFIX):])
            elif line.strip():
                tail.append(ANSI_RE.sub("", line).strip())
        proc.wait()
        job["proc"] = None

        if job.get("cancel_requested"):
            job["status"] = "cancelled"
            job["error"] = None
            cleanup_async(job_id)
            return

        if job.get("timed_out"):
            job["status"] = "error"
            job["error"] = f"Download timed out ({timeout}s limit)"
            cleanup_async(job_id)
            return

        # Trust the file on disk over yt-dlp's exit code. yt-dlp exits
        # non-zero when it cannot delete its own intermediate files — on
        # Windows that is usually a transient lock held by antivirus or the
        # search indexer — even though the finished download is complete.
        # Treating that as a failure hides a perfectly good file from the UI.
        expected_ext = ".mp3" if job["format"] == "audio" else ".mp4"
        chosen = find_output(job_id, expected_ext)

        if chosen is None:
            job["status"] = "error"
            leftovers = job_files(job_id)
            if leftovers:
                job["error"] = (
                    "ffmpeg is required to merge or extract this download "
                    f"into {expected_ext}, but it was not found on PATH"
                )
            elif proc.returncode != 0 and tail:
                job["error"] = tail[-1]
            else:
                job["error"] = "Download completed but no file was found"
            cleanup_async(job_id)
            return

        # Publish the result FIRST. Deleting the intermediates is cosmetic, so
        # it happens afterwards and off this thread — see cleanup_async(). If it
        # ran here, a blocking delete would leave a finished download stranded
        # in 'downloading' forever and permanently consume a worker slot.
        job["status"] = "done"
        job["file"] = chosen
        job["progress"] = dict(job.get("progress") or {}, percent=100.0)
        ext = os.path.splitext(chosen)[1]
        title = (job.get("title") or "").strip()
        if title:
            safe = "".join(c for c in title if c not in r'\/:*?"<>|').strip()[:100].strip()
            job["filename"] = f"{safe}{ext}" if safe else os.path.basename(chosen)
        else:
            job["filename"] = os.path.basename(chosen)

        cleanup_async(job_id, keep=chosen)

    except Exception as exc:
        job["status"] = "error"
        job["error"] = str(exc)
    finally:
        if watchdog is not None:
            watchdog.cancel()
        job["proc"] = None


# ---------------------------------------------------------------------- auth

def auth_ok():
    if not TOKEN:
        return True
    supplied = ""
    header = request.headers.get("Authorization", "")
    if header.startswith("Bearer "):
        supplied = header[7:]
    if not supplied:
        supplied = request.args.get("token", "") or request.cookies.get("reclip_token", "")
    return hmac.compare_digest(supplied, TOKEN)


@app.before_request
def require_token():
    # The page itself stays public so a browser can load the UI and prompt for
    # the token; every data endpoint behind /api/ is gated.
    if request.path == "/" or request.path.startswith("/static/"):
        return None
    if request.path.startswith("/api/") and not auth_ok():
        return jsonify({"error": "Unauthorized: a valid token is required"}), 401
    return None


# -------------------------------------------------------------------- routes

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/settings", methods=["GET", "POST"])
def api_settings():
    if request.method == "GET":
        return jsonify({
            "concurrency": settings.get("concurrency"),
            "timeout": settings.get("timeout"),
            "proxy": settings.get("proxy"),
            "cookies_from_browser": settings.get("cookies_from_browser"),
            "cookies_file": settings.get("cookies_file"),
            "job_ttl": settings.get("job_ttl"),
            "auth_required": bool(TOKEN),
            "workers": len(workers),
            "queued": download_queue.qsize(),
        })

    data = request.json or {}
    with _settings_lock:
        for key in DEFAULT_SETTINGS:
            if key in data:
                settings[key] = data[key]
        try:
            settings["concurrency"] = max(1, min(8, int(settings["concurrency"])))
            settings["timeout"] = max(30, min(7200, int(settings["timeout"])))
            settings["job_ttl"] = max(60, min(604800, int(settings["job_ttl"])))
        except (TypeError, ValueError):
            settings.update(load_settings())
            return jsonify({"error": "Invalid numeric setting"}), 400
        for key in ("proxy", "cookies_from_browser", "cookies_file"):
            settings[key] = str(settings.get(key) or "").strip()
        save_settings(settings)

    return jsonify({"ok": True, "workers": ensure_workers()})


@app.route("/api/info", methods=["POST"])
def get_info():
    data = request.json or {}
    url = (data.get("url") or "").strip()
    if not url:
        return jsonify({"error": "No URL provided"}), 400

    cmd = ["yt-dlp", "--no-playlist", "-j"] + media_options() + [url]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=60)
        if result.returncode != 0:
            tail = [l for l in (result.stderr or "").strip().split("\n") if l.strip()]
            return jsonify({"error": ANSI_RE.sub("", tail[-1]) if tail else "yt-dlp failed"}), 400

        info = parse_ytdlp_json(result.stdout)

        # Build quality options — keep best format per resolution
        best_by_height = {}
        for f in info.get("formats", []):
            height = f.get("height")
            if height and f.get("vcodec", "none") != "none":
                tbr = f.get("tbr") or 0
                if height not in best_by_height or tbr > (best_by_height[height].get("tbr") or 0):
                    best_by_height[height] = f

        formats = [
            {"id": f["format_id"], "label": f"{height}p", "height": height}
            for height, f in best_by_height.items()
        ]
        formats.sort(key=lambda x: x["height"], reverse=True)

        return jsonify({
            "title": info.get("title", ""),
            "thumbnail": info.get("thumbnail", ""),
            "duration": info.get("duration"),
            "uploader": info.get("uploader", ""),
            "formats": formats,
        })
    except subprocess.TimeoutExpired:
        return jsonify({"error": "Timed out fetching video info"}), 400
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@app.route("/api/playlist", methods=["POST"])
def get_playlist_info():
    data = request.json or {}
    url = (data.get("url") or "").strip()
    if not url:
        return jsonify({"error": "No URL provided"}), 400

    cmd = ["yt-dlp", "--flat-playlist", "-J"] + media_options() + [url]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=60)
        if result.returncode != 0:
            tail = [l for l in (result.stderr or "").strip().split("\n") if l.strip()]
            return jsonify({"error": ANSI_RE.sub("", tail[-1]) if tail else "yt-dlp failed"}), 400

        info = json.loads(result.stdout)
        entries = info.get("entries", []) or []

        # Flat playlist entries do not always carry an absolute URL: for a
        # single video the ``url`` field is null and the real address lives in
        # ``webpage_url``. Prefer whichever is actually usable.
        urls = []
        for entry in entries:
            candidate = entry.get("webpage_url") or entry.get("url")
            if candidate:
                urls.append(candidate)

        truncated = False
        limit = 500
        if len(urls) > limit:
            urls = urls[:limit]
            truncated = True

        return jsonify({"urls": urls, "total": len(entries), "truncated": truncated})
    except subprocess.TimeoutExpired:
        return jsonify({"error": "Timed out fetching playlist info"}), 400
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@app.route("/api/download", methods=["POST"])
def start_download():
    data = request.json or {}
    url = (data.get("url") or "").strip()
    if not url:
        return jsonify({"error": "No URL provided"}), 400

    job_id = uuid.uuid4().hex[:10]
    with jobs_lock:
        jobs[job_id] = {
            "id": job_id,
            "status": "queued",
            "url": url,
            "title": data.get("title", "") or "",
            "format": data.get("format", "video") or "video",
            "format_id": data.get("format_id"),
            "progress": {},
            "created_at": time.time(),
        }

    download_queue.put(job_id)
    ensure_workers()
    return jsonify({"job_id": job_id, "queued": download_queue.qsize()})


@app.route("/api/status/<job_id>")
def check_status(job_id):
    job = jobs.get(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    return jsonify({
        "status": job["status"],
        "error": job.get("error"),
        "filename": job.get("filename"),
        "progress": job.get("progress") or {},
        "queued": download_queue.qsize(),
    })


@app.route("/api/cancel/<job_id>", methods=["POST"])
def cancel_job(job_id):
    job = jobs.get(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404

    if job["status"] in ("done", "error", "cancelled"):
        return jsonify({"status": job["status"]})

    if job["status"] == "queued":
        # The worker skips anything that is no longer 'queued'.
        job["status"] = "cancelled"
        return jsonify({"status": "cancelled"})

    job["cancel_requested"] = True
    proc = job.get("proc")
    if proc is not None and proc.poll() is None:
        try:
            proc.terminate()
        except OSError:
            pass
    return jsonify({"status": "cancelling"})


@app.route("/api/file/<job_id>")
def download_file(job_id):
    job = jobs.get(job_id)
    if not job or job["status"] != "done":
        return jsonify({"error": "File not ready"}), 404
    return send_file(job["file"], as_attachment=True, download_name=job["filename"])


# ------------------------------------------------------------------- startup

ensure_workers()
threading.Thread(target=reaper_loop, name="reclip-reaper", daemon=True).start()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8899))
    host = os.environ.get("HOST", "127.0.0.1")
    auth_state = "token required" if TOKEN else "open (no token set)"
    print(f"  ReClip listening on http://{host}:{port}  |  auth: {auth_state}  "
          f"|  workers: {len(workers)}")
    app.run(host=host, port=port)
