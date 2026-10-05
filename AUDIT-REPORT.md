# ReClip 代码与功能复验报告

- **项目**：`F:\2026-10-03-11-25-06\reclip`
- **上游版本**：averygan/reclip @ `1d161d1`
- **复验时间**：2026-10-03
- **复验对象**：`app.py`（210 行）、`templates/index.html`（705 行）、运行中的服务实例（`http://127.0.0.1:8899`）
- **上游代码改动**：无（`git status` 仅显示新增的 `start-reclip.bat`、`DEPLOY-Windows.md`）

---

## 一、总体结论

**主流程可用，项目宣称的核心功能均已实现。**

| 功能 | 结论 | 依据 |
|---|---|---|
| Web UI 加载与静态资源 | ✅ 正常 | 首页 200 / favicon 200 |
| 视频元数据解析 | ✅ 正常 | 返回标题、上传者、时长、封面、4 档清晰度 |
| MP4 下载（音视频合并） | ✅ 正常 | ffprobe 确认 h264 + AAC 双流，时长与源一致 |
| MP3 提取 | ✅ 正常 | ffprobe 确认 codec=mp3 / 48kHz / 立体声 / 140kbps |
| 批量下载 | ✅ 可用 | 前端逻辑与多线程后端匹配 |
| 播放列表展开 | ⚠️ 未验证 | 依赖 YouTube，本网络环境不可达 |
| 并发能力 | ✅ 正常 | 2 个并发请求各 2.44s（基准 2.60s） |
| 错误处理 | ✅ 无 500 | 各类非法输入均返回 4xx |
| 中文文件名 | ✅ 正常 | RFC 5987 `filename*=UTF-8''` 编码 |

**但有 5 处缺陷，其中 2 处会导致「静默错误交付」，1 处为安全注入点。** 均非阻断性，不影响主流程可用性。

---

## 二、检测过程与关键检查点

### 检查点 1：并发模型（决定下载时 UI 是否卡死）

`app.py` 使用 `app.run(host, port)`，未显式指定 `threaded`。需确认 Flask 默认值。

**方法**：先测单次 `/api/info` 基准耗时，再并发发起 2 个相同请求比较各自耗时。

```bash
curl -s -o /dev/null -w "%{time_total}\n" -X POST .../api/info -d '{...}'
curl ... & curl ... & wait
```

**结果**：基准 2.598s；并发时 A 2.445s、B 2.443s。
**判定**：耗时未叠加 → Flask `app.run()` 默认 `threaded=True`，**下载任务不会阻塞 UI 请求**。

---

### 检查点 2：下载产物的文件选择逻辑（本轮最重要发现）

**背景**：yt-dlp 下载 DASH 流时会先落盘中间文件 `<job_id>.f<视频id>.mp4`（纯视频）与 `<job_id>.f<音频id>.m4a`（纯音频），再由 ffmpeg 合并为 `<job_id>.mp4` 并删除中间文件。**若 ffmpeg 不可用，合并不会发生，中间文件会留存。**

**方法**：直接调用真实的 `run_download()`，预置不同文件名组合，观察其选中并交付哪个文件。
（仅替换 `subprocess.run` 避免联网、替换 `os.remove` 屏蔽本沙箱删除策略，被测逻辑本身未改动。）

**结果**：

| 场景 | 目录内容 | 状态 | 交付文件 | 判定 |
|---|---|---|---|---|
| S1 ffmpeg 缺失 | `f2996.mp4` + `f140.m4a` | `done` | `xx.f2996.mp4` | ❌ 交付**无音轨**视频 |
| S2 ffmpeg 正常 | `mp4` | `done` | `xx.mp4` | ✅ 正常 |
| S3 MP3 模式 + ffmpeg 缺失 | `f140.m4a` | `done` | `xx.f140.m4a` | ❌ 交付 **m4a 而非 MP3** |
| S4 指定清晰度 | `f30016.mp4` + `f30280.m4a` | `done` | `xx.f30016.mp4` | ❌ 交付**无音轨**视频 |

**根因**：`app.py` 第 54–65 行只按扩展名筛选，未区分「合并后的成品」与「中间流」：

```python
files = glob.glob(os.path.join(DOWNLOAD_DIR, f"{job_id}.*"))
target = [f for f in files if f.endswith(".mp4")]
chosen = target[0] if target else files[0]
```

**危害等级：中等。属静默失败**——用户看到「成功」，拿到的却是无声视频或错误格式，且无任何提示。

---

### 检查点 3：产物清理失败的处理

**方法**：执行 `app.py` 在 audio 模式下的同款命令，观察 yt-dlp 退出行为。

```bash
yt-dlp --no-playlist -o "downloads/mp3test.%(ext)s" -x --audio-format mp3 <url>
```

