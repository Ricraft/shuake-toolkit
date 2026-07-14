# -*- coding: utf-8 -*-
"""
刷课工具包 - 依赖一键安装器
自动安装所有依赖，使用国内镜像源加速
"""
import sys
import os
import subprocess
import threading
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
import site

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from src.dependencies import CORE_DEPENDENCIES, OPTIONAL_DEPENDENCIES

# 设置UTF-8编码
if sys.stdout and sys.stdout.encoding and "gbk" in sys.stdout.encoding.lower():
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

class DependencyInstaller:
    def __init__(self, root):
        self.root = root
        self.root.title("刷课工具包 - 依赖安装器")
        self.root.geometry("700x550")
        self.root.resizable(True, True)
        
        self.installing = False
        self.setup_ui()
        
    def setup_ui(self):
        # 主框架
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        
        # 标题
        title_label = ttk.Label(
            main_frame, 
            text="依赖一键安装器", 
            font=("Microsoft YaHei UI", 16, "bold")
        )
        title_label.grid(row=0, column=0, columnspan=2, pady=(0, 20))
        
        # Python环境信息
        env_frame = ttk.LabelFrame(main_frame, text="Python环境", padding="10")
        env_frame.grid(row=1, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=(0, 15))
        
        self.python_info = tk.StringVar()
        self.update_python_info()
        ttk.Label(env_frame, textvariable=self.python_info).grid(row=0, column=0, sticky=tk.W)
        
        # 镜像源选择
        mirror_frame = ttk.LabelFrame(main_frame, text="镜像源配置", padding="10")
        mirror_frame.grid(row=2, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=(0, 15))
        
        ttk.Label(mirror_frame, text="PyPI镜像:").grid(row=0, column=0, sticky=tk.W)
        
        self.pypi_mirror = tk.StringVar(value="tuna")
        pypi_combo = ttk.Combobox(
            mirror_frame, 
            textvariable=self.pypi_mirror,
            values=[
                "tuna (清华)", 
                "aliyun (阿里)", 
                "huawei (华为)", 
                "official (官方)"
            ],
            state="readonly",
            width=25
        )
        pypi_combo.grid(row=0, column=1, padx=10, sticky=tk.W)
        
        ttk.Label(mirror_frame, text="Playwright浏览器镜像:").grid(row=1, column=0, sticky=tk.W, pady=(10, 0))
        
        self.pw_mirror = tk.StringVar(value="npmmirror")
        pw_combo = ttk.Combobox(
            mirror_frame, 
            textvariable=self.pw_mirror,
            values=[
                "npmmirror (淘宝)", 
                "official (官方)"
            ],
            state="readonly",
            width=25
        )
        pw_combo.grid(row=1, column=1, padx=10, sticky=tk.W, pady=(10, 0))
        
        # 安装选项
        options_frame = ttk.LabelFrame(main_frame, text="安装选项", padding="10")
        options_frame.grid(row=3, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=(0, 15))
        
        self.install_browser = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            options_frame, 
            text="安装Playwright Chromium浏览器（推荐）",
            variable=self.install_browser
        ).grid(row=0, column=0, sticky=tk.W)
        
        self.use_system_browser = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            options_frame, 
            text="优先使用系统浏览器（Edge/Chrome）",
            variable=self.use_system_browser
        ).grid(row=1, column=0, sticky=tk.W, pady=(5, 0))
        
        # 日志区域
        log_frame = ttk.LabelFrame(main_frame, text="安装日志", padding="10")
        log_frame.grid(row=4, column=0, columnspan=2, sticky=(tk.W, tk.E, tk.N, tk.S), pady=(0, 15))
        
        self.log_text = scrolledtext.ScrolledText(
            log_frame, 
            height=12, 
            wrap=tk.WORD,
            font=("Consolas", 9)
        )
        self.log_text.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        
        # 进度条
        self.progress = ttk.Progressbar(
            main_frame, 
            orient=tk.HORIZONTAL, 
            mode='indeterminate'
        )
        self.progress.grid(row=5, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=(0, 10))
        
        # 按钮
        button_frame = ttk.Frame(main_frame)
        button_frame.grid(row=6, column=0, columnspan=2, sticky=(tk.W, tk.E))
        
        self.install_btn = ttk.Button(
            button_frame, 
            text="开始安装", 
            command=self.start_install,
            style="Accent.TButton"
        )
        self.install_btn.grid(row=0, column=0, padx=(0, 10))
        
        self.check_btn = ttk.Button(
            button_frame, 
            text="检查依赖", 
            command=self.check_dependencies
        )
        self.check_btn.grid(row=0, column=1)
        
        # 配置权重
        main_frame.columnconfigure(0, weight=1)
        main_frame.rowconfigure(4, weight=1)
        
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        
    def update_python_info(self):
        info = f"Python {sys.version.split()[0]} | 路径: {sys.executable}"
        self.python_info.set(info)
        
    def log(self, message):
        self.log_text.insert(tk.END, message + "\n")
        self.log_text.see(tk.END)
        self.root.update()
        
    def get_mirror_urls(self):
        """获取镜像源URL"""
        pypi_map = {
            "tuna (清华)": "https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple",
            "aliyun (阿里)": "https://mirrors.aliyun.com/pypi/simple",
            "huawei (华为)": "https://mirrors.huaweicloud.com/repository/pypi/simple",
            "official (官方)": "https://pypi.org/simple"
        }
        
        pw_map = {
            "npmmirror (淘宝)": "https://npmmirror.com/mirrors/playwright",
            "official (官方)": "https://playwright.azureedge.net"
        }
        
        return (
            pypi_map.get(self.pypi_mirror.get(), pypi_map["tuna (清华)"]),
            pw_map.get(self.pw_mirror.get(), pw_map["npmmirror (淘宝)"])
        )
        
    def run_command(self, cmd, env=None):
        """运行命令并捕获输出"""
        try:
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding='utf-8',
                errors='replace',
                bufsize=1,
                universal_newlines=True,
                env=env
            )
            
            for line in process.stdout:
                if line:
                    self.log(line.rstrip())
            
            return process.wait() == 0
            
        except Exception as e:
            self.log(f"错误: {str(e)}")
            return False
            
    def check_dependencies(self):
        """检查依赖安装情况"""
        self.log("="*60)
        self.log("正在检查依赖...")
        self.log("="*60)
        
        # 核心依赖检查
        self.log("\n--- 核心依赖 ---")
        core_deps = [
            (dependency.module, dependency.requirement)
            for dependency in CORE_DEPENDENCIES + OPTIONAL_DEPENDENCIES
        ]
        
        all_ok = True
        for module_name, display_name in core_deps:
            try:
                __import__(module_name)
                self.log(f"✓ {display_name}: 已安装")
            except ImportError:
                self.log(f"✗ {display_name}: 未安装")
                all_ok = False
        
        # 检查各模块
        self.log("\n--- 项目模块检查 ---")
        modules = [
            ("统一启动器.py", "统一启动器"),
            ("scripts/fetch_zhs_courses.py", "智慧树课程获取"),
            ("src/题库服务器.py", "题库服务器"),
            ("scripts/ai_connectivity_test.py", "AI连通性测试"),
            ("Autovisor/Autovisor.py", "Autovisor核心"),
        ]
        
        script_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        for rel_path, name in modules:
            full_path = os.path.join(script_dir, rel_path)
            if os.path.exists(full_path):
                self.log(f"✓ {name}: 存在")
            else:
                self.log(f"✗ {name}: 不存在")
                all_ok = False
        
        # 检查Playwright浏览器
        self.log("\n--- Playwright检查 ---")
        try:
            from playwright.async_api import async_playwright
            self.log("✓ Playwright API: 可用")
            
            # 检查浏览器是否安装
            try:
                import subprocess
                result = subprocess.run(
                    [sys.executable, "-m", "playwright", "install", "--dry-run", "chromium"],
                    capture_output=True,
                    text=True
                )
                if "already installed" in result.stdout.lower():
                    self.log("✓ Chromium浏览器: 已安装")
                else:
                    self.log("○ Chromium浏览器: 未安装（可使用系统浏览器）")
            except:
                self.log("○ 无法检测浏览器安装状态")
                
        except Exception as e:
            self.log(f"✗ Playwright API: {str(e)}")
            all_ok = False
        
        # 检查系统浏览器
        self.log("\n--- 系统浏览器检查 ---")
        system_browsers = [
            ("msedge", "Microsoft Edge"),
            ("chrome", "Google Chrome"),
        ]
        
        found_browser = False
        for browser, name in system_browsers:
            try:
                import shutil
                if shutil.which(browser):
                    self.log(f"✓ {name}: 可用")
                    found_browser = True
            except:
                pass
        
        if not found_browser:
            self.log("○ 未检测到Edge/Chrome，可能需要安装Playwright浏览器")
        
        self.log("="*60)
        if all_ok:
            self.log("\n所有依赖检查通过！")
            messagebox.showinfo("检查完成", "所有依赖已安装，可以开始使用了！")
        else:
            self.log("\n部分依赖缺失，请点击'开始安装'")
            messagebox.showwarning("检查完成", "部分依赖缺失，建议进行安装")
            
    def start_install(self):
        """开始安装"""
        if self.installing:
            return
            
        self.installing = True
        self.install_btn.config(state="disabled", text="安装中...")
        self.check_btn.config(state="disabled")
        self.progress.start()
        
        thread = threading.Thread(target=self.install_all)
        thread.daemon = True
        thread.start()
        
    def install_all(self):
        """安装所有依赖"""
        try:
            pypi_url, pw_url = self.get_mirror_urls()
            
            self.log("="*60)
            self.log("开始安装依赖...")
            self.log(f"PyPI镜像: {pypi_url}")
            self.log(f"Playwright镜像: {pw_url}")
            self.log("="*60)
            
            # 设置环境变量
            env = os.environ.copy()
            env["PLAYWRIGHT_DOWNLOAD_HOST"] = pw_url
            env["PYTHONUNBUFFERED"] = "1"
            
            # 升级pip
            self.log("\n[1/4] 升级pip...")
            pip_cmd = [sys.executable, "-m", "pip", "install", "--upgrade", "pip", "-i", pypi_url]
            if not self.run_command(pip_cmd, env):
                self.log("pip升级失败，但继续安装...")
            
            # 安装核心依赖（版本应与 requirements.txt 保持同步）
            self.log("\n[2/4] 安装核心依赖...")
            requirements = [
                dependency.requirement
                for dependency in CORE_DEPENDENCIES + OPTIONAL_DEPENDENCIES
            ]
            
            for req in requirements:
                self.log(f"\n安装: {req}")
                cmd = [sys.executable, "-m", "pip", "install", req, "-i", pypi_url, "--no-cache-dir"]
                if not self.run_command(cmd, env):
                    self.log(f"警告: {req} 安装可能有问题")
            
            # 安装Playwright浏览器
            if self.install_browser.get():
                self.log("\n[3/4] 安装Playwright浏览器...")
                pw_cmd = [sys.executable, "-m", "playwright", "install", "chromium"]
                if not self.run_command(pw_cmd, env):
                    self.log("浏览器安装失败，尝试使用系统浏览器...")
            else:
                self.log("\n[3/4] 跳过浏览器安装")
            
            # 完成
            self.log("\n[4/4] 安装完成！")
            self.log("="*60)
            self.log("依赖安装完成，请点击'检查依赖'验证")
            
            self.root.after(0, lambda: messagebox.showinfo(
                "安装完成", 
                "依赖安装完成！\n建议点击'检查依赖'按钮验证安装结果。"
            ))
            
        except Exception as e:
            self.log(f"\n安装过程出错: {str(e)}")
            import traceback
            self.log(traceback.format_exc())
            
        finally:
            self.installing = False
            self.root.after(0, lambda: self.install_btn.config(state="normal", text="开始安装"))
            self.root.after(0, lambda: self.check_btn.config(state="normal"))
            self.root.after(0, lambda: self.progress.stop())

def main():
    root = tk.Tk()
    app = DependencyInstaller(root)
    
    # 居中显示
    root.update_idletasks()
    width = root.winfo_width()
    height = root.winfo_height()
    x = (root.winfo_screenwidth() // 2) - (width // 2)
    y = (root.winfo_screenheight() // 2) - (height // 2)
    root.geometry('{}x{}+{}+{}'.format(width, height, x, y))
    
    root.mainloop()

if __name__ == "__main__":
    main()
