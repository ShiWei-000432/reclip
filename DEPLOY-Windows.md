# ReClip 本地部署手册（Windows）

> 仓库：https://github.com/averygan/reclip
> 适用平台：Windows 10 / 11（x64）
> 本文档基于实际部署验证结果编写

---

## 一、项目概览与技术栈

ReClip 是一个自托管的视频/音频下载器，提供 Web 界面，通过 yt-dlp 支持 1000+ 站点。

| 层次 | 技术 | 说明 |
|---|---|---|
| 后端 | Python + Flask | 单文件 `app.py`，约 210 行 |
| 前端 | 原生 HTML/CSS/JS | `templates/index.html` 单文件，**无构建步骤** |
| 下载引擎 | yt-dlp | 外部命令行程序，负责解析与下载 |
| 媒体处理 | ffmpeg / ffprobe | 外部命令行程序，负责音视频合并与 MP3 提取 |
| 持久化 | 本地文件系统 | 下载产物存于 `downloads/`，任务状态存于内存字典 |

**关键架构特征（决定了部署要点）：**

1. `app.py` 通过 `subprocess.run(["yt-dlp", ...])` 直接调用**裸命令名**，因此 `yt-dlp` 与 `ffmpeg` 必须在**进程的 PATH 中可执行**，而不是仅"存在于磁盘上"。
2. 任务状态保存在进程内存的 `jobs = {}` 字典中，**服务重启后所有任务记录丢失**。
3. 下载在 `threading.Thread(daemon=True)` 中执行，单机单进程适用；`app.run()` 为 Flask 开发服务器，不适合公网生产部署。

---

## 二、运行环境与依赖

### 2.1 必需组件

| 组件 | 版本要求 | 本项目实测版本 | 用途 |
|---|---|---|---|
| Python | ≥ 3.8 | 3.13.12 | 运行 Flask 后端 |
| pip | 随 Python | 26.x | 安装依赖 |
| Flask | 无硬性版本 | 3.1.3 | Web 框架 |
| yt-dlp | 建议最新版 | 2026.08.19 | 下载引擎 |
| ffmpeg | 建议最新版 | 9.0.2 full_build | 合并音视频 / 提取 MP3 |

> **注意**：`ffprobe` 会随 ffmpeg 一同安装，无需单独处理。
> `requirements.txt` 中只声明了 `flask` 与 `yt-dlp` 两个 Python 依赖；**ffmpeg 是系统级外部程序，不受 pip 管理**。

### 2.2 网络要求

- 可访问目标视频站点（YouTube 在中国大陆需代理，Bilibili 等国内站点直连可用）。
- 可访问 Python 包索引（安装依赖时）。

---

## 三、完整部署步骤

### 步骤 1：获取源码

```bash
git clone https://github.com/averygan/reclip.git
cd reclip
```

### 步骤 2：创建虚拟环境并安装 Python 依赖

```bash
python -m venv venv
venv\Scripts\python.exe -m pip install --upgrade pip
venv\Scripts\python.exe -m pip install -r requirements.txt
```

