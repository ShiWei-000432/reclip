# ReClip「保存视频」流程指南

> 项目路径：`F:\2026-10-03-11-25-06\reclip`
> 服务地址：http://127.0.0.1:8899
> 适用：Windows 10 / 11

---

## 一、先理解数据流：有两次"保存"

ReClip 的保存是**两段式**的，混淆这两段是绝大多数困惑的根源：

```
① 服务端暂存                          ② 浏览器落盘
─────────────────────────            ─────────────────────────
用户点 Download                       任务 done 后前端自动触发
   ↓                                     ↓
POST /api/download                    浏览器请求 GET /api/file/<job_id>
   ↓                                     ↓
后台线程执行 yt-dlp                   服务端以 attachment 响应返回文件
   ↓                                     ↓
写入 reclip\downloads\<job_id>.mp4    文件落到【浏览器的下载目录】
```

**关键结论：**
- `reclip\downloads\` 只是**中转站**，不是你最终看到文件的地方。
- 最终文件位置由**浏览器设置**决定，默认是 `C:\Users\<你>\Downloads`。
- 因此"我下载完了但找不到文件"，先看浏览器下载目录，而不是项目目录。

---

## 二、前置步骤（首次部署时做一次）

| 步骤 | 命令 | 说明 |
|---|---|---|
| 1. 确认 Python | `python --version` | 需 ≥ 3.8 |
| 2. 创建虚拟环境 | `python -m venv venv` | 在项目根目录执行 |
| 3. 安装依赖 | `venv\Scripts\python.exe -m pip install -r requirements.txt` | 装上 Flask 与 yt-dlp.exe |
| 4. 安装 ffmpeg | `winget install --id Gyan.FFmpeg -e --accept-source-agreements --accept-package-agreements` | **必需**，否则 MP4 无声音、MP3 失败 |

**ffmpeg 安装后必须做一次可执行性验证：**
```cmd
ffmpeg -version
```
必须真实打印版本号。**`where ffmpeg` 能找到路径不代表可用**——winget 的 `Links\ffmpeg.exe` 是 0 字节 shim，实际不可执行。`start-reclip.bat` 已用 `ffmpeg -version` 做探测并自动注入真实路径，绕过了这个坑。

---

## 三、启动命令

### 方式 1：一键启动（推荐）

```cmd
cd /d F:\2026-10-03-11-25-06\reclip
start-reclip.bat
```

脚本会自动：注入 `venv\Scripts`（让子进程找得到 `yt-dlp.exe`）→ 探测并注入真实 ffmpeg 路径 → 启动 Flask。

看到下面输出即成功：
```
[INFO] ffmpeg added to PATH from: ...\ffmpeg-9.0.2-full_build\bin
  ReClip is running at http://localhost:8899
