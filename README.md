# 统一启动器项目说明

当前启动器版本：**v1.7.3**。更新内容见 [v1.7.3 发布说明](docs/releases/v1.7.3.md)。
关于页联网读取最新 GitHub Release 的说明，仅展示最新已发布版本；更新内容在 Markdown 中维护，不再写入 HTML。

本仓库由四个边界不同的部分组成：统一启动器、自研的 Autovisor Python 核心、随包分发的 Yatori 第三方核心，以及以「零补丁」方式内置的 ZHS 第三方源码核心。运行数据、账号配置、日志和二进制依赖不属于业务源码。

## 目录职责

```text
.
├─ 统一启动器.py              # WebView 桌面入口、服务门面与生命周期
├─ bootstrap.py               # 一键引导：venv / 依赖 / Playwright / ZHS 环境
├─ requirements-zhs.txt       # ZHS 独立环境依赖（由 zhs/pyproject.toml 自动生成）
├─ src/                       # 启动器后端、题库服务、更新与通用基础设施
│  ├─ application_bootstrap.py # 崩溃日志、WebView 创建和启动失败清理
│  ├─ launcher_api.py         # pywebview 暴露给前端的最小 API
│  ├─ launcher_startup_service.py # 启动服务装配与延迟任务声明
│  ├─ web_settings_service.py # Web 三组配置校验、保存与跨文件回滚
│  ├─ web_state_service.py    # Web 运行状态一致性快照与局部故障降级
│  ├─ scheduled_task_service.py # 启动延迟任务的跟踪、异常记录与退出取消
│  ├─ dependencies.py         # 唯一的启动器依赖定义与检查入口
│  ├─ autovisor_dependency_manager.py # Autovisor 依赖、浏览器与运行配置准备
│  ├─ question_bank_controller.py # 题库设置与本地服务生命周期
│  ├─ update_controller.py    # 核心检查、安装与并发状态机
│  ├─ practice_mode_service.py # Web 刷题模式的账号与进程生命周期
│  ├─ atomic_io.py            # 配置文件原子写入
│  ├─ core_manager.py         # Yatori/Autovisor/ZHS 下载和更新
│  ├─ zhs_vendor.py           # zhs/ 副本校验、同步、重装与冒烟检查
│  ├─ zhs_service.py          # ZHS 核心：环境、登录、课程、命令与日志
│  ├─ zhs_bridge/             # 在 venv-zhs 里运行的扫码/会话校验桥接脚本
│  └─ 题库服务器.py            # 本地题库 HTTP 服务
├─ web/                       # 统一启动器 WebView 前端
├─ Autovisor/                 # Autovisor 可独立运行的 Python 项目
│  ├─ runtime_bootstrap.py    # 所有入口统一挂载 runtime_deps
│  ├─ modules/                # 浏览器自动化、答题、配置及运行器
│  ├─ runtime_deps/           # 自动生成/分发的二进制依赖，不作为源码维护
│  ├─ web/                    # 可选 FastAPI Dashboard（仍为实验性）
│  └─ tests/                  # 课程流程、配置、入口、Dashboard 等核心测试
├─ Yatori/                    # 第三方二进制核心及其资产，按供应商包管理
├─ zhs/                       # ZHS 上游源码的零补丁副本（GPL-3.0，独立 venv）
├─ scripts/                   # 安装、诊断、课程数据与 update_zhs.py 辅助脚本
├─ data/                      # 运行缓存、题库数据库与 data/zhs 账号数据
└─ docs/                      # 架构审查、更新指南与重构记录
```

## 依赖

- `requirements.txt`：统一启动器和 Autovisor 基础运行依赖，包含必需的 `pywebview`。
- `requirements-optional.txt`：不使用 `Autovisor/runtime_deps` 时安装 OpenCV/NumPy。
- `Autovisor/requirements-web.txt`：实验性 FastAPI Dashboard。
- `requirements-zhs.txt`：ZHS 核心独立环境（`.runtime\venv-zhs`）的依赖，由
  `zhs/pyproject.toml` 自动生成，请勿手工修改。
- `requirements-dev.txt`：测试环境。

安装基础依赖：

```powershell
py -m pip install -r requirements.txt
py -m playwright install chromium
```

启动器会检查依赖是否能够实际导入，而不只检查包是否已安装；自动安装后仍无法
加载（例如本机缺少 DLL）时会在创建 Web 窗口前停止，并提示需要处理的依赖。
维护或排查环境时也可先运行：

```powershell
py scripts\install_dependencies.py --check
```

Autovisor 的验证码二进制依赖可由以下入口准备：

```powershell
py Autovisor\download_runtime_deps.py
```

ZHS 核心运行在独立环境 `.runtime\venv-zhs`（Python 3.13/3.14），由 bootstrap 在
安装基础依赖后自动创建；也可以只检查这一项：

```powershell
py bootstrap.py --check
```

## 核心更新安全

自动更新只接受 GitHub Release API 提供了有效 `sha256` 摘要的资源；下载后还会
核对 API 声明的文件大小和 SHA-256，校验成功前不会解压或替换现有核心。通过
GitHub 页面探测得到的回退链接、源码 `zipball` 或摘要格式异常的资源仍可用于
显示版本信息，但默认禁止自动安装。

Yatori 更新会先在独立临时目录中完成下载、摘要校验、解压、入口识别和新版本
暂存，再通过同盘目录切换启用；切换失败会自动恢复旧核心。用户配置只从当前
正在使用的 `Yatori/config.yaml` 继承，不会复用可能过期的全局备份文件。