安装完成后 `venv\Scripts\` 下应出现 `yt-dlp.exe`，这是后端调用的下载引擎。

**验证：**

```bash
venv\Scripts\python.exe -m pip list | findstr /i "flask yt-dlp"
venv\Scripts\yt-dlp.exe --version
```

### 步骤 3：安装 ffmpeg

任选其一：

```powershell
# 方式 A：winget（推荐）
winget install --id Gyan.FFmpeg -e --accept-source-agreements --accept-package-agreements
```

```powershell
# 方式 B：手动下载静态构建
# 从 https://www.gyan.dev/ffmpeg/builds/ 下载 ffmpeg-release-essentials.zip
# 解压后把其中的 bin 目录加入系统 PATH（例如 C:\ffmpeg\bin）
```

**验证（重要）：**

```cmd
ffmpeg -version
```

必须能打印出 `ffmpeg version ...` 才算成功。
**仅凭 `where ffmpeg` 能返回路径不足以确认可用**——详见「常见问题 Q1」。

### 步骤 4：启动服务

原仓库的 `reclip.sh` 依赖 `python3`、`brew`/`apt`，**在 Windows 上无法直接运行**。本项目已提供 Windows 启动脚本 `start-reclip.bat`，它会自动完成 PATH 注入：

```cmd
start-reclip.bat
```

等价的**手动启动方式**（便于理解原理）：

```cmd
set "PATH=%~dp0venv\Scripts;%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-9.0.2-full_build\bin;%PATH%"
venv\Scripts\python.exe app.py
```

### 步骤 5：自定义配置（可选）

`app.py` 支持两个环境变量：

| 变量 | 默认值 | 说明 |
|---|---|---|
| `PORT` | `8899` | 监听端口 |
| `HOST` | `127.0.0.1` | 监听地址。改为 `0.0.0.0` 可供局域网访问 |

```cmd
set PORT=9000
set HOST=0.0.0.0
start-reclip.bat
```

> 修改 `HOST` 后请确认 Windows 防火墙已放行对应端口。

---

## 四、验证项目正常运行

按以下顺序逐层验证，可快速定位故障环节。

### 4.1 进程与端口

```cmd
netstat -ano | findstr :8899
```
应看到 `TCP 127.0.0.1:8899 ... LISTENING`。

### 4.2 HTTP 层

```bash
curl -o nul -w "%{http_code}\n" http://127.0.0.1:8899/
```
预期 `200`。

### 4.3 接口层

| 请求 | 预期结果 |
|---|---|
| `POST /api/info` body `{"url":""}` | `400 {"error":"No URL provided"}` |
| `GET /api/status/xxx` | `404 {"error":"Job not found"}` |
| `GET /static/favicon.svg` | `200 image/svg+xml` |

### 4.4 端到端业务验证

在浏览器打开 `http://localhost:8899`，粘贴一个可直连站点的链接（如 Bilibili），点击 **Fetch**。成功标志：

- 显示出视频标题、封面缩略图、时长、上传者；
- 出现清晰度下拉框（如 1080p / 720p / 480p / 360p）。

随后点击 **Download**，任务进入 `downloading`，结束后可下载文件。

**命令行验证接口（等价方式）：**

```bash
curl -X POST http://127.0.0.1:8899/api/info ^
  -H "Content-Type: application/json" ^
  -d "{\"url\":\"https://www.bilibili.com/video/BV1GJ411x7h7\"}"
```

**判定下载产物是否正常的核心标准：**

- 选择 MP4 时，产物必须**同时包含画面与声音**。若只有画面无声，说明 ffmpeg 未生效（见 Q1）。
- 选择 MP3 时，应在 `downloads/` 下得到 `.mp3` 文件。

---

## 五、常见问题与解决方法

### Q1（最高频）`WARNING: You have requested merging of multiple formats but ffmpeg is not installed`

**现象**：下载能完成，但 MP4 只有画面没有声音；或 MP3 模式直接失败。

**根因**：yt-dlp 找不到可执行的 ffmpeg。即使 `where ffmpeg` 能找到路径也仍然会失败——winget 生成的 `%LOCALAPPDATA%\Microsoft\WinGet\Links\ffmpeg.exe` 是 **0 字节的 reparse point shim，实际不可执行**（实测直接调用退出码 255，无任何输出）。

**解决**：把 ffmpeg 的**真实包内 bin 目录**加入 PATH，而非 Links 目录：

```cmd
%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-9.0.2-full_build\bin
```

`start-reclip.bat` 已内置该逻辑：它会用 `ffmpeg -version` 做**可执行性探测**，探测失败才注入真实路径，因此可自动适配 ffmpeg 升级后的版本号变化。

**确认方法**：执行 `ffmpeg -version` 必须真实打印版本号。

---

### Q2 YouTube 报 `SSL: UNEXPECTED_EOF_WHILE_READING`

**现象**：
```
ERROR: [youtube] xxx: Unable to download API page: [SSL: UNEXPECTED_EOF_WHILE_READING]
```

**根因**：中国大陆网络无法直连 YouTube，TLS 握手被中断。**这是网络环境问题，不是部署问题**。

**解决**：
```cmd
set HTTPS_PROXY=http://127.0.0.1:7890
set HTTP_PROXY=http://127.0.0.1:7890
start-reclip.bat
```
或在 yt-dlp 配置文件中设置代理。Bilibili、抖音等国内站点无需代理。

---

### Q3 任务长期停留在 `downloading` 且不结束

**可能原因与对策：**

