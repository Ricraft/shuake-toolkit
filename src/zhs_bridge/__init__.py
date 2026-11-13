"""ZHS 核心桥接脚本（在 .runtime/venv-zhs 解释器下运行）。

启动器主进程运行在独立的 venv 里，不安装 zhs 依赖；需要调用 zhs 能力时，
由这里的小脚本以 venv-zhs 的解释器启动，用换行分隔的 JSON 事件与启动器通信。
"""

__all__ = ["qr_login"]