```

### 方式 2：手动启动（便于理解原理）

```cmd
cd /d F:\2026-10-03-11-25-06\reclip
set "PATH=%CD%\venv\Scripts;%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-9.0.2-full_build\bin;%PATH%"
venv\Scripts\python.exe app.py
```

### 方式 3：后台常驻

```cmd
start /min cmd /c start-reclip.bat
```

### 不要用 `reclip.sh`

它依赖 `python3` / `brew` / `apt` 等 POSIX 约定，**Windows 下必然失败**。

---

## 四、保存视频的完整操作流程

### 在浏览器里操作

1. 打开 http://localhost:8899
2. 粘贴视频链接（支持多行批量，用换行或逗号分隔）
3. 选择 **MP4**（视频）或 **MP3**（音频）
4. 点 **Fetch** → 卡片显示缩略图、标题、时长、清晰度选项
5. 选清晰度（可选，默认最高）
6. 点 **Download**（单个）或 **Download All**（批量）

### 背后实际发生了什么

| 界面动作 | 接口调用 | 说明 |
|---|---|---|
| Fetch | `POST /api/info` | yt-dlp 解析元数据，返回各档清晰度 |
| （含 `list=` 的链接） | `POST /api/playlist` | 展开播放列表为多条 |
| Download | `POST /api/download` | 创建任务，返回 `job_id`，后台线程开跑 |
| 轮询 | `GET /api/status/<job_id>` | 前端每秒查一次，直到 `done` / `error` |
| 保存 | `GET /api/file/<job_id>` | 服务端以附件形式返回，浏览器保存 |

### 保存被跳过了怎么办

`done` 状态下卡片右侧有 **Save** 按钮，可手动重新触发下载。若浏览器拦截了自动保存，点它即可。

---

## 五、关键配置项

| 配置 | 设置方式 | 默认 | 说明 |
|---|---|---|---|
| 端口 | `set PORT=9000` 再启动 | `8899` | |
| 监听地址 | `set HOST=0.0.0.0` 再启动 | `127.0.0.1` | 改为 `0.0.0.0` 可局域网访问，**但无任何认证，勿暴露公网** |
| 代理 | `set HTTPS_PROXY=http://127.0.0.1:7890` | 无 | 访问 YouTube 等必需 |
| 跳过 yt-dlp 自更新 | `set RECLIP_NO_UPDATE=1` | 未设置 | 仅影响 `reclip.sh`，Windows 启动脚本不含自更新 |
| 下载超时 | 硬编码在 `app.py` 第 87 行 | 300 秒 | 大文件需改代码 |
| 暂存目录 | 硬编码 `downloads\` | — | 自动创建 |

---

## 六、常见注意事项

### 1. 找不到下载好的文件

文件在**浏览器的下载目录**，不在 `reclip\downloads\`。后者是服务端中转目录，可在确认浏览器已保存后手动清空。

### 2. 浏览器拦截批量自动保存

点 **Download All** 时浏览器可能弹出"是否允许下载多个文件"。需选择允许，否则只有第一个会落盘。

### 3. MP4 只有画面没有声音

**这是 ffmpeg 未生效的典型症状**，且旧版代码不会报错（静默失败）。先用 `ffmpeg -version` 确认可用；本项目提供的 `fix-download-output.patch` 已修复该静默失败问题（见第八节）。

### 4. MP3 模式交付了 .m4a

同样源于 ffmpeg 缺失或失败。打补丁后，此情况会明确报错而不是交付错误格式。

### 5. 界面显示"下载失败"但文件其实完好

**y t-dlp 的退出码不可完全信任。** 它在产物已生成后清理临时文件失败（Windows 的 `WinError 5 拒绝访问`，常由杀毒软件、Windows Search 索引器、资源管理器预览短暂锁定文件触发）时会返回非零。旧版代码据此判定失败，导致完好的文件无法通过 `/api/file` 取回。此问题已由补丁修复。

### 6. 任务列表在重启后消失

`jobs` 是进程内内存字典，**服务重启后所有任务记录丢失**（已下载的文件仍在磁盘上，但无法再通过界面取回）。这是当前架构的固有限制。

### 7. 大文件超时

单任务硬编码 300 秒上限，超出即报 `Download timed out (5 min limit)`。

### 8. 某些站点突然无法解析

网站改版会让 yt-dlp 的 extractor 失效，更新即可：
```cmd
venv\Scripts\python.exe -m pip install -U yt-dlp
```

### 9. YouTube 无法访问

中国大陆网络环境下报 `SSL: UNEXPECTED_EOF_WHILE_READING`，属网络限制，需配置代理。

### 10. 暂停与恢复

在运行窗口按 `Ctrl+C` 停止。强制停止：
```cmd
taskkill /F /IM python.exe
```
（会终止所有 Python 进程，请谨慎）

---

## 七、验证保存流程是否正常

```bash
B=http://127.0.0.1:8899

# 1. 服务可达
curl -o /dev/null -w "%{http_code}\n" $B/

# 2. 元数据解析（国内可直连样本）
curl -X POST $B/api/info -H "Content-Type: application/json" \
     -d '{"url":"https://www.bilibili.com/video/BV1GJ411x7h7"}'

# 3. 触发下载，拿到 job_id
curl -X POST $B/api/download -H "Content-Type: application/json" \
     -d '{"url":"https://www.bilibili.com/video/BV1GJ411x7h7","format":"video","format_id":"30016","title":"demo"}'

# 4. 轮询状态直到 done
curl $B/api/status/<job_id>

# 5. 取回文件
curl -OJ $B/api/file/<job_id>
```

**最关键的一步——用 ffprobe 验证产物本身，而不是只看界面提示：**

```cmd
ffprobe -v error -show_entries stream=codec_type,codec_name -of default=noprint_wrappers=1 downloads\<文件>
```

正确结果必须**同时出现** `codec_type=video` 与 `codec_type=audio`。只有 video 说明 ffmpeg 未参与合并。

---

## 八、待应用的修复补丁

当前克隆的工作树文件在本环境为只读，无法原地修改，修复已整理为补丁：

**`fix-download-output.patch`**（已通过 `git apply --check` 校验）

在**可写的克隆**中执行：
```bash
cd your-reclip
git apply fix-download-output.patch
git diff --stat          # 应显示 app.py | 40 +- 左右
```

补丁修复两个问题：

1. **产物误选**：新增 `find_output()`，优先匹配精确文件名并排除 `.f<id>.<ext>` 中间流，避免把无音轨的纯视频或未转码的 m4a 当作成品交付。
2. **失败误判**：改为"以磁盘上的文件为准"，产物已生成即视为成功；清理临时文件的失败不再让整个任务失败。当只存在中间流时，给出明确的 ffmpeg 缺失提示。

前端另有一处属性注入点需一并处理（`templates/index.html:571`），详见 `AUDIT-REPORT.md` 与 `FEATURE-ROADMAP.md`：
```js
// 注意：直接套用 esc() 并不足够
// esc() 基于 textContent/innerHTML 序列化，按 HTML 规范只转义 & < >，不转义引号，
// 因此在 src="..." 这类属性上下文中仍可被突破。需使用转义引号的专用函数：
function escAttr(s) {
  return String(s ?? '').replace(/[&<>"']/g, c =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}
// 然后： thumbHtml = `<img src="${escAttr(c.thumbnail)}" alt="">`;
```
