# 第三方组件声明：zhs

本目录 `zhs/` 内是第三方开源项目 **zhs**（智慧树自动刷课工具）的原样副本，
用于「统一启动器」的 zhs 核心。它不是本项目的原创代码。

- 上游项目：https://github.com/pjm314159/zhs
- 内置版本：0.1.5（上游 git main@50bd236）
- 许可证：**GPL-3.0-only**（全文见本目录 `LICENSE`）
- 原样性：`src/zhs` 与上游一致，**未做任何修改**；校验清单见 `UPSTREAM.sha256`，
  元信息见 `UPSTREAM.json`。

## 为什么原样内置

启动器对 zhs 的集成全部放在启动器自己的文件里（进程启动、配置播种、日志读取、UI），
因此升级 zhs 时可以整目录替换，不需要逐版改源码：

```powershell
python scripts/update_zhs.py --source "D:\Python\zhs2123\zhs"
```

该脚本会校验本地是否被改动过、覆盖源文件、重写 `UPSTREAM.sha256`，并做一次冒烟检查。

## GPL 提示

GPL-3.0 具有传染性：若对外分发包含本目录的程序，整体分发需遵循 GPL-3.0 并附带源码。
仅在个人机器上使用不受此约束。正式分发前请确认合规策略（见 README 的“第三方组件”一节）。