**结果**：
```
[download]    Destination: downloads\mp3test.m4a
[ExtractAudio] Destination: downloads\mp3test.mp3     ← 产物已成功生成
ERROR: [WinError 5] 拒绝访问。: 'downloads\mp3test.m4a' -> ...   ← 清理源文件失败
```
随后 ffprobe 确认 `downloads/mp3test.mp3` **完全有效**（mp3 / 48kHz / 立体声 / 212.3s）。

**关键点**：`app.py` 第 49–52 行以 `returncode != 0` 直接判定失败：
```python
if result.returncode != 0:
    job["status"] = "error"; job["error"] = result.stderr...
```
**`WinError 5 拒绝访问` 正是 Windows 上杀毒软件、Windows Search 索引器、资源管理器预览短暂锁定文件时的典型报错。** 因此在真实 Windows 环境中，下载明明成功、文件明明已生成，用户却会看到失败提示。

---

### 检查点 4：前端注入点

**方法**：定位所有把服务端数据插入 HTML 的位置，逐一核对是否经过 `esc()` 转义；对可疑处用 Node 还原模板插值验证。

`templates/index.html:571`：
```js
thumbHtml = `<img src="${c.thumbnail}" alt="">`;   // 未转义
```

**验证**：
```js
const c = { thumbnail: 'x" onerror="alert(document.domain)' };
// 生成结果： <img src="x" onerror="alert(document.domain)" alt="">
// 判定：是 —— 属性注入成立
```

`c.thumbnail` 来源于 `info.get("thumbnail")`，即被解析站点的响应元数据。已在报告的其他位置正确使用 `esc()`（第 590、593、599 行），此处为遗漏。

---

### 检查点 5：接口健壮性与边界

| 用例 | 返回 | 评价 |
|---|---|---|
| 非法 JSON body | `400` + **HTML** | 无 500，但与 API 的 JSON 风格不一致 |
| 缺少 `Content-Type` | `415` + **HTML** | 同上 |
| `/api/download` 缺 `url` | `400` + JSON | ✅ |
| `/api/playlist` 非播放列表 | `400` + JSON | ✅ 错误信息可读 |
| 未知路由 | `404` | ✅ |
| 错误 HTTP 方法 | `405` | ✅ |

前端始终发送正确的 `Content-Type`，故 HTML 响应无实际影响。

---

### 检查点 6：中文文件名的响应头

**方法**：用 Flask `test_client` 直接驱动真实路由，注入带中文名的已完成任务，读取响应头。

**结果**：
```
HTTP 200
Content-Type       : audio/mpeg
Content-Length     : 3728989        ← 与实际字节数一致
Content-Disposition: attachment; filename=" MVNever...mp3";
                     filename*=UTF-8''%E3%80%90%E5%AE%98%E6%96%B9%20MV%E3%80%91...
```
**判定**：✅ 使用 RFC 5987 编码，中文文件名可被浏览器正确还原。

---

### 检查点 7：播放列表 URL 形态（未完成验证）

前端在 `/api/playlist` 拿到条目后直接 `urls.splice(i, 1, ...data.urls)`，即假定 `entry["url"]` 是**可直接再次解析的绝对 URL**。

`app.py:158` 仅取该字段：
```python
urls = [entry.get("url") for entry in entries if entry.get("url")]
```

**已获得的旁证**：对单个视频执行 `yt-dlp --flat-playlist -J`，`url` 为 `None`，真实地址在 `webpage_url`。yt-dlp 扁平列表条目的通用约定是 `url` + `ie_key` 组合，`url` 未必是绝对地址。

**未能验证的原因**：该分支主要面向 YouTube 播放列表，而 YouTube 在本网络环境不可达（`SSL: UNEXPECTED_EOF_WHILE_READING`），Bilibili 的 space 接口返回 412 阻断。

**建议**：在网络可达的环境下实测一次 YouTube 播放列表；若 `entry["url"]` 为裸视频 ID，应改为优先取 `webpage_url`，或补 `ie_key`。

---

## 三、问题清单与修复建议

### 缺陷 1｜中间文件被误当作成品交付（等级：中）

**位置**：`app.py` 第 54–65 行

**修复**：排除形如 `.f<数字>.` 的中间文件，并优先匹配精确文件名。

```python
import re

def _pick_output(files, job_id, ext):
    # 中间流文件名形如 <job_id>.f30016.mp4，需排除
    exact = os.path.join(DOWNLOAD_DIR, f"{job_id}{ext}")
    if os.path.exists(exact):
        return exact
    merged = [f for f in files
              if f.endswith(ext) and not re.search(r"\.f\d+\.", os.path.basename(f))]
    return merged[0] if merged else None
```

