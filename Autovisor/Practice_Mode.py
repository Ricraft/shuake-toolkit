# encoding=utf-8
"""
智慧树刷题模式 — 入口脚本

功能：
  自动登录智慧树后进入我的课堂页，系统自动监听 doHomework API
  检测到测验后自动调用题库答题并提交，提交后返回我的课堂页等待用户下一步操作

使用方法：
  python Practice_Mode.py

配置：
  使用 configs.ini 中的账号信息

依赖：
  - 需先启动题库服务器（统一启动器中点击启动题库，或单独运行 题库服务器.py）
  - 或使用 ZError 作为题库后端
"""
import sys
import os
import asyncio
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from runtime_bootstrap import activate_runtime_dependencies

activate_runtime_dependencies()

from modules.practice_mode import main
from modules.logger import Logger


if __name__ == "__main__":
    print("=" * 60)
    print("智慧树刷题模式 v1.0")
    print("=" * 60)
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n用户中断，刷题模式已退出")
    except Exception as e:
        logger = Logger()
        logger.error(f"刷题模式异常: {repr(e)}", shift=True)
        logger.write_log(traceback.format_exc())
        print(f"\n程序异常: {e}")
    finally:
        try:
            logger = Logger()
            logger.save(inform=True)
        except Exception:
            pass
        try:
            if sys.stdin and sys.stdin.isatty():
                input("程序已结束，按 Enter 退出...")
        except EOFError:
            pass
