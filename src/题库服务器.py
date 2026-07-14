# encoding=utf-8
"""
题库服务器 — 内置 ZError 题库 + jieba 分词匹配 + 本地缓存 + AI 兜底
无需启动 ZError，直接读取其 SQLite 数据库

API:
  GET/POST /query       — 题库查询
  GET  /api/status      — 健康检查
  POST /api/clear_cache — 清除缓存
"""
import json
import os
import re
import sqlite3
import sys
import threading
import time
import urllib.request
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from http.server import HTTPServer, BaseHTTPRequestHandler
import socketserver
from difflib import SequenceMatcher

# GBK 终端保护：替换 sys.stdout 防止 UnicodeEncodeError
if sys.stdout and hasattr(sys.stdout, 'encoding') and sys.stdout.encoding and sys.stdout.encoding.upper() == 'GBK':
    _orig_stdout = sys.stdout
    _orig_fd = None
    try:
        _orig_fd = _orig_stdout.fileno()
    except (OSError, ValueError):
        pass
    class _SafeStdout:
        encoding = 'utf-8'
        def write(self, msg):
            try:
                _orig_stdout.write(msg)
            except UnicodeEncodeError:
                _orig_stdout.write(msg.encode('gbk', errors='replace').decode('gbk'))
        def flush(self):
            try:
                _orig_stdout.flush()
            except (OSError, ValueError):
                pass
        def isatty(self):
            return getattr(_orig_stdout, 'isatty', lambda: False)()
        def fileno(self):
            if _orig_fd is not None:
                return _orig_fd
            raise OSError()
    sys.stdout = _SafeStdout()

# 创建多线程HTTP服务器类
class ThreadedHTTPServer(socketserver.ThreadingMixIn, HTTPServer):
    allow_reuse_address = True
    daemon_threads = True

# 尝试导入 jieba（中文分词），不可用时回退到滑动窗口分词
try:
    import jieba
    _JIEBA_AVAILABLE = True
except ImportError:
    jieba = None
    _JIEBA_AVAILABLE = False

# ============================================================
# 数据库层
# ============================================================

def _get_base_dir():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(os.path.abspath(sys.executable))
    # 统一启动器在项目根目录，src/ 是子目录，数据库放 data/
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


DB_PATH = os.path.join(_get_base_dir(), "data", "题库缓存.db")

# 插入操作的线程锁，防止并发写入重复
_insert_lock = threading.Lock()


def _get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# ==== ZError 数据库自动检测 ====

def _detect_zerror_db_path():
    """自动检测 ZError 的 SQLite 数据库路径"""
    candidates = []

    # 环境变量覆盖
    env_path = os.environ.get("ZERROR_DB_PATH", "").strip()
    if env_path and os.path.isfile(env_path):
        return env_path

    # 标准路径: C:\Users\{username}\AppData\Local\ZError\airesponses.db
    try:
        username = os.environ.get("USERNAME", "") or os.environ.get("USER", "")
        if not username:
            userprofile = os.environ.get("USERPROFILE", "")
            if userprofile:
                username = os.path.basename(userprofile)
        if username:
            candidates.append(
                os.path.join("C:\\Users", username, "AppData", "Local", "ZError", "airesponses.db")
            )
    except Exception:
        pass

    # 也检查 LOCALAPPDATA 环境变量
    local_appdata = os.environ.get("LOCALAPPDATA", "")
    if local_appdata:
        candidates.append(os.path.join(local_appdata, "ZError", "airesponses.db"))

    # 同级目录 / EXE同目录
    here = _get_base_dir()
    candidates.append(os.path.join(here, "airesponses.db"))
    candidates.append(os.path.join(here, "..", "ZError-2.2.4", "airesponses.db"))

    for path in candidates:
        if os.path.isfile(path):
            return path
    return ""


_zerror_db_path = _detect_zerror_db_path()
_ZERROR_DB_AVAILABLE = bool(_zerror_db_path and os.path.isfile(_zerror_db_path))


def _zerror_get_conn():
    """获取 ZError 数据库连接"""
    if not _ZERROR_DB_AVAILABLE or not _zerror_db_path:
        return None
    conn = sqlite3.connect(_zerror_db_path)
    conn.row_factory = sqlite3.Row
    return conn


def refresh_zerror_db_path():
    """重新检测 ZError 数据库路径"""
    global _zerror_db_path, _ZERROR_DB_AVAILABLE
    _zerror_db_path = _detect_zerror_db_path()
    _ZERROR_DB_AVAILABLE = bool(_zerror_db_path and os.path.isfile(_zerror_db_path))
    return _ZERROR_DB_AVAILABLE