| 原因 | 排查方法 |
|---|---|
| 目标站点需要登录/Cookie | 用 `venv\Scripts\yt-dlp.exe <url>` 手工执行同一链接，观察报错 |
| 大文件下载慢 | `app.py` 硬编码 300 秒超时，超过即报 `Download timed out (5 min limit)`，需改代码或换小文件 |
| 外部程序被安全软件拦截 | 检查杀毒软件日志；确认 `downloads\` 有写入权限 |
| 进程被外部因素挂起 | 检查 `ps`/任务管理器中 yt-dlp、ffmpeg 是否仍有活动 |

**定位技巧**：任务失败时 `/api/status/<job_id>` 的 `error` 字段会回传 yt-dlp 的**最后一行 stderr**，这是最直接的线索。

---

### Q4 端口被占用 `OSError: [WinError 10048]`

```cmd
netstat -ano | findstr :8899
taskkill /F /PID <上一步查到的PID>
```
或直接换端口：`set PORT=9000 && start-reclip.bat`

---

### Q5 `./reclip.sh` 在 Windows 上运行失败

**现象**：提示 `Missing required tools: python3`，或 `pip: command not found`。

**根因**：`reclip.sh` 是 Bash 脚本，依赖 `python3` / `brew` / `apt` 与 `venv/bin/activate` 这些 POSIX 约定；Windows 的虚拟环境目录是 `venv\Scripts\`，且解释器名通常为 `python`。

**解决**：Windows 下不要用 `reclip.sh`，改用 `start-reclip.bat`（步骤 4）。若坚持用脚本，可在 Git Bash / WSL 中运行，但需自行解决 `python3` 命令名映射。

---

### Q6 下载成功但产物被截断 / 文件名异常

`app.py` 会对标题做非法字符过滤并**截断到 100 字符**，再作为文件名。若标题含大量特殊字符或极长，文件名可能被裁短——这是设计行为，非缺陷。

---

### Q7 某站点突然无法解析（Extractor 失效）

**根因**：目标网站改版会导致 yt-dlp 的 extractor 失效，这是最常见的长期维护问题。

**解决**：更新 yt-dlp 即可，这也是仓库脚本在每次启动时执行 `pip install -U yt-dlp` 的原因。
```cmd
venv\Scripts\python.exe -m pip install -U yt-dlp
```
如需临时跳过启动时的自动更新，设置环境变量 `RECLIP_NO_UPDATE=1`。

---

### Q8 Bilibili 提示「1080P 高码率 are missing; you have to become a premium member」

**根因**：高码率画质需要大会员。属于站点权限限制，非部署故障。可通过 `--cookies-from-browser` 传入已登录浏览器的 Cookie 解决，但需要修改 `app.py` 的命令拼接逻辑。

---

### Q9 局域网其他设备访问不到

**根因**：`app.py` 默认绑定 `127.0.0.1`（仅本机可访问）。

**解决**：
```cmd
set HOST=0.0.0.0
start-reclip.bat
```
并放行防火墙：
```cmd
netsh advfirewall firewall add rule name="ReClip 8899" dir=in action=allow protocol=TCP localport=8899
```
> 安全提示：`app.py` 的开发服务器**没有认证机制**，暴露到公网会被滥用，请勿直接对公网开放。

---

### Q10 并发与稳定性

- Flask 开发服务器为单线程阻塞模型（实际以 `threaded` 默认开启）。批量下载多个大文件时，多线程 + 每任务 300 秒超时的组合可能导致资源竞争。
- `jobs` 字典无锁，高并发下存在竞态。**个人自用无碍，多人共用建议改用生产级 WSGI 服务器**（仓库 Dockerfile 提供了 `gunicorn` 方案）：

```bash
docker build -t reclip .
docker run -p 8899:8899 reclip
```

---

## 六、启动与停止

| 操作 | 命令 |
|---|---|
| 启动 | 双击 `start-reclip.bat`，或命令行执行 |
| 停止 | 在运行窗口按 `Ctrl+C` |
| 强制停止 | `taskkill /F /IM python.exe`（会终止所有 Python 进程，请谨慎） |
| 清理下载产物 | 删除 `downloads\` 目录内文件（目录会被自动重建） |

---

## 附：实测环境记录

```
OS            : Windows
Python        : 3.13.12 (venv)
Flask         : 3.1.3
yt-dlp        : 2026.08.19
ffmpeg        : 9.0.2-full_build (Gyan)
监听地址      : http://127.0.0.1:8899
验证通过      : 首页 200 / 参数校验 400 / 任务查询 404 / Bilibili 元数据解析 / 音视频流下载
```
