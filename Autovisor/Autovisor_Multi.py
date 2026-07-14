# encoding=utf-8
"""
Autovisor 多账号版本
支持同时运行多个智慧树账号

使用方法:
1. 配置 configs.ini 添加多个账号
2. 运行: python Autovisor_Multi.py
3. 可选参数: --max 3 (限制同时运行的最大账号数)

注意: Windows系统必须使用 if __name__ == "__main__": 保护
"""
import argparse
import sys
import os
import multiprocessing

# Windows 多进程必须使用 freeze_support
multiprocessing.freeze_support()

# 添加模块路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from runtime_bootstrap import activate_runtime_dependencies

activate_runtime_dependencies()

# 提前导入 logger 触发 sys.stdout 安全替换，防止 GBK 编码崩溃
import modules.logger

from modules.multi_account_runner import MultiAccountManager
from modules.multi_config import MultiAccountConfig, generate_multi_account_config_example


def create_example_config():
    """创建多账号配置示例"""
    example = generate_multi_account_config_example()
    with open("configs_multi_example.ini", "w", encoding="utf-8") as f:
        f.write(example)
    print("已创建多账号配置示例: configs_multi_example.ini")
    print("请参照此文件格式修改你的 configs.ini")


def main():
    parser = argparse.ArgumentParser(description="Autovisor 多账号刷课工具")
    parser.add_argument("--config", "-c", default="configs.ini", help="配置文件路径")
    parser.add_argument("--max", "-m", type=int, default=None, help="同时运行的最大账号数")
    parser.add_argument("--example", action="store_true", help="生成配置示例文件")
    parser.add_argument("--list", "-l", action="store_true", help="列出所有配置的账号")
    args = parser.parse_args()
    
    if args.example:
        create_example_config()
        return
    
    print("=" * 60)
    print("Autovisor 多账号刷课工具")
    print("Github: CXRunfree All Rights Reserved.")
    print("=" * 60)
    
    # 检查配置文件
    if not os.path.exists(args.config):
        print(f"错误: 找不到配置文件 {args.config}")
        print("请创建配置文件或运行: python Autovisor_Multi.py --example")
        return
    
    # 加载配置
    multi_config = MultiAccountConfig(args.config)
    
    if args.list:
        print(f"\n共配置了 {multi_config.get_account_count()} 个账号:\n")
        for acc in multi_config.accounts:
            print(f"账号 {acc.account_id}:")
            print(f"  用户名: {acc.username or '(未设置)'}")
            print(f"  浏览器: {acc.driver}")
            print(f"  课程数: {len(acc.course_urls)}")
            print(f"  隐藏窗口: {'是' if acc.enable_hide_window else '否'}")
            print(f"  自动验证码: {'是' if acc.enable_auto_captcha else '否'}")
            print()
        return
    
    if multi_config.get_account_count() == 0:
        print("错误: 未配置任何有效账号")
        return
    
    # 运行多账号
    manager = MultiAccountManager(args.config)
    
    print(f"\n启动配置:")
    print(f"  配置文件: {args.config}")
    print(f"  账号总数: {multi_config.get_account_count()}")
    print(f"  同时运行: {args.max or multi_config.get_account_count()}")
    print(f"\n按 Ctrl+C 可以随时停止所有账号\n")
    
    try:
        manager.run_all(max_concurrent=args.max)
    except Exception as e:
        print(f"运行出错: {e}")
        import traceback
        traceback.print_exc()
    finally:
        try:
            if sys.stdin and sys.stdin.isatty():
                input("\n按 Enter 键退出...")
        except EOFError:
            pass


if __name__ == "__main__":
    main()