def init_database():
    conn = _get_conn()
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS Folders (
            Id INTEGER PRIMARY KEY AUTOINCREMENT,
            Name TEXT NOT NULL,
            ParentId INTEGER DEFAULT 0,
            CreateTime DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        INSERT OR IGNORE INTO Folders (Id, Name, ParentId) VALUES (0, '默认文件夹', 0);

        CREATE TABLE IF NOT EXISTS AIResponses (
            Id INTEGER PRIMARY KEY AUTOINCREMENT,
            Question TEXT NOT NULL,
            Options TEXT,
            QuestionType TEXT,
            Answer TEXT NOT NULL,
            CreateTime DATETIME DEFAULT CURRENT_TIMESTAMP,
            FolderId INTEGER DEFAULT 0,
            FolderName TEXT DEFAULT '默认文件夹',
            IsAi BOOLEAN DEFAULT 1,
            IsPendingCorrection BOOLEAN DEFAULT 0
        );

        CREATE INDEX IF NOT EXISTS idx_airesponses_question ON AIResponses(Question);
        CREATE INDEX IF NOT EXISTS idx_airesponses_folder ON AIResponses(FolderId);
    """)
    conn.commit()
    conn.close()


def _normalize_for_dedup(text):
    """规范化文本用于去重比较：去空格、统一标点、转小写"""
    if not text:
        return ""
    text = text.strip().lower()
    text = re.sub(r'\s+', '', text)
    text = text.replace('\n', '').replace('\r', '')
    text = text.replace('&nbsp;', ' ')
    text = re.sub(r"[，、；：。！？【】《》\"\"''（）…—·\s]+", ' ', text)
    return text.strip()


def check_question_exists(question, options=None):
    """检查题目是否已存在（去重）
    
    增强版：
    1. 精确匹配（Question 文本完全一致）
    2. 规范化匹配（去除格式差异后匹配）
    3. 选项辅助匹配
    4. 模糊匹配兜底（使用 _compute_match_score）
    
    返回值：(exists: bool, existing_answer: str or None)
    """
    if not question:
        return False, None
    
    conn = _get_conn()
    cursor = conn.cursor()
    
    # 1. 精确匹配 Question
    cursor.execute(
        "SELECT Answer, Options FROM AIResponses WHERE Question = ?",
        (question,)
    )
    row = cursor.fetchone()
    if row:
        conn.close()
        return True, row[0]
    
    # 2. 规范化匹配（去除空白、标点差异后再比较）
    q_norm = _normalize_for_dedup(question)
    if q_norm:
        cursor.execute(
            "SELECT Question, Answer, Options FROM AIResponses"
        )
        all_rows = cursor.fetchall()
        for r in all_rows:
            db_q = r[0]
            db_answer = r[1]
            if _normalize_for_dedup(db_q) == q_norm:
                conn.close()
                return True, db_answer
    
    # 3. 如果提供了选项，尝试 Question + Options 精确匹配
    if options:
        cursor.execute(
            "SELECT Answer FROM AIResponses WHERE Question = ? AND Options = ?",
            (question, options)
        )
        row = cursor.fetchone()
        if row:
            conn.close()
            return True, row[0]
        
        # 规范化后匹配 Question + Options
        opts_norm = _normalize_for_dedup(options)
        if q_norm and opts_norm:
            cursor.execute(
                "SELECT Question, Options, Answer FROM AIResponses WHERE Options IS NOT NULL"
            )
            all_rows = cursor.fetchall()
            for r in all_rows:
                db_q_norm = _normalize_for_dedup(r[0])
                db_opts_norm = _normalize_for_dedup(r[1])
                db_answer = r[2]
                if db_q_norm == q_norm and db_opts_norm == opts_norm:
                    conn.close()
                    return True, db_answer
    
    # 4. 模糊匹配兜底：用 _compute_match_score 检查高相似度题目
    if q_norm:
        cursor.execute(
            "SELECT Question, Answer FROM AIResponses"
        )
        all_rows = cursor.fetchall()
        for r in all_rows:
            db_q = r[0]
            db_answer = r[1]
            score = _compute_match_score(question, db_q)
            if score >= 0.95:  # 95% 以上相似度视为重复
                conn.close()
                return True, db_answer
    
    conn.close()
    return False, None


def insert_answer(question, answer, options=None, question_type=None, is_ai=True, skip_duplicate=True):
    """插入答案到题库（线程安全）
    
    参数：
        question: 题目文本
        answer: 答案文本
        options: 选项（可选）
        question_type: 题目类型（可选）
        is_ai: 是否为AI生成的答案
        skip_duplicate: 是否跳过重复题目（默认True）
    
    返回值：row_id (0表示未插入)
    """
    if not answer or not answer.strip():
        return 0
    
    with _insert_lock:
        if skip_duplicate:
            exists, existing_answer = check_question_exists(question, options)
            if exists:
                return 0
            
        conn = _get_conn()
        conn.execute(
            "INSERT INTO AIResponses (Question, Answer, Options, QuestionType, IsAi, CreateTime) "
            "VALUES (?, ?, ?, ?, ?, datetime('now'))",
            (question, answer, options, question_type, int(is_ai))
        )
        conn.commit()
        row_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.close()
    return row_id


def deduplicate_database():
    """清理数据库中的重复题目，保留最早录入的那条
    
    返回值：int — 删除的重复记录数
    """
    conn = _get_conn()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT Id, Question, Options FROM AIResponses ORDER BY CreateTime ASC
    """)
    all_rows = cursor.fetchall()
    
    seen = {}
    duplicate_ids = []
    
    for row in all_rows:
        row_id = row[0]
        q_norm = _normalize_for_dedup(row[1])
        opts_norm = _normalize_for_dedup(row[2]) if row[2] else ""
        key = (q_norm, opts_norm)
        
        if key in seen:
            duplicate_ids.append(row_id)
        else:
            seen[key] = row_id
    
    deleted = 0
    if duplicate_ids:
        for dup_id in duplicate_ids:
            cursor.execute("DELETE FROM AIResponses WHERE Id = ?", (dup_id,))
            deleted += 1
        conn.commit()
    
    conn.close()
    return deleted


