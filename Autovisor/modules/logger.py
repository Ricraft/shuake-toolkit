import os
import sys
import threading
import time


# 全局替换 sys.stdout，使其在 GBK 终端上遇到无法编码的字符时自动替换
if sys.stdout and hasattr(sys.stdout, 'encoding') and sys.stdout.encoding and sys.stdout.encoding.upper() == 'GBK':
    _original_stdout = sys.stdout
    _stdout_fd = None
    try:
        _stdout_fd = _original_stdout.fileno()
    except (OSError, ValueError):
        pass
    
    class _SafeStdout:
        encoding = 'utf-8'
        
        def write(self, msg):
            try:
                _original_stdout.write(msg)
            except UnicodeEncodeError:
                _original_stdout.write(msg.encode('gbk', errors='replace').decode('gbk'))
        
        def flush(self):
            try:
                _original_stdout.flush()
            except (OSError, ValueError):
                pass
        
        def isatty(self):
            return _original_stdout.isatty()
        
        def fileno(self):
            if _stdout_fd is not None:
                return _stdout_fd
            raise OSError()
    
    sys.stdout = _SafeStdout()


# 单例模式日志器
class Logger:
    _instance = None
    _lock = threading.Lock()  # 线程安全锁

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(Logger, cls).__new__(cls)
                cls._instance._init()
        return cls._instance

    def _init(self):
        os.makedirs("logs", exist_ok=True)  # 创建日志文件夹
        self._configured = False
        self.configure(os.getenv("AUTOVISOR_ACCOUNT_ID"))
        self.text = ""

    def configure(self, account_id=None):
        """为本次进程选择不会与其他账号碰撞的日志文件。"""
        if self._configured:
            return
        timestamp = time.strftime("%Y%m%d_%H%M%S", time.localtime())
        account_suffix = f"_Account_{account_id}" if account_id not in (None, "") else ""
        self.filename = f"logs/Log{account_suffix}_{timestamp}_{os.getpid()}.txt"
        self._configured = True

    def write_log(self, msg):
        date = time.strftime("%H:%M:%S", time.localtime())
        self.text += f"[{date}] {msg}"

    def save(self, inform=True):
        with open(self.filename, "w", encoding="utf-8") as f:
            f.write(self.text)
        if inform:
            print(f"日志文件已保存至: {self.filename}")

    def info(self, msg, shift=False):
        if shift:
            text = f"\n[INFO] {msg}"
        else:
            text = f"[INFO] {msg}"
        print(text, flush=True)
        self.write_log(f"[INFO] {msg}\n")

    def warn(self, msg, shift=False):
        if shift:
            text = f"\n[WARN] {msg}"
        else:
            text = f"[WARN] {msg}"
        print(text, flush=True)
        self.write_log(f"[WARN] {msg}\n")

    def error(self, msg, shift=False):
        if shift:
            text = f"\n[ERROR] {msg}"
        else:
            text = f"[ERROR] {msg}"
        print(text, flush=True)
        self.write_log(f"[ERROR] {msg}\n")