项目内的 Autovisor 包含本地适配修改，因此 Web 界面只允许检查上游版本信息，
不会直接覆盖安装上游发行包。需要同步 Autovisor 时，应人工合并上游改动并完成
全量回归；Yatori 未作本地魔改，仍使用确认式更新流程。

ZHS 上游只发布 tag 与源码归档，没有发布方 SHA-256 摘要，因此采用另一套策略：
官方 tag 归档 + 本地哈希清单 + 安装前备份 `zhs/` + 重装/冒烟失败自动回滚，
并在更新弹窗中明确提示。`zhs/` 保持上游原样（零补丁），启动器侧集成代码全部
位于 `src/`，升级时整体替换即可；`data/zhs` 中的登录态与课程数据不受影响。
完整流程见 [更新指南](docs/更新指南.md)。

开发调试时可以显式设置 `LAUNCHER_ALLOW_UNVERIFIED_CORE_UPDATES=1` 跳过摘要
要求；这会降低更新安全性，不建议在日常使用环境启用。

## 下载源与镜像

核心包、PyPI 包、Playwright Chromium 与 Autovisor 的 numpy/opencv 都采用「并发测速
选源 + 失败降级」：先对各镜像并发采样，按实测吞吐排序后使用最快的源，失败自动换
下一个；下载中途速度过低也会换源。镜像只改变传输通道，核心包仍必须通过 GitHub
清单声明的大小与 SHA-256 校验。

- `LAUNCHER_ALLOW_GITHUB_PROXIES=0`：关闭核心包的镜像，只走 GitHub 直连。
- `LAUNCHER_PROBE_GITHUB_SOURCES=0`：关闭核心包的测速，退回按内置顺序逐个尝试。
- `LAUNCHER_ALLOW_UNVERIFIED_CORE_UPDATES=1`：开发调试用，跳过摘要校验（不建议日常使用）。

pip 镜像与 Playwright 下载源无需开关，始终先测速再安装。

## 运行入口

```powershell
Copy-Item Autovisor\configs.example.ini Autovisor\configs.ini
py 统一启动器.py
py Autovisor\Autovisor.py
py Autovisor\Autovisor_Multi.py
```

统一启动器只提供 `web/` 前端，并通过 `pywebview` 显示；项目不再包含 Tk
界面或 Tk 降级入口。若启动时提示 `pywebview` 不可用，请重新安装
`requirements.txt` 后再运行。

ZHS 核心不在启动器进程中运行，而是由 `src/core_launch_service.py` 用
`.runtime\venv-zhs\Scripts\python.exe -m zhs ...` 启动，工作目录为
`data/zhs/account-1`（zhs 的数据目录固定在当前工作目录下的 `.zhs/`）。
首次使用先在「核心设置 → ZHS 新版核心」里扫码登录，再拉取课程并运行。

多账号配置使用 `user-account-N`、`course-url-N` 等编号小节。未编号的浏览器、
脚本和课程选项可作为所有账号的公共默认值；每个账号使用独立 Cookie 和进程，
但共用同一套完整课程执行逻辑。`--max N` 只限制并发数，不会跳过排队账号。

账号密码、API Key、Cookie、数据库和日志均为本地敏感运行数据；请勿提交或对外分发。请仅在平台规则和授权范围内使用自动化功能。

当前架构、开发操作与结构精简建议见 [开发文档](docs/开发文档.md)（结构评估在 §2.6）；
当前功能边界、验证证据与后续优先级见 [功能完整性评估](docs/功能完整性评估.md)；
历史实现率估算、修复与验证记录见 [代码审查报告](docs/CODE_AUDIT.md)，其中测试数字和站点状态不是当前保证；
四条更新通道、ZHS 升级步骤与依赖清单校验见 [更新指南](docs/更新指南.md)。

## 新用户一键启动

从 GitHub Release 下载 `shuake-toolkit-<version>.zip`，解压后双击：

```text
统一启动器.exe
```

EXE 带应用图标，显示启动准备窗口与日志，并自动调用同目录的 `bootstrap.py`。
请先解压完整运行包再启动，保留包内其他文件；它仍需要本机 Python，未把全部运行环境打进单个 EXE。
`启动依赖.cmd` 保留为排障备用入口。

首次启动会自动：

1. 查找 Python 3.11–3.13；
2. 在 `.runtime\venv` 创建本地虚拟环境；
3. 安装 `requirements.txt` 中的 Python 依赖（测速选源 + 多镜像回退）；
4. 安装 Playwright Chromium；
5. 准备 `Autovisor\runtime_deps` 中的 numpy/opencv；
6. 尝试准备 Yatori 核心；
7. 查找 Python 3.13/3.14，创建 `.runtime\venv-zhs` 并安装
   `requirements-zhs.txt`（找不到时只告警，ZHS 面板会提示环境未就绪）；
8. 检测 Microsoft Edge WebView2 Runtime；
9. 启动 `统一启动器.py`。

说明：

- 第一版要求系统已安装 Python 3.11–3.13；没有 Python 时会明确提示。
- 第一版检测到缺少 WebView2 Runtime 时只显示官方下载链接，不会自动静默安装。
- Yatori 核心按 Release 资产下载；如果下载失败，启动器仍可打开，可在界面稍后重试。
- 老用户继续使用 `launcher-<version>.zip` 自更新，不需要完整包。
维护者可在仓库 Variables 中设置 `YATORI_CORE_URL`、`YATORI_CORE_SHA256`、
`YATORI_CORE_SIZE`、`YATORI_CORE_VERSION`，完整包构建时会把它们写入
`full-manifest.json`，bootstrap 会优先从该 Release 资产下载并校验 Yatori 核心。
