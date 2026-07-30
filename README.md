# 统一启动器项目说明

本仓库由三个边界不同的部分组成：统一启动器、自研的 Autovisor Python 核心，以及随包分发的 Yatori 第三方核心。运行数据、账号配置、日志和二进制依赖不属于业务源码。

## 目录职责

```text
.
├─ 统一启动器.py              # 仅 WebView 的桌面应用组合入口
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
│  ├─ core_manager.py         # Yatori/Autovisor 下载和更新
│  └─ 题库服务器.py            # 本地题库 HTTP 服务
├─ web/                       # 统一启动器 WebView 前端
├─ Autovisor/                 # Autovisor 可独立运行的 Python 项目
│  ├─ runtime_bootstrap.py    # 所有入口统一挂载 runtime_deps
│  ├─ modules/                # 浏览器自动化、答题、配置及运行器
│  ├─ runtime_deps/           # 自动生成/分发的二进制依赖，不作为源码维护
│  ├─ web/                    # 可选 FastAPI Dashboard（仍为实验性）
│  └─ tests/                  # Dashboard API 测试
├─ Yatori/                    # 第三方二进制核心及其资产，按供应商包管理
├─ scripts/                   # 安装、诊断和课程数据辅助脚本
├─ data/                      # 运行缓存和题库数据库
└─ docs/                      # 架构审查与重构记录
```

## 依赖

- `requirements.txt`：统一启动器和 Autovisor 基础运行依赖，包含必需的 `pywebview`。
- `requirements-optional.txt`：不使用 `Autovisor/runtime_deps` 时安装 OpenCV/NumPy。
- `Autovisor/requirements-web.txt`：实验性 FastAPI Dashboard。
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

开发调试时可以显式设置 `LAUNCHER_ALLOW_UNVERIFIED_CORE_UPDATES=1` 跳过摘要
要求；这会降低更新安全性，不建议在日常使用环境启用。

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

多账号配置使用 `user-account-N`、`course-url-N` 等编号小节。未编号的浏览器、
脚本和课程选项可作为所有账号的公共默认值；每个账号使用独立 Cookie 和进程，
但共用同一套完整课程执行逻辑。`--max N` 只限制并发数，不会跳过排队账号。

账号密码、API Key、Cookie、数据库和日志均为本地敏感运行数据；请勿提交或对外分发。请仅在平台规则和授权范围内使用自动化功能。

当前实现率、已确认缺陷及后续拆分顺序见 [代码审查报告](docs/CODE_AUDIT.md)。
