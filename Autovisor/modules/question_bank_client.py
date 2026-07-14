# encoding=utf-8
"""题库 HTTP 客户端及答案解析纯函数。"""

from __future__ import annotations

import http.client
import json
import os
import re
import time
import traceback
from urllib.parse import urlsplit

from modules.logger import Logger


logger = Logger()
QB_URL = os.environ.get("QB_URL", "http://127.0.0.1:8083/query")
QB_TIMEOUT = 15


def _extract_last_balanced_json(text: str):
    """提取文本中最后一个完整 JSON 对象，忽略字符串内的花括号。"""
    if not text:
        return None
    depth = 0
    start = None
    in_string = False
    escaped = False
    last_object = None
    for index, char in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}" and depth > 0:
            depth -= 1
            if depth == 0 and start is not None:
                last_object = text[start:index + 1]
                start = None
    return last_object


def _question_bank_endpoint(qb_url: str | None = None):
    target_url = qb_url or QB_URL
    endpoint = urlsplit(target_url)
    if endpoint.scheme not in {"http", "https"} or not endpoint.hostname:
        raise ValueError(f"无效题库URL: {target_url}")
    port = endpoint.port or (443 if endpoint.scheme == "https" else 80)
    path = endpoint.path or "/query"
    if endpoint.query:
        path = f"{path}?{endpoint.query}"
    connection_class = (
        http.client.HTTPSConnection
        if endpoint.scheme == "https"
        else http.client.HTTPConnection
    )
    return connection_class, endpoint.hostname, port, path


def _normalize_qb(text):
    if not text:
        return ""
    text = re.sub(r"[^一-鿿\w\s]", "", text)
    return re.sub(r"\s+", "", text).lower()


def _match_option(answer_text, options):
    """将题库答案匹配到选项文本，返回匹配到的选项索引。"""
    if not answer_text or not options:
        return []
    try:
        parsed = json.loads(answer_text)
        if isinstance(parsed, dict):
            answer_text = str(parsed.get("answer", answer_text))
    except (json.JSONDecodeError, TypeError):
        pass

    parts = [part.strip() for part in str(answer_text).split("###") if part.strip()]
    if not parts:
        parts = [str(answer_text).strip()]
    judge_map = {
        "正确": "对",
        "错误": "错",
        "是": "对",
        "否": "错",
        "true": "对",
        "false": "错",
        "yes": "对",
        "no": "错",
        "right": "对",
        "wrong": "错",
    }

    matched_indices = []
    for part in parts:
        part_norm = _normalize_qb(judge_map.get(part.lower(), part))
        if not part_norm:
            continue
        best_index = -1
        best_score = 0
        for index, option in enumerate(options):
            if index in matched_indices:
                continue
            option_norm = _normalize_qb(option)
            if not option_norm:
                continue
            if part_norm in option_norm:
                score = len(part_norm) / max(len(option_norm), 1)
            elif option_norm in part_norm:
                score = len(option_norm) / max(len(part_norm), 1)
            else:
                continue
            if score > best_score:
                best_score = score
                best_index = index
                if score > 0.95:
                    break
        if best_index >= 0:
            matched_indices.append(best_index)

    if not matched_indices:
        for part in parts:
            part_upper = part.strip().upper()
            if len(part_upper) > 2:
                continue
            for index, option in enumerate(options):
                if index in matched_indices:
                    continue
                option_text = str(option).strip()
                if not option_text:
                    continue
                for prefix in (option_text[0], option_text[:2]):
                    if prefix.upper().rstrip(".、)） ") == part_upper:
                        matched_indices.append(index)
                        break
                if index in matched_indices:
                    break
    return matched_indices


def _detect_question_kind(query_type: str) -> str:
    if not query_type:
        return ""
    trimmed = query_type.strip().lower()
    if "single" in trimmed or "单选" in query_type or "单项选择" in query_type:
        return "single"
    if "multiple" in trimmed or "多选" in query_type or "多项选择" in query_type:
        return "multiple"
    if "judgement" in trimmed or "judgment" in trimmed or "判断" in query_type:
        return "judgement"
    if any(word in query_type for word in ("填空", "简答", "名词解释")) or "completion" in trimmed:
        return "completion"
    return ""


def _build_model_query_prompt(
    title: str,
    options_text: str = None,
    query_type: str = None,
) -> str:
    prompt = (
        "请先分析我给出的问题，给出简要的思考过程，如果问题比较复杂，给出详细思考过程。"
        '最后将答案用JSON的格式回答我，格式{"answer":"答案"}。'
        "如果是选择题，请返回对应选项的内容，不要返回选项字母或选项序号。"
    )
    kind = _detect_question_kind(query_type)
    if kind:
        kind_mapping = {
            "single": "单选",
            "multiple": "多选",
            "judgement": "判断",
            "completion": "填空",
        }
        prompt += f"题目类型：{kind_mapping.get(kind, kind)}题。"
        if kind == "single":
            prompt += "这是单选题，请返回正确选项的内容，不要返回选项字母、选项序号或无关说明。"
        elif kind == "multiple":
            prompt += '这是多选题，请返回所有正确选项的内容，不要返回选项字母、选项序号。如果有多个正确选项，请使用"###"连接每个选项内容。'
        elif kind == "judgement":
            prompt += '这是判断题，请只回答"正确"或"错误"，不要添加任何其他内容。'
        else:
            prompt += '这是一道填空题或者简答题，也有可能是名词解释。如果有多个空，请将每个空的答案使用"###"连接。'
    prompt += f"题目：{title}"
    if options_text and options_text.strip():
        prompt += f"，选项：{options_text.strip()}"
    return prompt


