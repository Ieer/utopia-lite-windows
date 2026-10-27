# Windows 11 使用说明

此包包含 Utopia Lite 源码、前端静态资源、当前主数据库及其挂载 SQLite 数据源的快照、已登记的本地 Markdown 源文件，以及 qwen2.5:1.5b 和 nomic-embed-text 模型。不包含树莓派的 Linux 虚拟环境、Linux Ollama、未登记的文件或配置密钥。数据库可能含业务数据和用户记录，请只交付给授权人员。

## 联网安装

1. 将整个 ZIP 解压到可写的本地目录，例如 `C:\Utopia`。不要在压缩包内直接运行，也不要只复制 `utopia-lite` 子目录。
2. 安装 Windows 64 位 Python 3.11：https://www.python.org/downloads/windows/ 。安装时勾选 Python Launcher 和 Add Python to PATH。不要使用嵌入式 Python 包，它不支持此项目所需的 SQLite 扩展。
3. 启动器优先使用根目录 `ollama\ollama.exe` 便携运行时，无需安装 Ollama。此运行时只保留 CPU 后端，不提供 GPU 加速；模型已随包提供，不需要重新下载。没有便携运行时时才查找系统安装的 Ollama。
4. 双击 `utopia-lite\setup-windows.cmd`。首次安装 Python 依赖需要联网；安装完成后，同一电脑可离线运行。
5. 双击 `utopia-lite\run-windows.cmd`，等待初始化完成，浏览器会自动打开 http://127.0.0.1:18050 。API 文档：http://127.0.0.1:18000/docs 。

首次启动会在没有 `admin` 用户时创建初始账号 `admin`、密码 `admin123`；已有 `admin` 的密码不会被覆盖。登录后可在页面顶部退出。请在开放访问前停止服务，在 `utopia-lite` 目录运行 `.venv\Scripts\python.exe change_password.py admin` 修改密码，再重启服务；重启会使已有登录会话失效。

保留启动窗口，按 Ctrl+C 停止服务。再次运行不会重置已有演示数据。端口 18000、18050 和本包专用的 Ollama 端口 11435 必须空闲。默认只允许本机访问；不要将未统一鉴权的 API 暴露到互联网。

## 完全离线安装

在一台可联网的 Windows 64 位电脑上安装 Python 3.11，并将项目包解压到可写目录。使用项目要求的同一 Python 版本和架构下载依赖 wheel：

```powershell
cd C:\Utopia
py -3.11 -m pip download --only-binary=:all: --dest wheelhouse -r utopia-lite\requirements.txt
```

将整个项目目录（含 `wheelhouse`、`models` 和 `ollama`）复制到内网目标机。目标机已有 Windows x64 Python 3.11 时无需重新安装，否则需另行传入 Python 安装程序。便携 Ollama 无需安装，不要复制其他机器的 `.venv`。在项目目录运行：

```powershell
utopia-lite\setup-windows.cmd -Offline
```

默认从项目根目录的 `wheelhouse` 安装，不访问包索引。依赖包必须包含所有 `requirements.txt` 依赖及其传递依赖；也可通过 `-Wheelhouse "D:\packages\wheelhouse"` 指定其他目录。安装后使用 `utopia-lite\run-windows.cmd` 启动。模型已随包提供，不需要联网拉取。若下载命令提示缺少二进制发行包，应使用 Windows x64、Python 3.11 环境准备 wheelhouse，不要把其他操作系统或 Python 版本的 wheel 混入。

## 内网访问

默认仅本机访问。确需让内网用户访问时，在命令提示符中设置监听地址再启动：

```bat
set UTOPIA_HOST=0.0.0.0
utopia-lite\run-windows.cmd
```

用户通过 `http://<服务器内网IP>:18050` 访问；API 使用 18000 端口。仅在 Windows 防火墙中向指定可信网段开放所需端口，不要开放 Ollama 的 11435 端口。当前 API 并非所有路由都要求鉴权，不能直接暴露到不可信网络；正式部署应放在带统一认证和 TLS 的内网反向代理后，并限制服务器及数据库文件访问权限。

