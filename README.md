# ReClip

A self-hosted, open-source video and audio downloader with a clean web UI. Paste links from YouTube, TikTok, Instagram, Twitter/X, and 1000+ other sites — download as MP4 or MP3.

![Python](https://img.shields.io/badge/python-3.8+-blue)
![License](https://img.shields.io/badge/license-MIT-green)

https://github.com/user-attachments/assets/419d3e50-c933-444b-8cab-a9724986ba05

![ReClip MP3 Mode](assets/preview-mp3.png)

## Features

- Download videos from 1000+ supported sites (via [yt-dlp](https://github.com/yt-dlp/yt-dlp))
- MP4 video or MP3 audio extraction
- Quality/resolution picker
- Bulk downloads — paste multiple URLs at once
- Automatic URL deduplication
- Clean, responsive UI — no frameworks, no build step
- Single Python file backend

### Added in this build

- **Bounded download queue** — a worker pool never runs more than
  `concurrency` yt-dlp processes at once, so "Download All" on a 500-item
  playlist no longer spawns 500 processes
- **Live progress** — percentage, transferred size, speed and ETA parsed
  straight from yt-dlp
- **Cancellable jobs** — stop a queued or running download from the UI
- **Optional token auth** — set `RECLIP_TOKEN` and every `/api/*` route
  requires it; the page and static assets stay public
- **Runtime settings** — proxy, cookie source, concurrency, timeout and job
  TTL are editable from the UI and persisted to `settings.json`
- **Automatic reaping** — finished jobs and their files are dropped after
  `job_ttl` seconds
- **Correct output selection** — the finished file is identified by its exact
  name, so per-format intermediates (`.f30016.mp4`, `.f30280.m4a`) are never
  delivered as if they were the merged result
- **Exit-code tolerant** — a cleanup failure inside yt-dlp (typically a file
  locked by antivirus on Windows) no longer reports a successful download as
  an error

## Quick Start

```bash
brew install yt-dlp ffmpeg    # or apt install ffmpeg && pip install yt-dlp
git clone https://github.com/averygan/reclip.git
cd reclip
./reclip.sh
```

Open **http://localhost:8899**.

Or with Docker:

```bash
docker build -t reclip . && docker run -p 8899:8899 reclip
```

## Configuration

Settings are editable from the **⚙ Settings** panel in the UI and stored in
`settings.json` next to `app.py`. All of them can also be applied per run.

| Setting | Default | Meaning |
|---|---|---|
| `concurrency` | `3` | Maximum simultaneous yt-dlp processes (1–8) |
| `timeout` | `300` | Per-job wall-clock limit in seconds |
| `proxy` | *(empty)* | e.g. `http://127.0.0.1:7890` — required for YouTube in some regions |
| `cookies_from_browser` | *(empty)* | `chrome` / `edge` / `firefox` — unlocks member-only and private videos |
| `cookies_file` | *(empty)* | Path to a Netscape-format `cookies.txt`; takes precedence over the browser option |
| `job_ttl` | `3600` | Seconds a finished job and its file are kept before being reaped |

Environment variables:

| Variable | Default | Meaning |
|---|---|---|
| `PORT` | `8899` | Listening port |
| `HOST` | `127.0.0.1` | Listening address. Use `0.0.0.0` for LAN access |
| `RECLIP_TOKEN` | *(unset)* | When set, every `/api/*` request must carry it — as `Authorization: Bearer <token>` or `?token=<token>`. **Always set this if you expose the app beyond localhost.** |

## Usage

1. Paste one or more video URLs into the input box
2. Choose **MP4** (video) or **MP3** (audio)
3. Click **Fetch** to load video info and thumbnails
4. Select quality/resolution if available
5. Click **Download** on individual videos, or **Download All**

Downloads are queued server-side and run `concurrency` at a time. Each card
shows live progress and can be cancelled while it is running or still queued.
When a job finishes the browser is handed the file automatically; the **Save**
button re-triggers it if the browser blocked the automatic download.

## Supported Sites

Anything [yt-dlp supports](https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md), including:

YouTube, TikTok, Instagram, Twitter/X, Reddit, Facebook, Vimeo, Twitch, Dailymotion, SoundCloud, Loom, Streamable, Pinterest, Tumblr, Threads, LinkedIn, and many more.

## Stack

- **Backend:** Python + Flask (~150 lines)
- **Frontend:** Vanilla HTML/CSS/JS (single file, no build step)
- **Download engine:** [yt-dlp](https://github.com/yt-dlp/yt-dlp) + [ffmpeg](https://ffmpeg.org/)
- **Dependencies:** 2 (Flask, yt-dlp)

## Disclaimer

This tool is intended for personal use only. Please respect copyright laws and the terms of service of the platforms you download from. The developers are not responsible for any misuse of this tool.

## License

[MIT](LICENSE)
