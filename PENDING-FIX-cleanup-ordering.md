# 待修复：清理操作不应决定任务结果

**优先级**：中（正常机器上不触发，但属于真实的健壮性缺陷）
**位置**：`app.py` `run_download()`，第 387–390 行

---

## 现象

任务在**产物已完整生成**的情况下，永远停留在 `downloading`：

```
downloads/c3a95f522a.f30016.mp4   9,292,223 字节   (视频流)
downloads/c3a95f522a.f30280.m4a   5,408,198 字节   (音频流)
downloads/c3a95f522a.mp4         14,767,133 字节   (合并成品，ffprobe 校验通过)
```

此时 `/api/status/<job_id>` 恒为 `downloading`，`/api/file/<job_id>` 返回 404，用户拿不到那个完好的成品。

排查结论：yt-dlp 与 ffmpeg 进程**均已退出**，只剩 Flask 进程存活 → 卡点在 `app.py` 自身的清理调用上。

---

## 根因

`run_download()` 把「删除中间文件」放在了「写入任务结果」**之前**：

```python
# 第 387 行
remove_job_files(job_id, keep=chosen)     # ← 卡在这里

# 第 389–390 行
job["status"] = "done"
job["file"] = chosen
```

`remove_job_files` 内部是 `try: os.remove(path) except Exception: pass`。异常能被捕获，但**调用被挂起（hang）时捕获无效**——线程永远停在 `os.remove` 上，后面的状态写入语句根本执行不到。

## 触发条件

| 环境 | 行为 |
|---|---|
| 正常 Windows / macOS / Linux | `os.remove` 立即返回（成功，或被占用时抛 `PermissionError`）→ **无影响** |
| 文件删除被中途拦停并挂起 | 工作线程被永久占用 → 任务永久停在 `downloading` |

本 Agent 沙箱的 `safe-delete` 策略正是第二种情况（它会挂起删除调用而非抛异常）。因此该缺陷在本环境 100% 复现，在普通机器上不会被触发。

**但设计上这是错的**：清理是收尾动作，无论成功、失败还是被阻塞，都不应该影响「下载是否成功」这个已经确定的事实，更不应该占住一个宝贵的工作线程（并发上限为 3，卡死 3 次服务即不可用）。

---

## 修复方案

两步：先把结果落定，再把清理挪到独立线程。

**1. 新增一个 fire-and-forget 清理函数**（放在 `remove_job_files` 之后）

```python
def cleanup_async(job_id, keep=None):
    """Delete leftovers off the worker thread.

    Cleanup is cosmetic. It must never gate a job's outcome, and it must
    never be able to stall a worker: a delete can block for a long time when
    antivirus, a search indexer or the filesystem itself holds the file, and
    the worker's slot is far more valuable than the freed bytes.
    """
    threading.Thread(
        target=remove_job_files, args=(job_id,), kwargs={"keep": keep}, daemon=True
    ).start()
```

**2. 调整 `run_download()` 的顺序**（第 387 行起）

```python
        # ---- 原先 ----
        remove_job_files(job_id, keep=chosen)

        job["status"] = "done"
        job["file"] = chosen
        job["progress"] = dict(job.get("progress") or {}, percent=100.0)

        # ---- 改为 ----
        job["status"] = "done"
        job["file"] = chosen
        job["progress"] = dict(job.get("progress") or {}, percent=100.0)
        # ...（filename 计算保持不变）...

        # 清理放在最后，且不占用工作线程
        cleanup_async(job_id, keep=chosen)
```

同理，取消（第 353 行）与超时（第 359 行）分支里的 `remove_job_files(job_id)` 也应改为 `cleanup_async(job_id)`——这两个分支同样存在「卡在清理、状态写不进去」的风险（`cancel_requested` 的判断在第 349 行，先于清理，所以取消本身能生效；但线程仍会被占用）。reaper（第 300 行）运行在独立的守护线程中，可以不动。

---

## 验收方法

```bash
B=http://127.0.0.1:8899
JOB=$(curl -s -X POST $B/api/download -H "Content-Type: application/json" \
  -d '{"url":"https://www.bilibili.com/video/BV1GJ411x7h7","format":"video","format_id":"30016","title":"验收"}' \
  | sed -E 's/.*"job_id":"([^"]+)".*/\1/')

# 必须在合并完成后立即变为 done，且 /api/file 可下载
curl -s $B/api/status/$JOB
curl -s -o out.mp4 -w "%{http_code} %{size_download}\n" $B/api/file/$JOB
ffprobe -v error -show_entries stream=codec_type -of default=noprint_wrappers=1 out.mp4
# 期望：codec_type=video 与 codec_type=audio 同时出现
```

修复后，即使删除被挂起，任务也会立刻返回 `done`，`/api/file` 可正常下载；被挂起的清理线程是守护线程，进程退出时随之一并结束。