当前数据库已经放入 `utopia-lite\data\utopia-lite.db`；挂载库放入 `data\sources`，首次启动自动修正 Linux 挂载路径。保留现有实体、文档、映射和历史记录，不重新生成演示数据。当前 64 维 hash 向量索引保持原配置，本地聊天仍可使用 Ollama。SQLite 快照包含已提交的 WAL 数据，但不同数据库之间不是跨库事务快照；打包期间请避免编辑。迁移后如需移动目录，已有绝对挂载路径需重新配置。

## 数据与排错

- 数据库和本地工作文件保存在 `utopia-lite\data`，运行日志保存在 `utopia-lite\logs`。备份时先停止服务，再复制整个目录。
- 如果 PowerShell 提示无法运行脚本，请确认这是可信压缩包，右键 ZIP 属性，勾选“解除锁定”后重新解压。启动入口仅对当前 PowerShell 进程设置执行策略，不更改系统策略；企业组策略仍可能禁止运行。
- 在线安装失败时检查联网条件；离线安装失败时确认 `wheelhouse` 与目标机 Python 版本/架构匹配，再运行 `setup-windows.cmd -Offline`。启动失败时查看 `logs\*.err.log`。
- 修改端口可在命令提示符中先执行 `set UTOPIA_API_PORT=18001` 和 `set UTOPIA_PORT=18051`，然后运行 `run-windows.cmd`。
- 压缩包根目录的 `MANIFEST.sha256` 是包内文件校验清单；ZIP 旁边的 `.sha256` 文件用于检查拷贝后的 ZIP 是否完整。Windows 可使用 `certutil -hashfile 压缩包文件名.zip SHA256`。

## 离线打包

本目录的 `wheelhouse` 针对 Windows x64 / CPython 3.11。打包前必须准备根目录 `ollama` CPU 便携运行时；缺少该组件时打包脚本会拒绝生成 ZIP。联网准备机可执行 `py -3.11 prepare-ollama.py`，按需下载官方 Ollama 0.40.3 的 CPU 文件；该模式通过 HTTPS 获取并检查 ZIP 条目 CRC，不等同于完整 ZIP 的 SHA-256 校验。网络不允许分段下载时，下载官方 `https://github.com/ollama/ollama/releases/download/v0.40.3/ollama-windows-amd64.zip`，再执行 `py -3.11 prepare-ollama.py --archive D:\packages\ollama-windows-amd64.zip`，此模式会先核验官方完整 ZIP SHA-256。两种模式均保留许可证并生成 `ollama\SOURCE.json` 来源和文件摘要记录，不覆盖已有 `ollama`。

启动器设置 `OLLAMA_NO_CLOUD=1`，模型服务仅监听本机 11435 端口。CPU 推理速度取决于机器配置；如需 GPU 加速，应另行准备完整、匹配显卡的官方运行时，不能只替换主程序。

停止所有服务后，在包根目录执行：

启动器还支持 `powershell.exe -NoProfile -ExecutionPolicy Bypass -File utopia-lite\windows.ps1 -SmokeTest`，会检查 API、图谱、登录页面和未登录的 Dash 访问保护后自动停止服务，不打开浏览器。它仍会执行正常数据库初始化和挂载路径迁移；测试业务快照时，应先复制整个 `data` 目录，再通过 `UTOPIA_DATA_ROOT` 和 `UTOPIA_LITE_DB` 指向副本。

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\package-windows.ps1
```

默认在当前目录的上一级生成 `utopia-lite-offline-win64-py311.zip` 和 `.zip.sha256`，已有文件不会覆盖；可用 `-OutputPath D:\delivery\utopia.zip` 指定其他文件名。脚本打包源码、数据、模型、便携 Ollama、wheelhouse 和部署说明，排除 `.venv`、日志和 Python 缓存，重新生成不含自身条目的包内 `MANIFEST.sha256`。数据库存在非空 WAL 时会拒绝打包，须先停止服务并完成数据库 checkpoint。数据库含业务数据时只能交付给授权人员。

交付包不包含 Windows Python 安装程序；内网目标机需已有 Python 3.11 x64。首次执行 `setup-windows.cmd -Offline`，随后执行 `run-windows.cmd`，不需要 `ollama pull`。