def export_questions():
    """导出所有题目为JSON格式
    
    返回值：list[dict]
    """
    conn = _get_conn()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT Id, Question, Options, QuestionType, Answer, CreateTime, 
               FolderId, FolderName, IsAi, IsPendingCorrection
        FROM AIResponses
        ORDER BY CreateTime DESC
    """)
    
    questions = []
    for row in cursor.fetchall():
        questions.append({
            "id": row[0],
            "question": row[1],
            "options": row[2],
            "question_type": row[3],
            "answer": row[4],
            "create_time": row[5],
            "folder_id": row[6],
            "folder_name": row[7],
            "is_ai": bool(row[8]),
            "is_pending_correction": bool(row[9])
        })
    
    conn.close()
    return questions


def import_questions(questions_data, skip_existing=True):
    """批量导入题目（增强去重：批次内 + 数据库 + 规范化匹配）
    
    参数：
        questions_data: list[dict] - 题目列表
        skip_existing: bool - 是否跳过已存在的题目
    
    返回值：dict - {"total": int, "imported": int, "skipped": int, "errors": list}
    """
    result = {
        "total": len(questions_data),
        "imported": 0,
        "skipped": 0,
        "errors": []
    }
    
    conn = _get_conn()
    cursor = conn.cursor()
    
    # 批次内去重：用规范化后的 (question, options) 作为key
    seen_in_batch = set()
    
    for q in questions_data:
        try:
            question = q.get("question", "")
            answer = q.get("answer", "")
            options = q.get("options")
            question_type = q.get("question_type")
            is_ai = q.get("is_ai", True)
            
            if not question or not answer:
                result["errors"].append(f"题目或答案为空: {question[:30]}...")
                continue
            
            # 批次内去重：同一批导入中重复的题目跳过
            q_norm = _normalize_for_dedup(question)
            opts_norm = _normalize_for_dedup(options) if options else ""
            batch_key = (q_norm, opts_norm)
            if batch_key in seen_in_batch:
                result["skipped"] += 1
                continue
            seen_in_batch.add(batch_key)
            
            # 检查数据库中是否已存在
            if skip_existing:
                exists, _ = check_question_exists(question, options)
                if exists:
                    result["skipped"] += 1
                    continue
            
            # 插入题目
            cursor.execute(
                "INSERT INTO AIResponses (Question, Answer, Options, QuestionType, IsAi, CreateTime) "
                "VALUES (?, ?, ?, ?, ?, datetime('now'))",
                (question, answer, options, question_type, int(is_ai))
            )
            result["imported"] += 1
            
        except Exception as e:
            result["errors"].append(f"导入失败: {str(e)[:50]}")
    
    conn.commit()
    conn.close()
    
    return result


def get_statistics():
    """获取题库统计信息
    
    返回值：dict
    """
    conn = _get_conn()
    cursor = conn.cursor()
    
    # 总数
    cursor.execute("SELECT COUNT(*) FROM AIResponses")
    total = cursor.fetchone()[0]
    
    # AI答案数量
    cursor.execute("SELECT COUNT(*) FROM AIResponses WHERE IsAi = 1")
    ai_count = cursor.fetchone()[0]
    
    # 题库答案数量
    cursor.execute("SELECT COUNT(*) FROM AIResponses WHERE IsAi = 0")
    manual_count = cursor.fetchone()[0]
    
    # 待校正数量
    cursor.execute("SELECT COUNT(*) FROM AIResponses WHERE IsPendingCorrection = 1")
    pending_count = cursor.fetchone()[0]
    
    conn.close()
    
    return {
        "total": total,
        "ai_count": ai_count,
        "manual_count": manual_count,
        "pending_count": pending_count
    }


# ============================================================
# 文本匹配引擎（复刻 ZError Rust 端逻辑）
# ============================================================

# 与 ZError Rust 端 QUERY_STOPWORDS 完全一致
QUERY_STOPWORDS = {
    "的", "地", "得", "了", "着", "吗", "呢", "啊", "呀", "吧", "么", "嘛",
    "在", "是", "和", "与", "及", "或", "并", "且", "将", "把", "被", "由",
    "对", "于", "中", "上", "下", "请问", "哪里", "哪儿", "哪个", "哪种",
    "哪些", "什么", "怎么", "怎样", "如何", "为何", "为什么", "多少", "几",
    "一下", "以下", "下列", "题目", "选项", "答案", "内容", "说法", "图片",
    "图中", "名字", "名称", "城市", "国家", "地区", "地方",
}

QUESTION_OPTIONS_MATCH_KEYWORDS = {"以下", "下列", "下面", "下叙"}

_URL_RE = re.compile(r"https?://[^\s]+")

# 中文标点判定（与 Rust 端一致）
def _is_punctuation_or_space(c):
    if c.isspace() or c.isascii() and c in "!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~":
        return True
    return c in "，。！？、；：（）【】《》""''—…·"


def _should_require_option_match(title):
    return any(kw in title for kw in QUESTION_OPTIONS_MATCH_KEYWORDS)


def _normalize(text):
    if not text:
        return ""
    text = re.sub(r'[^一-鿿\w\s]', '', text)
    text = re.sub(r'\s+', '', text)
    return text.lower()


def _char_similarity(a, b):
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    return SequenceMatcher(None, a, b).ratio()


def _extract_urls(text):
    """提取 URL 列表（排序），与 Rust 端 extract_urls 一致"""
    urls = _URL_RE.findall(text)
    cleaned = []
    for u in urls:
        u = u.rstrip("，。！？、；：（）【】《》")
        cleaned.append(u)
    cleaned.sort()
    return cleaned


def _normalize_urls(text):
    """替换 URL 为 __URL__ 占位符"""
    return _URL_RE.sub("__URL__", text)


def _extract_keywords(text):
    """
    提取关键词，优先使用 jieba（与 ZError 一致），
    不可用时回退到滑动窗口分词
    """
    normalized = _normalize(text)
    if not normalized:
        return set()

    if _JIEBA_AVAILABLE and jieba:
        raw_tokens = jieba.cut_for_search(normalized)
        keywords = []
        for token in raw_tokens:
            token = token.strip().lower()
            if not token or QUERY_STOPWORDS.issuperset({token}):
                continue
            if all(_is_punctuation_or_space(c) for c in token):
                continue
            char_count = len(token)
            if token.isascii() and token.isdigit():
                keywords.append(token)
            elif token.isascii() and token.isalpha():
                if char_count > 1:
                    keywords.append(token)
            else:
                if char_count > 1:
                    keywords.append(token)
        # 按长度降序排列（与 Rust 端一致），去子串
        keywords.sort(key=lambda k: len(k), reverse=True)
        compact = []
        for k in keywords:
            if any(k == e or (len(k) > 1 and k in e) for e in compact):
                continue
            compact.append(k)
        return set(compact)

    # 回退：滑动窗口分词
    text_lower = normalized.lower()
    keywords = set()
    for n in (2, 3, 4):
        for i in range(len(text_lower) - n + 1):
            w = text_lower[i:i + n]
            if w.isascii() and len(w) <= 2:
                continue
            keywords.add(w)
    result = {k for k in keywords if k not in QUERY_STOPWORDS}
    to_remove = set()
    for a in result:
        for b in result:
            if a != b and a in b:
                to_remove.add(a)
    result -= to_remove
    return result


def _keyword_coverage(query_keywords, candidate_keywords):
    if not query_keywords:
        return 1.0
    matched = len(query_keywords & candidate_keywords)
    return matched / len(query_keywords)


def _compute_match_score(query, candidate):
    """综合匹配评分，复刻 ZError 的 compute_query_match_score"""
    q_norm = _normalize_urls(query).strip().lower()
    c_norm = _normalize_urls(candidate).strip().lower()

    if not q_norm or not c_norm:
        return 0.0
    if q_norm == c_norm:
        return 1.0

    q_pure = _normalize(query)
    c_pure = _normalize(candidate)
    char_sim = _char_similarity(q_pure, c_pure)
    if char_sim < _match_config["threshold"]:
        return 0.0

    if char_sim >= _match_config["char_sim_fast_pass"]:
        return char_sim

    q_keywords = _extract_keywords(q_norm)
    if not q_keywords:
        return char_sim

    c_keywords = _extract_keywords(c_norm)
    coverage = _keyword_coverage(q_keywords, c_keywords)

    min_coverage = 1.0 if len(q_keywords) <= 2 else _match_config["keyword_coverage_min"]
    if coverage + 1e-9 < min_coverage:
        return 0.0

    tw = _match_config["title_weight"]
    ow = _match_config["options_weight"]
    return char_sim * tw + coverage * ow


# ============================================================
# 题库查询
# ============================================================

def _query_zerror_db(title, options=None):
    """直接查询 ZError 数据库（与 ZError Rust query_database 逻辑一致）"""
    if not _ZERROR_DB_AVAILABLE:
        return []

    conn = _zerror_get_conn()
    if not conn:
        return []

    try:
        rows = conn.execute(
            "SELECT Id, Question, Options, Answer, IsAi, COALESCE(IsPendingCorrection, 0) "
            "FROM AIResponses ORDER BY CreateTime DESC"
        ).fetchall()
    except Exception:
        conn.close()
        return []

    require_option_match = _should_require_option_match(title)
    query_urls = _extract_urls(title)
    has_options = bool(options and options.strip())

    scored = []
    for row in rows:
        db_id, db_q, db_opts, db_ans, db_is_ai, db_pending = row

        # URL 精确匹配（与 Rust 端一致）
        if query_urls:
            db_urls = _extract_urls(db_q)
            if query_urls != db_urls:
                continue

        title_sim = _compute_match_score(title, db_q)
        if title_sim == 0:
            continue

        if require_option_match:
            if not has_options or not db_opts:
                continue
            opt_sim = _compute_match_score(options, db_opts)
            if opt_sim == 0:
                continue
            sim = title_sim * _match_config["title_weight"] + opt_sim * _match_config["options_weight"]
        elif has_options and db_opts:
            opt_sim = _compute_match_score(options, db_opts)
            if opt_sim > 0:
                sim = title_sim * _match_config["title_weight"] + opt_sim * _match_config["options_weight"]
            else:
                sim = title_sim
        else:
            sim = title_sim

        if sim >= _match_config["threshold"]:
            scored.append((db_id, db_q, db_ans, bool(db_is_ai), bool(db_pending), sim))

    conn.close()
    scored.sort(key=lambda x: x[5], reverse=True)
    return [(s[0], s[1], s[2], s[3], s[4]) for s in scored[:50]]


def query_local(title, options=None):
    """本地缓存数据库模糊匹配"""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT Id, Question, Options, Answer, IsAi, COALESCE(IsPendingCorrection, 0) "
        "FROM AIResponses ORDER BY CreateTime DESC"
    ).fetchall()
    conn.close()

    has_options = bool(options and options.strip())
    scored = []
    for row in rows:
        db_id, db_q, db_opts, db_ans, db_is_ai, db_pending = row

        title_sim = _compute_match_score(title, db_q)
        if title_sim == 0:
            continue

        if has_options and db_opts:
            opt_sim = _compute_match_score(options, db_opts)
            if opt_sim > 0:
                sim = title_sim * _match_config["title_weight"] + opt_sim * _match_config["options_weight"]
            else:
                sim = title_sim
        else:
            sim = title_sim

        if sim >= _match_config["threshold"]:
            scored.append((db_id, db_q, db_ans, bool(db_is_ai), bool(db_pending), sim))

    scored.sort(key=lambda x: x[5], reverse=True)
    return [(s[0], s[1], s[2], s[3], s[4]) for s in scored[:10]]


def _deduplicate_results(results, existing_ids=None):
    """去重：移除已存在的 id"""
    if existing_ids is None:
        existing_ids = set()
    unique = []
    for r in results:
        if r[0] not in existing_ids:
            unique.append(r)
            existing_ids.add(r[0])
    return unique


# ============================================================
# ZError 远程后端（可选）
# ============================================================

_remote_zerror_url = os.environ.get("QB_REMOTE_URL", "").strip()
_remote_admin_token = os.environ.get("QB_REMOTE_TOKEN", "").strip()
_REMOTE_TIMEOUT = 8


def configure_remote_zerror(url="", admin_token=""):
    """配置 ZError 远程题库 URL 和管理员令牌"""
    global _remote_zerror_url, _remote_admin_token
    _remote_zerror_url = (url or "").strip()
    _remote_admin_token = (admin_token or "").strip()
    if _remote_zerror_url and not _remote_zerror_url.startswith("http"):
        _remote_zerror_url = f"http://{_remote_zerror_url}"
    if not _remote_zerror_url.endswith("/query"):
        if _remote_zerror_url.endswith("/"):
            _remote_zerror_url = _remote_zerror_url.rstrip("/") + "/query"
        else:
            _remote_zerror_url = _remote_zerror_url + "/query"


def _call_remote_zerror(title, options=None):
    """调用 ZError 远程题库查询，返回 (answer, is_ai) 或 (None, False)"""
    if not _remote_zerror_url:
        return None, False
    try:
        params = {"title": title}
        if options:
            params["options"] = options
        body = json.dumps(params).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if _remote_admin_token:
            headers["Authorization"] = f"Bearer {_remote_admin_token}"
        req = urllib.request.Request(
            _remote_zerror_url,
            data=body,
            headers=headers,
            method="POST",
        )
        resp = urllib.request.urlopen(req, timeout=_REMOTE_TIMEOUT)
        data = json.loads(resp.read())

        if "code" in data and data.get("code") == 0 and data.get("data"):
            item = data["data"]
            if isinstance(item, list) and item:
                item = item[0]
            if isinstance(item, dict):
                answer = item.get("answer", "")
                is_ai = item.get("is_ai", False)
                if answer:
                    return answer, is_ai
    except Exception:
        pass
    return None, False


# ============================================================
# AI 回调 — 单模型 / 多模型并发
# ============================================================

_ai_config = {
    "enabled": False,
    "models": [],  # [{"type": "OPENAI", "url": "", "model": "", "api_key": ""}]
    "concurrent": True,
}

_auto_save = True

_match_config = {
    "threshold": 0.72,
    "keyword_coverage_min": 0.75,
    "char_sim_fast_pass": 0.85,
    "title_weight": 0.7,
    "options_weight": 0.3,
}


def configure_ai_models(models=None, enabled=None, concurrent=None):
    """配置 AI 模型列表（支持多模型并发）"""
    if models is not None:
        _ai_config["models"] = models
    if enabled is not None:
        _ai_config["enabled"] = enabled
    if concurrent is not None:
        _ai_config["concurrent"] = concurrent


def configure_ai(enabled=False, url="", model="", api_key="", ai_type=""):
    """兼容旧接口：单模型配置"""
    if enabled and (url or model or api_key or ai_type):
        _ai_config["enabled"] = enabled
        _ai_config["models"] = [{
            "type": ai_type or "OPENAI",
            "url": url or "",
            "model": model or "",
            "api_key": api_key or "",
        }]
    else:
        _ai_config["enabled"] = enabled


def configure_match(threshold=None, keyword_coverage_min=None, char_sim_fast_pass=None,
                    title_weight=None, options_weight=None):
    """配置匹配算法参数"""
    if threshold is not None:
        _match_config["threshold"] = float(threshold)
    if keyword_coverage_min is not None:
        _match_config["keyword_coverage_min"] = float(keyword_coverage_min)
    if char_sim_fast_pass is not None:
        _match_config["char_sim_fast_pass"] = float(char_sim_fast_pass)
    if title_weight is not None:
        _match_config["title_weight"] = float(title_weight)
    if options_weight is not None:
        _match_config["options_weight"] = float(options_weight)


def configure_auto_save(enabled=True):
    """配置是否自动保存 AI 答案到本地题库"""
    global _auto_save
    _auto_save = bool(enabled)


def get_match_config():
    return dict(_match_config)


def get_ai_config():
    return dict(_ai_config)


def get_auto_save():
    return _auto_save


def get_remote_zerror_config():
    return {"url": _remote_zerror_url, "admin_token": _remote_admin_token}


def _call_single_ai(model_cfg, prompt):
    """调用单个 AI 模型获取答案，返回 answer 或 None"""
    if not model_cfg.get("api_key"):
        return None

    ai_type = model_cfg.get("type", "OPENAI").upper()
    model = model_cfg.get("model", "")
    api_key = model_cfg.get("api_key", "")
    url = model_cfg.get("url", "")

    try:
        if ai_type in ("OPENAI", "DEEPSEEK", "CUSTOM"):
            api_url = url or "https://api.openai.com/v1/chat/completions"
            body = json.dumps({
                "model": model or "gpt-3.5-turbo",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.1,
            }).encode("utf-8")
            req = urllib.request.Request(api_url, data=body, headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            })
            resp = urllib.request.urlopen(req, timeout=60)
            data = json.loads(resp.read())
            raw = data["choices"][0]["message"]["content"]
        else:
            return None

        raw_clean = raw.strip()
        if raw_clean.startswith("```"):
            raw_clean = re.sub(r'^```\w*\n?', '', raw_clean)
            raw_clean = re.sub(r'\n?```$', '', raw_clean)
        try:
            result = json.loads(raw_clean)
            ans = result.get("answer", "")
            if ans:
                return ans
        except (json.JSONDecodeError, Exception):
            pass
        # JSON 解析失败，尝试从文本中提取 answer 字段
        m = re.search(r'"answer"\s*:\s*"([^"]*)"', raw_clean)
        if m:
            return m.group(1)
        # 直接返回清理后的文本（去掉可能的前缀说明）
        lines = [l.strip() for l in raw_clean.split('\n') if l.strip() and not l.strip().startswith('//')]
        if lines:
            return lines[-1]  # 取最后一行作为答案
        return None
    except Exception as e:
        print(f"[题库AI] 模型 {model or 'unknown'} 调用失败: {e}")
        return None


def _call_ai(title, options=None, query_type=None):
    """调用配置的 AI 获取答案 — 支持多模型并发"""
    if not _ai_config["enabled"] or not _ai_config.get("models"):
        return None

    type_hints = {
        "single": "这是单选题，请只返回正确选项的内容，不要返回选项字母。格式：{\"answer\":\"答案内容\"}",
        "multiple": "这是多选题，请返回所有正确选项的内容，用###分隔。格式：{\"answer\":\"答案1###答案2\"}",
        "judgement": "这是判断题，请只回答\"正确\"或\"错误\"。格式：{\"answer\":\"正确\"}",
        "completion": "这是填空题/简答题/名词解释。如果有多个空，用###分隔答案。格式：{\"answer\":\"答案\"}",
    }
    hint = type_hints.get(query_type, "请用JSON格式回答：{\"answer\":\"答案内容\"}")

    parts = [f"题目：{title}"]
    if options:
        parts.append(f"选项：{options}")
    parts.append(hint)
    prompt = "\n".join(parts)

    models = _ai_config["models"]

    if _ai_config.get("concurrent", True) and len(models) > 1:
        answers = {}
        with ThreadPoolExecutor(max_workers=min(len(models), 5)) as executor:
            futures = {executor.submit(_call_single_ai, m, prompt): m for m in models}
            for future in as_completed(futures):
                answer = future.result()
                if answer:
                    answers[answer] = answers.get(answer, 0) + 1
        if answers:
            best_answer = max(answers, key=answers.get)
            return best_answer
        return None
    else:
        for model_cfg in models:
            answer = _call_single_ai(model_cfg, prompt)
            if answer:
                return answer
        return None


# ============================================================
# HTTP 服务器
# ============================================================

class QuestionBankHandler(BaseHTTPRequestHandler):
    server_version = "QuestionBank/1.0"
    max_request_bytes = 2 * 1024 * 1024
    trusted_origin_re = re.compile(r"^https?://(?:127\.0\.0\.1|localhost)(?::\d+)?$")

    def log_message(self, format, *args):
        pass  # 静默日志

    def _send_json(self, data, status=200):
        try:
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            origin = self.headers.get("Origin", "")
            if origin and self.trusted_origin_re.fullmatch(origin):
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Vary", "Origin")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError, OSError) as e:
            pass  # 客户端已断开，忽略

    def do_OPTIONS(self):
        origin = self.headers.get("Origin", "")
        if not origin or not self.trusted_origin_re.fullmatch(origin):
            self._send_json({"error": "origin not allowed"}, 403)
            return
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type,Authorization")
        self.end_headers()

    def do_GET(self):
        origin = self.headers.get("Origin", "")
        if origin and not self.trusted_origin_re.fullmatch(origin):
            self._send_json({"error": "origin not allowed"}, 403)
            return
        path = urlparse(self.path).path
        if path == "/query":
            self._handle_query(is_get=True)
        elif path == "/api/status":
            self._send_json({"status": "running", "message": "题库服务器运行中"})
        else:
            self._send_json({"error": "not found"}, 404)

    def do_POST(self):
        origin = self.headers.get("Origin", "")
        if origin and not self.trusted_origin_re.fullmatch(origin):
            self._send_json({"error": "origin not allowed"}, 403)
            return
        length = int(self.headers.get("Content-Length", 0))
        if length < 0 or length > self.max_request_bytes:
            self._send_json({"error": "request body too large"}, 413)
            return
        body = self.rfile.read(length) if length else b""
        try:
            data = json.loads(body) if body else {}
        except json.JSONDecodeError:
            data = {}

        path = urlparse(self.path).path
        if path == "/query":
            self._handle_query(is_get=False, data=data)
        elif path == "/api/clear_cache":
            count = self._clear_cache()
            self._send_json({"success": True, "cleared": count})
        elif path == "/api/export":
            self._handle_export()
        elif path == "/api/import":
            self._handle_import(data)
        elif path == "/api/statistics":
            self._handle_statistics()
        elif path == "/api/deduplicate":
            self._handle_deduplicate()
        else:
            self._send_json({"error": "not found"}, 404)

    def _parse_query_params(self):
        """从 URL 解析查询参数"""
        from urllib.parse import urlparse, parse_qs
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        return {
            "title": params.get("title", [""])[0],
            "options": params.get("options", [None])[0],
            "type": params.get("type", [None])[0],
        }

    def _handle_query(self, is_get=False, data=None):
        try:
            if is_get:
                params = self._parse_query_params()
                title = params["title"]
                options = params.get("options")
                query_type = params.get("type")
            else:
                title = data.get("title", "") if data else ""
                options = data.get("options")
                query_type = data.get("query_type") or data.get("type")

            if not title:
                self._send_json({"success": False, "message": "title 不能为空"}, 400)
                return

            # 0. 内置 ZError 数据库查询（优先，无需启动 ZError 进程）
            zerror_results = _query_zerror_db(title, options)
            if zerror_results:
                data_list = [{
                    "id": r[0],
                    "question": r[1],
                    "answer": r[2],
                    "is_ai": r[3],
                    "is_pending_correction": r[4],
                    "source": "zerror",
                } for r in zerror_results]
                self._send_json({"success": True, "data": data_list})
                return

            # 1. 本地缓存查询
            results = query_local(title, options)

            if results:
                data_list = [{
                    "id": r[0],
                    "question": r[1],
                    "answer": r[2],
                    "is_ai": r[3],
                    "is_pending_correction": r[4],
                } for r in results]
                self._send_json({"success": True, "data": data_list})
                return

             # 1. 远程 ZError 题库查询
            remote_answer, remote_is_ai = _call_remote_zerror(title, options)
            if remote_answer:
                if _auto_save:
                    insert_answer(title, remote_answer, options, query_type, is_ai=remote_is_ai)
                self._send_json({
                    "success": True,
                    "data": [{
                        "id": 0,
                        "question": title,
                        "answer": remote_answer,
                        "is_ai": remote_is_ai,
                        "is_pending_correction": False,
                        "source": "remote_zerror",
                    }]
                })
                return

            # 2. AI 兜底
            ai_answer = _call_ai(title, options, query_type)
            if ai_answer:
                if _auto_save:
                    insert_answer(title, ai_answer, options, query_type, is_ai=True)
                self._send_json({
                    "success": True,
                    "data": [{
                        "id": 0,
                        "question": title,
                        "answer": ai_answer,
                        "is_ai": True,
                        "is_pending_correction": False,
                    }]
                })
            else:
                self._send_json({
                    "success": True,
                    "data": [],
                    "message": "未找到匹配结果"
                })
        except Exception as e:
            try:
                self._send_json({"success": False, "message": f"查询异常: {e}"}, 500)
            except Exception:
                pass
            print(f"[QB] 查询异常: {e}")
            import traceback
            traceback.print_exc()

    def _clear_cache(self):
        conn = _get_conn()
        conn.execute("DELETE FROM AIResponses")
        conn.commit()
        changes = conn.total_changes
        conn.close()
        return changes
    
    def _handle_export(self):
        """处理导出请求"""
        try:
            questions = export_questions()
            self._send_json({
                "success": True,
                "data": questions,
                "count": len(questions)
            })
        except Exception as e:
            self._send_json({"success": False, "message": f"导出失败: {e}"}, 500)
    
    def _handle_import(self, data):
        """处理导入请求"""
        try:
            if not data or "questions" not in data:
                self._send_json({"success": False, "message": "缺少 questions 字段"}, 400)
                return
            
            skip_existing = data.get("skip_existing", True)
            result = import_questions(data["questions"], skip_existing)
            
            self._send_json({
                "success": True,
                "result": result
            })
        except Exception as e:
            self._send_json({"success": False, "message": f"导入失败: {e}"}, 500)
    
    def _handle_statistics(self):
        """处理统计请求"""
        try:
            stats = get_statistics()
            self._send_json({
                "success": True,
                "data": stats
            })
        except Exception as e:
            self._send_json({"success": False, "message": f"获取统计失败: {e}"}, 500)

    def _handle_deduplicate(self):
        """处理去重请求"""
        try:
            deleted = deduplicate_database()
            self._send_json({
                "success": True,
                "deleted": deleted,
                "message": f"已清理 {deleted} 条重复记录"
            })
        except Exception as e:
            self._send_json({"success": False, "message": f"去重失败: {e}"}, 500)


class QuestionBankServer:
    """题库服务器管理器"""

    def __init__(self, port=8083, host="127.0.0.1"):
        self.port = port
        self.host = host
        self._server = None
        self._thread = None
        self._running = False

    @property
    def url(self):
        return f"http://{self.host}:{self.port}"

    @property
    def running(self):
        return self._running

    def start(self):
        if self._running:
            return
        try:
            init_database()
            refresh_zerror_db_path()
            # 使用 ThreadedHTTPServer 支持并发请求
            self._server = ThreadedHTTPServer((self.host, self.port), QuestionBankHandler)
            self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
            self._thread.start()
            # 等待服务器开始监听
            time.sleep(0.5)
            # 验证端口是否真正监听
            import socket
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1)
            result = sock.connect_ex((self.host, self.port))
            sock.close()
            if result == 0:
                self._running = True
                print(f"[QB] 服务器已启动: http://{self.host}:{self.port}")
                print(f"[QB] 端口 {self.port} 验证成功")
            else:
                self.stop()
                raise OSError(f"端口 {self.port} 验证失败，错误码 {result}")
        except Exception as e:
            print(f"[QB] 服务器启动失败: {e}")
            import traceback
            traceback.print_exc()
            raise

    def stop(self):
        if self._server:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        self._running = False

    def health_check(self):
        """快速健康检查"""
        try:
            req = urllib.request.Request(f"{self.url}/api/status")
            resp = urllib.request.urlopen(req, timeout=5)
            return resp.status == 200
        except Exception as e:
            print(f"[QB] health_check failed: {e}")
            return False

    def get_stats(self):
        """获取题库统计"""
        stats = {"total": 0, "ai_cached": 0, "local": 0, "zerror_total": 0, "zerror_ai": 0, "zerror_available": _ZERROR_DB_AVAILABLE}
        try:
            conn = _get_conn()
            total = conn.execute("SELECT COUNT(*) FROM AIResponses").fetchone()[0]
            ai_count = conn.execute("SELECT COUNT(*) FROM AIResponses WHERE IsAi=1").fetchone()[0]
            conn.close()
            stats["total"] = total
            stats["ai_cached"] = ai_count
            stats["local"] = total - ai_count  # 非AI手动录入的本地题数
        except Exception:
            pass

        # ZError 数据库统计（包含其 AI 记录）
        if _ZERROR_DB_AVAILABLE:
            try:
                zconn = _zerror_get_conn()
                if zconn:
                    ztotal = zconn.execute("SELECT COUNT(*) FROM AIResponses").fetchone()[0]
                    zai = zconn.execute("SELECT COUNT(*) FROM AIResponses WHERE IsAi=1").fetchone()[0]
                    zconn.close()
                    stats["zerror_total"] = ztotal
                    stats["zerror_ai"] = zai
                    stats["total"] = stats["total"] + ztotal
                    stats["ai_cached"] = stats["ai_cached"] + zai  # 包含 ZError 的 AI 记录
            except Exception:
                pass

        return stats


# ============================================================
# 测试入口
# ============================================================

if __name__ == "__main__":
    server = QuestionBankServer(port=8083)
    server.start()
    print(f"题库服务器已启动: {server.url}")
    print("按 Ctrl+C 停止...")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        server.stop()
        print("已停止")
