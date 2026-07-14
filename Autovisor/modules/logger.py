import os
import sys
import threading
import time
from collections import deque
from pathlib import Path


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
    _instance_lock = threading.Lock()
    _DEFAULT_LOG_DIR = Path(__file__).resolve().parent.parent / "logs"
    _MAX_RECENT_ENTRIES = 2000

    def __new__(cls):
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = super(Logger, cls).__new__(cls)
                cls._instance._init()
        return cls._instance

    def _init(self):
        self._write_lock = threading.RLock()
        self._recent_entries = deque(maxlen=self._MAX_RECENT_ENTRIES)
        self._configured = False
        self._account_id = None
        self._log_dir = self._DEFAULT_LOG_DIR
        self.filename = ""
        self._last_write_error = None
        self.configure(os.getenv("AUTOVISOR_ACCOUNT_ID"))

    @property
    def text(self):
        """兼容旧调用：只返回最近的有界日志窗口。"""
        with self._write_lock:
            return "".join(self._recent_entries)

    @text.setter
    def text(self, value):
        with self._write_lock:
            self._recent_entries.clear()
            if value:
                self._recent_entries.append(str(value))

    def configure(
        self,
        account_id=None,
        *,
        log_dir=None,
        force=False,
        clear=False,
    ):
        """选择日志文件；未写入前允许补充真实账号 ID。"""
        normalized_account = (
            str(account_id) if account_id not in (None, "") else None
        )
        target_dir = Path(log_dir).resolve() if log_dir else self._DEFAULT_LOG_DIR
        with self._write_lock:
            same_target = (
                self._configured
                and normalized_account == self._account_id
                and target_dir == self._log_dir
            )
            if same_target and not force:
                return self.filename
            if self._configured and self._recent_entries and not force:
                return self.filename

            target_dir.mkdir(parents=True, exist_ok=True)
            timestamp = time.strftime("%Y%m%d_%H%M%S", time.localtime())
            unique_suffix = time.time_ns() % 1_000_000
            account_suffix = (
                f"_Account_{normalized_account}" if normalized_account else ""
            )
            filename = (
                f"Log{account_suffix}_{timestamp}_{os.getpid()}_"
                f"{unique_suffix:06d}.txt"
            )
            self._log_dir = target_dir
            self.filename = str((target_dir / filename).resolve())
            self._account_id = normalized_account
            self._configured = True
            self._last_write_error = None
            if clear:
                self._recent_entries.clear()
            return self.filename

    def write_log(self, msg):
        date = time.strftime("%H:%M:%S", time.localtime())
        entry = f"[{date}] {msg}"
        with self._write_lock:
            if not self._configured:
                self.configure()
            self._recent_entries.append(entry)
            try:
                with open(self.filename, "a", encoding="utf-8") as log_file:
                    log_file.write(entry)
                    log_file.flush()
                self._last_write_error = None
            except OSError as exc:
                # 日志磁盘异常不能反向打断刷课主流程。
                self._last_write_error = exc

    def save(self, inform=True):
        with self._write_lock:
            if not self._configured:
                self.configure()
            try:
                Path(self.filename).touch(exist_ok=True)
            except OSError as exc:
                self._last_write_error = exc
        if inform:
            print(f"日志文件已保存至: {self.filename}")

    def _emit(self, level, msg, shift=False):
        prefix = f"\n[{level}]" if shift else f"[{level}]"
        print(f"{prefix} {msg}", flush=True)
        self.write_log(f"[{level}] {msg}\n")

    def info(self, msg, shift=False):
        self._emit("INFO", msg, shift)

    def warn(self, msg, shift=False):
        self._emit("WARN", msg, shift)

    def error(self, msg, shift=False):
        self._emit("ERROR", msg, shift)