def _extract_answer_from_json(response_text: str) -> str:
    if not response_text:
        return ""
    cleaned = response_text.strip()
    if cleaned.startswith("```json"):
        cleaned = cleaned[7:]
    elif cleaned.startswith("```"):
        cleaned = cleaned[3:]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]
    cleaned = cleaned.strip()

    def extract(parsed):
        if not isinstance(parsed, dict):
            return None
        return parsed.get("answer") or parsed.get("anwser")

    try:
        answer = extract(json.loads(cleaned))
        if answer:
            return str(answer)
    except (json.JSONDecodeError, TypeError):
        pass

    json_text = _extract_last_balanced_json(cleaned)
    if json_text:
        try:
            answer = extract(json.loads(json_text))
            if answer:
                return str(answer)
        except (json.JSONDecodeError, TypeError):
            pass

    match = re.search(
        r'(?s)\{\s*"(?:answer|anwser)"\s*:\s*"(.*?)"[\s\S]*?\}',
        cleaned,
    )
    return match.group(1) if match else response_text.strip()


def _is_model_error(response_text: str) -> str:
    if not response_text:
        return ""
    cleaned = response_text.strip()
    if cleaned.startswith("```json"):
        cleaned = cleaned[7:]
    elif cleaned.startswith("```"):
        cleaned = cleaned[3:]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]
    cleaned = cleaned.strip()
    if cleaned.startswith(("错误:", "Error:")):
        return cleaned
    if '"error"' not in cleaned:
        return ""

    candidates = [cleaned]
    balanced = _extract_last_balanced_json(cleaned)
    if balanced and balanced != cleaned:
        candidates.append(balanced)
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(parsed, dict) and "error" in parsed:
            error = parsed["error"]
            if isinstance(error, dict) and "message" in error:
                return str(error["message"])
            return str(error)
    return ""


def query_question_bank(
    title,
    options_text=None,
    query_type=None,
    *,
    qb_url: str | None = None,
    timeout: int | float | None = None,
    max_retries: int = 5,
    sleep=time.sleep,
    logger_instance=None,
):
    """调用题库服务器查询答案，返回 ``(answer, is_ai)``。"""
    active_logger = logger_instance or logger
    target_url = qb_url or QB_URL
    request_timeout = QB_TIMEOUT if timeout is None else timeout
    params = {"title": title}
    if options_text:
        params["options"] = options_text
    if query_type:
        params["query_type"] = query_type
    body = json.dumps(params).encode("utf-8")
    connection_class, host, port, query_path = _question_bank_endpoint(target_url)

    for attempt in range(max_retries):
        connection = None
        response = None
        try:
            connection = connection_class(host, port, timeout=request_timeout)
            connection.request(
                "POST",
                query_path,
                body=body,
                headers={"Content-Type": "application/json", "Connection": "close"},
            )
            response = connection.getresponse()
            if response.status >= 400:
                active_logger.warn(f"题库返回错误状态码: {response.status}")
                if attempt < max_retries - 1:
                    wait = 2 ** attempt
                    active_logger.warn(
                        f"重试({attempt + 1}/{max_retries}, 等待{wait}s)"
                    )
                    sleep(wait)
                    continue
                return None, False

            data = json.loads(response.read().decode("utf-8"))
            if not isinstance(data, dict):
                active_logger.warn("题库返回格式错误：根节点不是对象")
                return None, False
            if data.get("code") == 0:
                active_logger.warn(
                    f"题库返回错误: {data.get('message', '未知错误')}"
                )
                return None, False

            raw_data = None
            if data.get("code") == 1 and data.get("data"):
                raw_data = data["data"]
            elif data.get("success") and data.get("data"):
                raw_data = data["data"]
            elif data.get("data"):
                raw_data = data["data"]
            items = [raw_data] if isinstance(raw_data, dict) else raw_data
            if not isinstance(items, list) or not items:
                active_logger.warn(f"题库未找到答案，响应: {str(data)[:100]}")
                return None, False
            item = items[0]
            if not isinstance(item, dict):
                active_logger.warn("题库返回格式错误：答案项不是对象")
                return None, False

            answer = item.get("answer", "")
            is_ai = bool(item.get("is_ai", False))
            if answer:
                error_message = _is_model_error(str(answer))
                if error_message:
                    active_logger.warn(f"模型返回错误: {error_message[:50]}")
                    return None, False
                answer = _extract_answer_from_json(str(answer))
            if answer and "题目不完整" in answer:
                active_logger.warn("AI检测到题目不完整")
                return None, False
            if answer and answer.strip():
                active_logger.info(f"题库查询成功: {answer[:50]}...")
                return answer, is_ai
            active_logger.warn("题库返回空答案")
            return None, False
        except (ConnectionRefusedError, ConnectionResetError, OSError) as exc:
            if attempt < max_retries - 1:
                wait = 2 ** attempt
                active_logger.warn(
                    f"题库连接失败(重试{attempt + 1}/{max_retries}, 等{wait}s): {exc}"
                )
                sleep(wait)
            else:
                active_logger.warn(f"题库连接失败: {exc} (URL: {target_url})")
                active_logger.write_log(
                    f"题库连接详细错误: {traceback.format_exc()[:300]}\n"
                )
        except Exception as exc:
            if attempt < max_retries - 1:
                wait = 2 ** attempt
                active_logger.write_log(
                    f"题库查询异常(重试{attempt + 1}/{max_retries}, 等{wait}s): {exc}\n"
                )
                sleep(wait)
            else:
                active_logger.write_log(f"题库查询异常: {exc}\n")
                active_logger.write_log(
                    f"详细错误: {traceback.format_exc()[:300]}\n"
                )
        finally:
            if response:
                try:
                    response.close()
                except Exception:
                    pass
            if connection:
                try:
                    connection.close()
                except Exception:
                    pass
    return None, False

