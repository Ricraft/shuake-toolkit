# 统一启动器项目说明

本仓库由三个边界不同的部分组成：统一启动器、自研的 Autovisor Python 核心，以及随包分发的 Yatori 第三方核心。运行数据、账号配置、日志和二进制依赖不属于业务源码。

## 目录职责

```text
.
├─ 统一启动器.py              # 桌面应用组合入口（仍是后续拆分重点）
├─ src/                       # 启动器后端、题库服务、更新与通用基础设施
│  ├─ launcher_api.py         # pywebview 暴露给前端的最小 API
│  ├─ dependencies.py         # 唯一的启动器依赖定义与检查入口
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

- `requirements.txt`：统一启动器和 Autovisor 基础运行依赖。
- `requirements-optional.txt`：不使用 `Autovisor/runtime_deps` 时安装 OpenCV/NumPy。
- `Autovisor/requirements-web.txt`：实验性 FastAPI Dashboard。
- `requirements-dev.txt`：测试环境。

安装基础依赖：

```powershell
py -m pip install -r requirements.txt
py -m playwright install chromium
```

Autovisor 的验证码二进制依赖可由以下入口准备：

```powershell
py Autovisor\download_runtime_deps.py
```

## 运行入口

```powershell
py 统一启动器.py
py Autovisor\Autovisor.py
py Autovisor\Autovisor_Multi.py
```

账号密码、API Key、Cookie、数据库和日志均为本地敏感运行数据；请勿提交或对外分发。请仅在平台规则和授权范围内使用自动化功能。

当前实现率、已确认缺陷及后续拆分顺序见 [代码审查报告](docs/CODE_AUDIT.md)。