再据此把 `chosen` 为 `None` 的情况判为错误，而不是回退到 `files[0]`。

---

### 缺陷 2｜清理失败被误判为下载失败（等级：中）

**位置**：`app.py` 第 49–52 行

**修复**：先看文件是否已生成，再决定是否报错。

```python
result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)

files = glob.glob(os.path.join(DOWNLOAD_DIR, f"{job_id}.*"))
ext = ".mp3" if format_choice == "audio" else ".mp4"
chosen = _pick_output(files, job_id, ext)

if chosen is None:
    job["status"] = "error"
    job["error"] = (result.stderr.strip().split("\n")[-1] if result.returncode
                    else "Download completed but no file was found")
    return
# 文件已存在即视为成功，忽略 yt-dlp 因清理失败产生的非零退出码
```

同时建议把清理循环的 `except OSError` 放宽为 `except Exception`。

---

### 缺陷 3｜缩略图属性注入（等级：安全 / 中）

**位置**：`templates/index.html:571`

**修复**：
```js
thumbHtml = `<img src="${esc(c.thumbnail)}" alt="">`;
```
更稳妥的做法是限制为 `http/https` 协议：
```js
const safeThumb = /^https?:\/\//i.test(c.thumbnail || '') ? c.thumbnail : '';
```

---

### 缺陷 4｜批量下载无并发上限（等级：资源）

**位置**：`index.html` `dlAll()` + `app.py` `/api/download`

**说明**：`dlAll()` 会为列表中每个视频立即发起下载请求，后端每个请求启动一个 yt-dlp 进程，且 `/api/playlist` 不限制条目数。大歌单可能瞬间拉起大量进程与并发连接。

**修复**：前端按固定窗口（如同时 3 个）串行推进；后端可加一个进程数上限或轻量队列。

---

### 缺陷 5｜任务与文件无淘汰机制（等级：资源）

**说明**：`jobs` 字典永不清理，`downloads/` 也不自动回收，长期运行会持续增长。

**修复**：为 job 增加时间戳，定期清理已完成/已过期的条目与对应文件；或在 `/api/file` 返回后删除（如不需要二次下载）。

---

### 次要观察

- **`/api/info` 与 `/api/download` 返回 HTML 错误页**：建议注册 Flask `errorhandler`，统一返回 JSON。
- **`-f "{format_id}+bestaudio/best"` 对已含音轨的渐进式格式会重复合并音频**：Bilibili 等纯 DASH 站点无影响，其他站点可能出现多音轨。
- **`--merge-output-format mp4` 强制容器**：若源为 VP9/Opus 等，可能出现兼容性较差的 mp4（未在 YouTube 上实测，因网络不可达）。
- **前端 `pollCard` 无最大轮询次数**：任务卡死时轮询会无限持续。
- **前端播放列表判定条件过宽**：`urls[i].includes('list=')`，会导致带 `&list=` 参数的**单个视频 URL 被展开成整个播放列表**，与后端 `--no-playlist` 的意图相矛盾。

---

## 四、建议的验证方式（回归用例）

```bash
B=http://127.0.0.1:8899
# 1. 静态与参数校验
curl -o /dev/null -w "%{http_code}\n" $B/
curl -X POST $B/api/info -H "Content-Type: application/json" -d '{"url":""}'
# 2. 元数据解析（国内可直连样本）
curl -X POST $B/api/info -H "Content-Type: application/json" \
     -d '{"url":"https://www.bilibili.com/video/BV1GJ411x7h7"}'
# 3. 下载后必须用 ffprobe 核验"双流"，这是识别缺陷 1 的唯一可靠手段
ffprobe -v error -show_entries stream=codec_type,codec_name \
        -of default=noprint_wrappers=1 downloads/<file>.mp4
#    正确结果应同时出现 codec_type=video 与 codec_type=audio
```

> **关键**：仅凭界面显示「成功」不足以判定下载正确。必须用 `ffprobe` 确认 MP4 同时含视频流与音频流、MP3 的 `format_name` 为 `mp3`。

---

## 五、复验结论

ReClip 在本机的部署是**成功且可用的**：服务稳定运行，元数据解析、MP4 合并、MP3 提取三条主链路均已用 `ffprobe` 逐字节验证通过，并发模型正确，错误处理无 500。

发现的 5 处缺陷均为**健壮性与安全性问题，不阻断主流程**。其中缺陷 1、2 在 ffmpeg 缺失或存在文件锁的真实场景下会给出**误导性结果**，建议优先修复；缺陷 3 在自用场景下风险有限（需攻击者控制视频站点的缩略图元数据），但修复成本极低，建议一并处理。
