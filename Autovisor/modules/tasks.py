import asyncio
import json
import os
import random
import re
import traceback
import http.client
from urllib.parse import urlsplit

from playwright.async_api import Page
from pygetwindow import Win32Window
from modules.configs import Config
from modules.utils import get_video_attr, display_window, get_browser_window, hide_window, is_playwright_window
from playwright._impl._errors import TargetClosedError
from modules.logger import Logger
from modules.floating_widget import inject_widget, update_widget_question

logger = Logger()


def _extract_last_balanced_json(text: str):
    """从文本末尾提取最后一个平衡的 JSON 对象片段"""
    bytes_text = text.encode('utf-8')
    end = None
    depth = 0
    i = len(bytes_text)
    while i > 0:
        i -= 1
        b = bytes_text[i]
        if end is None:
            if b == ord('}'):
                end = i
                depth = 1
                continue
        else:
            if b == ord('}'):
                depth += 1
            elif b == ord('{'):
                depth -= 1
                if depth == 0:
                    start = i
                    return text[start:end+1]
    return None


def clean_html_tags(text):
    """清理HTML标签和特殊字符
    
    将HTML内容转换为纯文本显示
    """
    if not text:
        return ""
    
    # 去除HTML标签
    text = re.sub(r'<[^>]+>', '', text)
    
    # 替换HTML实体
    html_entities = {
        '&nbsp;': ' ',
        '&amp;': '&',
        '&lt;': '<',
        '&gt;': '>',
        '&quot;': '"',
        '&apos;': "'",
        '&#39;': "'",
    }
    
    for entity, char in html_entities.items():
        text = text.replace(entity, char)
    
    # 处理括号内的空格占位符：(&nbsp; &nbsp;) -> （）
    text = re.sub(r'\(\s+\)', '（）', text)
    text = re.sub(r'\(\s*\)', '（）', text)
    
    # 去除多余空白
    text = re.sub(r'\s+', ' ', text)
    
    return text.strip()

async def smart_click_text(page, text: str) -> bool:
    selectors = ['div', 'span', 'p', 'label', 'a', 'li', 'button', '[role="option"]']

    # 方法1：JS 直接精确匹配 textContent
    try:
        clicked = await page.evaluate(f'''
            (function() {{
                const selectors = {selectors};
                for (const sel of selectors) {{
                    for (const el of document.querySelectorAll(sel)) {{
                        if (el.textContent.trim() === '{text}') {{
                            el.click();
                            return true;
                        }}
                    }}
                }}
                return false;
            }})()
        ''')
        if clicked:
            logger.info(f"JS方式点击成功: {text}")
            return True
    except Exception as e:
        logger.debug(f"JS方式失败: {e}")

    # 方法2：XPath 精确匹配直接文本节点
    try:
        await page.click(f'xpath=//div[text()="{text}"]', timeout=2000)
        logger.info(f"XPath直接文本节点点击成功: {text}")
        return True
    except:
        pass

    # 方法3：XPath 包含文本（匹配子元素）
    try:
        await page.click(f'xpath=//div[contains(text(),"{text}")]', timeout=2000)
        logger.info(f"XPath包含文本点击成功: {text}")
        return True
    except:
        pass

    # 方法4：get_by_text + force
    try:
        await page.get_by_text(text, exact=True).click(force=True, timeout=2000)
        logger.info(f"force点击成功: {text}")
        return True
    except:
        pass

    logger.warn("[FAIL] 点击失败: %s" % text)
    return False

STATUS_PROBE_JS = r"""
() => {
    const normalize = (text) => (text || '').replace(/\s+/g, ' ').trim();
    const visibleText = [];
    for (const el of Array.from(document.querySelectorAll('body *'))) {
        if (visibleText.length >= 5) break;
        const text = normalize(el.textContent || '');
        if (!text) continue;
        if (!(text.includes('学习进度') || text.includes('掌握度') || text.includes('%'))) continue;
        const rect = el.getBoundingClientRect();
        const style = window.getComputedStyle(el);
        if (rect.width < 40 || rect.height < 16) continue;
        if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') continue;
        if (!visibleText.includes(text)) {
            visibleText.push(text.slice(0, 80));
        }
    }

    const titleSelectors = ['#lessonOrder', '.current_play [title]', '.current_play', 'h1', 'h2', '[title]'];
    let lessonTitle = '';
    for (const selector of titleSelectors) {
        const el = document.querySelector(selector);
        if (!el) continue;
        lessonTitle = normalize(el.getAttribute && el.getAttribute('title') ? el.getAttribute('title') : el.textContent);
        if (lessonTitle) break;
    }

    const video = document.querySelector('video');
    const currentTime = video ? Number(video.currentTime || 0) : null;
    const duration = video ? Number(video.duration || 0) : null;
    const percent = video && duration > 0 ? Math.floor((currentTime / duration) * 100) : null;
    return {
        pageTitle: normalize(document.title || ''),
        lessonTitle,
        hasVideo: !!video,
        paused: video ? !!video.paused : null,
        ended: video ? !!video.ended : null,
        currentTime,
        duration,
        percent,
        text: visibleText,
        url: location.href,
    };
}
"""

async def trigger_restart(page: Page, worker_name: str, restart_event: asyncio.Event | None = None) -> None:
    if restart_event is not None and restart_event.is_set():
        return
    if restart_event is not None:
        restart_event.set()
    logger.warn(f"[{worker_name}] 触发强制重建，立即关闭当前上下文.", shift=True)
    try:
        await page.context.close()
    except Exception:
        pass

async def status_ocr_stream(
    page: Page,
    worker_name: str,
    interval_sec: int = 5,
    restart_event: asyncio.Event | None = None,
) -> None:
    await page.wait_for_load_state("domcontentloaded")
    while True:
        try:
            await asyncio.sleep(interval_sec)
            data = await page.evaluate(STATUS_PROBE_JS)
            if data["hasVideo"]:
                cur = 0 if data["currentTime"] is None else data["currentTime"]
                dur = 0 if data["duration"] is None else data["duration"]
                logger.info(
                    f"[{worker_name}/OCR] {data['lessonTitle'] or data['pageTitle']} | "
                    f"{data['percent'] if data['percent'] is not None else 0}% | "
                    f"paused={data['paused']} | ended={data['ended']} | {cur:.0f}/{dur:.0f}s"
                )
                if data["ended"]:
                    logger.warn(f"[{worker_name}/OCR] 检测到 ended=True，等待 worker 重建.", shift=True)
                    await trigger_restart(page, worker_name, restart_event)
            elif data["text"]:
                logger.info(f"[{worker_name}/OCR] {' | '.join(data['text'][:3])}")
        except TargetClosedError:
            logger.write_log(f"{worker_name} status stream offline.\n")
            return
        except Exception:
            continue

# ============================================================
# 题库查询
# ============================================================

QB_URL = os.environ.get("QB_URL", "http://127.0.0.1:8083/query")
QB_TIMEOUT = 15


def _question_bank_endpoint():
    """解析题库地址，不在模块导入阶段发起网络连接。"""
    endpoint = urlsplit(QB_URL)
    if endpoint.scheme not in {"http", "https"} or not endpoint.hostname:
        raise ValueError(f"无效题库URL: {QB_URL}")
    port = endpoint.port or (443 if endpoint.scheme == "https" else 80)
    path = endpoint.path or "/query"
    if endpoint.query:
        path = f"{path}?{endpoint.query}"
    connection_class = (
        http.client.HTTPSConnection if endpoint.scheme == "https" else http.client.HTTPConnection
    )
    return connection_class, endpoint.hostname, port, path


def _normalize_qb(text):
    """去除标点和多余空白，统一小写"""
    if not text:
        return ""
    text = re.sub(r'[^一-鿿\w\s]', '', text)
    text = re.sub(r'\s+', '', text)
    return text.lower()


def _match_option(answer_text, options):
    """将题库答案匹配到选项文本，返回匹配到的选项索引列表"""
    if not answer_text or not options:
        return []

    # 先尝试 JSON 解析
    ans_parsed = None
    try:
        parsed = json.loads(answer_text)
        if isinstance(parsed, dict):
            ans_parsed = parsed.get("answer", answer_text)
    except (json.JSONDecodeError, Exception):
        pass
    
    if ans_parsed:
        answer_text = str(ans_parsed)
    
    # 分割多选答案 (用 ### 分隔)
    parts = [p.strip() for p in answer_text.split("###") if p.strip()]
    if not parts:
        parts = [answer_text.strip()]
    
    # 常见判断题答案标准化
    judge_map = {"正确": "对", "错误": "错", "是": "对", "否": "错", "true": "对", "false": "错",
                 "yes": "对", "no": "错", "right": "对", "wrong": "错"}

    matched_indices = []
    for part in parts:
        if not part:
            continue
        
        # 判断题特殊处理
        part_mapped = judge_map.get(part.lower(), part)
        
        part_norm = _normalize_qb(part_mapped)
        if not part_norm:
            continue

        best_idx = -1
        best_score = 0
        for i, opt in enumerate(options):
            if i in matched_indices:
                continue
            opt_norm = _normalize_qb(opt)
            if not opt_norm:
                continue
            # 选项含答案文本 或 答案含选项文本
            if part_norm in opt_norm:
                score = len(part_norm) / max(len(opt_norm), 1)
            elif opt_norm in part_norm:
                score = len(opt_norm) / max(len(part_norm), 1)
            else:
                continue
            if score > best_score:
                best_score = score
                best_idx = i
                if score > 0.95:
                    break

        if best_idx >= 0:
            matched_indices.append(best_idx)

    # 如果按文本匹配不到，尝试匹配 ABCD/123 等选项字母
    if not matched_indices:
        for part in parts:
            part_upper = part.strip().upper()
            if len(part_upper) <= 2:
                for i, opt in enumerate(options):
                    if i in matched_indices:
                        continue
                    opt_stripped = opt.strip()
                    # 选项格式 "A.xxx" "A、xxx" "A)" "A " "(A)"
                    for prefix in [opt_stripped[0], opt_stripped[:2]]:
                        cleaned = prefix.upper().rstrip(".、)） ")
                        if cleaned == part_upper:
                            matched_indices.append(i)
                            break
                    if i in matched_indices:
                        break

    return matched_indices


def _detect_question_kind(query_type: str) -> str:
    """检测题目类型（参考ZError-2.2.4的实现）"""
    if not query_type:
        return ""
    
    trimmed = query_type.strip().lower()
    
    if "single" in trimmed or "单选" in query_type or "单项选择" in query_type:
        return "single"
    elif "multiple" in trimmed or "多选" in query_type or "多项选择" in query_type:
        return "multiple"
    elif "judgement" in trimmed or "judgment" in trimmed or "判断" in query_type:
        return "judgement"
    elif "completion" in trimmed or "填空" in query_type or "简答" in query_type or "名词解释" in query_type:
        return "completion"
    return ""


def _build_model_query_prompt(title: str, options_text: str = None, query_type: str = None) -> str:
    """构建AI查询提示词（参考ZError-2.2.4的build_model_query_prompt）"""
    q = "请先分析我给出的问题，给出简要的思考过程，如果问题比较复杂，给出详细思考过程。最后将答案用JSON的格式回答我，格式{\"answer\":\"答案\"}。"
    q += "如果是选择题，请返回对应选项的内容，不要返回选项字母或选项序号。"
    
    kind = _detect_question_kind(query_type)
    if kind:
        kind_mapping = {
            "single": "单选",
            "multiple": "多选", 
            "judgement": "判断",
            "completion": "填空"
        }
        q += f"题目类型：{kind_mapping.get(kind, kind)}题。"
        
        if kind == "single":
            q += "这是单选题，请返回正确选项的内容，不要返回选项字母、选项序号或无关说明。"
        elif kind == "multiple":
            q += "这是多选题，请返回所有正确选项的内容，不要返回选项字母、选项序号。如果有多个正确选项，请使用\"###\"连接每个选项内容。"
        elif kind == "judgement":
            q += "这是判断题，请只回答\"正确\"或\"错误\"，不要添加任何其他内容。"
        elif kind == "completion":
            q += "这是一道填空题或者简答题，也有可能是名词解释。如果有多个空，请将每个空的答案使用\"###\"连接。"
    
    q += f"题目：{title}"
    
    if options_text and options_text.strip():
        q += f"，选项：{options_text.strip()}"
    
    return q


def _extract_answer_from_json(response_text: str) -> str:
    """从AI响应中提取答案（完全兼容ZError-2.2.4格式）
    
    提取策略（按优先级）：
    1. 去除markdown代码块标记（```json 或 ```）
    2. 直接解析整个文本为JSON
    3. 从末尾提取最后一个平衡的JSON对象片段
    4. 使用正则在混合文本中捕获answer字段
    5. 回退：返回原始内容
    
    支持处理拼写错误：answer 和 anwser
    """
    if not response_text:
        return ""
    
    # 1) 去除可能的 markdown 代码块标记
    cleaned = response_text.strip()
    if cleaned.startswith("```json"):
        cleaned = cleaned[7:]
    elif cleaned.startswith("```"):
        cleaned = cleaned[3:]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]
    cleaned = cleaned.strip()
    
    # 提取答案的内部工具函数
    def extract_field_from_value(parsed):
        if isinstance(parsed, dict):
            if "answer" in parsed and parsed["answer"]:
                return str(parsed["answer"])
            if "anwser" in parsed and parsed["anwser"]:  # 处理拼写错误
                return str(parsed["anwser"])
        return None
    
    # 2) 首先尝试直接解析整个文本为 JSON
    try:
        parsed = json.loads(cleaned)
        ans = extract_field_from_value(parsed)
        if ans:
            return ans
    except (json.JSONDecodeError, Exception):
        pass
    
    # 3) 失败则从末尾尝试提取最后一个平衡的 JSON 对象片段
    json_str = _extract_last_balanced_json(cleaned)
    if json_str:
        try:
            parsed = json.loads(json_str)
            ans = extract_field_from_value(parsed)
            if ans:
                return ans
        except (json.JSONDecodeError, Exception):
            pass
    
    # 4) 使用正则在混合文本中直接捕获 answer 字段
    pattern = r'(?s)\{\s*"(?:answer|anwser)"\s*:\s*"(.*?)"[\s\S]*?\}'
    match = re.search(pattern, cleaned)
    if match:
        ans = match.group(1)
        return ans
    
    # 5) 回退：返回原始内容
    return response_text.strip()


def _is_model_error(response_text: str) -> str:
    """检测AI响应是否包含错误（参考ZError-2.2.4的实现）
    
    返回值：
    - 空字符串：无错误
    - 非空字符串：错误信息
    """
    if not response_text:
        return ""
    
    # 去除可能的 markdown 代码块标记
    cleaned = response_text.strip()
    if cleaned.startswith("```json"):
        cleaned = cleaned[7:]
    elif cleaned.startswith("```"):
        cleaned = cleaned[3:]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]
    cleaned = cleaned.strip()
    
    # 检查错误前缀
    if cleaned.startswith("错误:") or cleaned.startswith("Error:"):
        return cleaned
    
    # 检查JSON格式的错误
    if "\"error\"" in cleaned:
        try:
            parsed = json.loads(cleaned)
            if isinstance(parsed, dict) and "error" in parsed:
                error = parsed["error"]
                if isinstance(error, dict) and "message" in error:
                    return str(error["message"])
                return str(error)
        except (json.JSONDecodeError, Exception):
            # 尝试从末尾提取JSON
            json_str = _extract_last_balanced_json(cleaned)
            if json_str:
                try:
                    parsed = json.loads(json_str)
                    if isinstance(parsed, dict) and "error" in parsed:
                        error = parsed["error"]
                        if isinstance(error, dict) and "message" in error:
                            return str(error["message"])
                        return str(error)
                except (json.JSONDecodeError, Exception):
                    pass
    
    return ""


def query_question_bank(title, options_text=None, query_type=None):
    """调用题库服务器查询答案，返回 (answer_text, is_ai) 或 (None, False)
    
    兼容多种响应格式:
    - ZError-2.2.4: {"code": 1, "data": [{"question": "...", "answer": "...", "is_ai": true}]}
    - ZError-2.2.4错误: {"code": 0, "message": "错误信息"} 或 HTTP状态码500/408
    - 题库服务器: {"success": true, "data": [{"answer": "...", "is_ai": false}]}
    - 其他自定义格式
    
    参考ZError-2.2.4的API设计：
    - 先查询数据库，未找到则调用AI模型
    - AI响应格式为JSON: {"answer":"答案"}
    - 多选题答案用"###"分隔
    - 检测到URL时发送视觉分析请求（带__URL_QUESTION__前缀）
    - 超时处理：普通题目30秒，URL题目120秒
    """
    params = {"title": title}
    if options_text:
        params["options"] = options_text
    if query_type:
        params["query_type"] = query_type
    body = json.dumps(params).encode("utf-8")
    connection_class, host, port, query_path = _question_bank_endpoint()
    
    max_retries = 5
    for attempt in range(max_retries):
        conn = None
        resp = None
        try:
            conn = connection_class(host, port, timeout=QB_TIMEOUT)
            conn.request(
                "POST",
                query_path,
                body=body,
                headers={"Content-Type": "application/json", "Connection": "close"},
            )
            resp = conn.getresponse()
            status_code = resp.status
            
            # 检查HTTP状态码
            if status_code >= 400:
                logger.warn(f"题库返回错误状态码: {status_code}")
                if attempt < max_retries - 1:
                    import time
                    wait = 2 ** attempt
                    logger.warn(f"重试({attempt+1}/{max_retries}, 等待{wait}s)")
                    time.sleep(wait)
                    continue
                return None, False
            
            data = json.loads(resp.read().decode("utf-8"))

            # 检查ZError错误格式: {"code": 0, "message": "..."}
            if "code" in data and data.get("code") == 0:
                error_msg = data.get("message", "未知错误")
                logger.warn(f"题库返回错误: {error_msg}")
                return None, False
            
            items = None
            # 兼容ZError格式: {"code": 1, "data": [...]}
            if "code" in data and data.get("code") == 1 and data.get("data"):
                raw_data = data["data"]
                items = [raw_data] if isinstance(raw_data, dict) else raw_data
            # 兼容旧版题库格式: {"success": true, "data": [...]}
            elif data.get("success") and data.get("data"):
                raw_data = data["data"]
                items = [raw_data] if isinstance(raw_data, dict) else raw_data
            # 兼容其他格式：直接包含data字段
            elif "data" in data and data.get("data"):
                raw_data = data["data"]
                items = [raw_data] if isinstance(raw_data, dict) else raw_data

            if items:
                item = items[0]
                answer = item.get("answer", "")
                is_ai = item.get("is_ai", False)
                
                # 检查答案是否包含模型错误
                if answer:
                    error_msg = _is_model_error(answer)
                    if error_msg:
                        logger.warn(f"模型返回错误: {error_msg[:50]}")
                        return None, False
                
                # 如果答案是JSON格式，提取answer字段
                if answer:
                    answer = _extract_answer_from_json(answer)
                
                # 检查是否为"题目不完整"响应
                if answer and "题目不完整" in answer:
                    logger.warn("AI检测到题目不完整")
                    return None, False
                
                if answer and answer.strip():
                    logger.info(f"题库查询成功: {answer[:50]}...")
                    return answer, is_ai
                logger.warn("题库返回空答案")
            else:
                logger.warn(f"题库未找到答案，响应: {str(data)[:100]}")
            return None, False
        except (ConnectionRefusedError, ConnectionResetError, OSError) as e:
            if attempt < max_retries - 1:
                import time
                wait = 2 ** attempt
                logger.warn(f"题库连接失败(重试{attempt+1}/{max_retries}, 等{wait}s): {e}")
                time.sleep(wait)
            else:
                logger.warn(f"题库连接失败: {e} (URL: {QB_URL})")
                import traceback
                logger.write_log(f"题库连接详细错误: {traceback.format_exc()[:300]}\n")
        except Exception as e:
            if attempt < max_retries - 1:
                import time
                wait = 2 ** attempt
                logger.write_log(f"题库查询异常(重试{attempt+1}/{max_retries}, 等{wait}s): {e}\n")
                time.sleep(wait)
            else:
                logger.write_log(f"题库查询异常: {e}\n")
                import traceback
                logger.write_log(f"详细错误: {traceback.format_exc()[:300]}\n")
        finally:
            if resp:
                try:
                    resp.close()
                except Exception:
                    pass
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass
    return None, False


async def task_monitor(tasks: list[asyncio.Task]) -> None:
    checked_tasks = set()
    logger.info("任务监控已启动.")
    while any(not task.done() for task in tasks):
        for task in tasks:
            if task.done() and task not in checked_tasks:
                checked_tasks.add(task)
                if task.cancelled():
                    continue
                try:
                    exc = task.exception()
                except asyncio.CancelledError:
                    continue
                if exc is None:
                    continue
                func_name = task.get_coro().__name__
                logger.error(f"任务函数{func_name} 出现异常.", shift=True)
                logger.write_log(f"{repr(exc)}\n")
        await asyncio.sleep(1)
    logger.info("任务监控已退出.", shift=True)


async def activate_window(page: Page) -> None:
    while True:
        try:
            await asyncio.sleep(2)
            window = await get_browser_window(page, retries=1, delay_ms=50)
            if window and is_playwright_window(window) and window.isMinimized:
                window.moveTo(-3200, -3200)
                await asyncio.sleep(0.3)
                window.restore()
                logger.info("检测到播放窗口最小化,已自动恢复.")
        except TargetClosedError:
            logger.write_log("浏览器已关闭,窗口激活模块已下线.\n")
            return
        except Exception as e:
            continue


async def video_optimize(page: Page, config: Config) -> None:
    await page.wait_for_load_state("domcontentloaded")
    click_counter = 0  # 计数器，用于控制点击频率
    first_set_rate = True  # 标记是否首次设置倍数
    while True:
        try:
            await asyncio.sleep(2)
            await page.wait_for_selector("video", state="attached", timeout=3000)
            volume = await get_video_attr(page, "volume")
            rate = await get_video_attr(page, "playbackRate")
            
            # 判断课程类型
            is_hike_class = "hike.zhihuishu.com" in page.url
            is_national_wisdom = "wisdom-mooc.zhihuishu.com" in page.url
            
            if is_hike_class or is_national_wisdom:
                # 智慧共享课每隔一段时间hover播放器保持显示
                click_counter += 1
                video_container = page.locator("#vjs_container")
                if await video_container.count() > 0:
                    await video_container.first.hover()

                if config.soundOff and volume != 0:
                    await page.evaluate(config.volume_none)

                # 全国智慧共享课不调倍速（按累计墙钟时间计分），翻转课正常调速
                if not is_national_wisdom and (rate != config.limitSpeed or click_counter >= 3):
                    click_counter = 0
                    # 点击对应倍速按钮（基于 .speedTab.speedTabXX 复合选择器）
                    speed_map = {
                        2.0: ".speedTab.speedTab20", 1.5: ".speedTab.speedTab15",
                        1.25: ".speedTab.speedTab10", 1.0: ".speedTab.speedTab05"
                    }
                    target_cls = speed_map.get(config.limitSpeed, ".speedTab.speedTab15")
                    speed_btn = page.locator(target_cls)
                    if await speed_btn.count() > 0:
                        await speed_btn.first.click()
                        await page.wait_for_timeout(200)
                        if first_set_rate:
                            logger.info(f"智慧共享课倍数已设置为 {config.limitSpeed}x")
                            first_set_rate = False
            
            else:
                # 普通课程处理
                if config.soundOff and volume != 0:
                    await page.evaluate(config.volume_none)
                    await page.evaluate(config.set_none_icon)
                
                if rate != config.limitSpeed:
                    await page.evaluate(config.revise_speed)
                    await page.evaluate(config.revise_speed_name)
                    if first_set_rate:
                        logger.info(f"倍数已设置为 {config.limitSpeed}x")
                        first_set_rate = False
                    
        except TargetClosedError:
            logger.write_log("浏览器已关闭,视频调节模块已下线.\n")
            return
        except Exception as e:
            continue


async def play_video(page: Page, config: Config) -> None:
    await page.wait_for_load_state("domcontentloaded")
    from modules.utils import APPLY_VIDEO_SETTINGS_JS
    limit_speed_default = config.limitSpeed
    while True:
        try:
            await asyncio.sleep(2)
            await page.wait_for_selector("video", state="attached", timeout=3000)
            paused = await page.evaluate("document.querySelector('video').paused")
            is_national_wisdom = "wisdom-mooc.zhihuishu.com" in page.url

            if paused:
                is_hike_class = "hike.zhihuishu.com" in page.url
                is_shared_class = is_hike_class or is_national_wisdom
                if is_shared_class:
                    ended = await page.evaluate("document.querySelector('video').ended")
                    currentTime = await page.evaluate("document.querySelector('video').currentTime")
                    duration = await page.evaluate("document.querySelector('video').duration")
                    
                    # 如果视频异常结束（ended=true 但 currentTime 很短），重置重新播放
                    if ended and duration > 0 and currentTime < min(duration * 0.1, 3):
                        logger.info("检测到视频异常结束(播放时间过短)，重置并重新播放.")
                        await page.evaluate("document.querySelector('video').currentTime = 0;")
                        await page.evaluate("Object.defineProperty(document.querySelector('video'), 'ended', { value: false, writable: true });")
                    elif ended:
                        # 正常播完，不需要处理，learning_loop 会检测到100%并退出
                        continue
            
                logger.info("检测到视频暂停,正在尝试播放.")
                
                # 先禁用 pause 方法，防止网站立即重新暂停视频
                limit_speed = 1.0 if is_national_wisdom else limit_speed_default
                await page.evaluate(config.remove_pause)
                
                result = await page.evaluate(
                    APPLY_VIDEO_SETTINGS_JS,
                    {"speed": limit_speed, "mute": config.soundOff},
                )
                if result and result['success']:
                    logger.info(f"视频设置已应用: 倍速={result['playbackRate']}x, 静音={result['muted']}")
                
                # 使用 Promise 处理 play() 调用，确保正确处理播放失败
                play_result = await page.evaluate('''async () => {
                    const video = document.querySelector('video');
                    if (!video) return { success: false, error: 'video not found' };
                    try {
                        await video.play();
                        return { success: true, paused: video.paused, error: null };
                    } catch (e) {
                        return { success: false, error: e.message || 'play failed' };
                    }
                }''')
                
                if play_result['success']:
                    logger.write_log(f"视频已恢复播放（静音+{limit_speed}x倍速）.\n")
                else:
                    logger.warn(f"视频播放失败: {play_result['error']}")
            else:
                # 视频未暂停但可能"假播放"（paused=false 但 currentTime 不增加）
                if is_national_wisdom:
                    try:
                        status = await page.evaluate('''() => {
                            const video = document.querySelector('video');
                            if (!video) return null;
                            return {
                                currentTime: video.currentTime,
                                duration: video.duration,
                                readyState: video.readyState,
                                networkState: video.networkState,
                                src: video.src || video.currentSrc || null
                            };
                        }''')
                        if status and status['duration'] > 0 and status['currentTime'] < 0.5:
                            await page.evaluate(config.remove_pause)
                            await page.evaluate('document.querySelector("video").play();')
                    except Exception:
                        pass
        except TargetClosedError:
            logger.write_log("浏览器已关闭,视频播放模块已下线.\n")
            return
        except Exception as e:
            continue


async def _extract_question_title(page: Page) -> str:
    """从页面提取当前题目文本"""
    try:
        title_el = await page.query_selector(".topic-title")
        if title_el:
            text = await title_el.text_content()
            return text.strip() if text else ""
    except Exception:
        pass
    # 备选: 尝试 .subject-title / .question-title
    for sel in (".subject-title", ".question-title", ".el-dialog__body"):
        try:
            el = await page.query_selector(sel)
            if el:
                text = await el.text_content()
                return text.strip()[:500] if text else ""
        except Exception:
            continue
    return ""


async def _extract_options(page: Page):
    """从页面提取选项文本列表"""
    options = []
    try:
        items = await page.query_selector_all(".topic-item")
        for item in items:
            text = await item.text_content()
            if text:
                options.append(text.strip())
    except Exception:
        pass
    return options


async def _extract_wisdom_question(page: Page):
    """提取智慧课题目信息"""
    try:
        question_info = page.locator(".question-info")
        if await question_info.count() > 0:
            text = await question_info.first.text_content()
            return text.strip() if text else ""
    except Exception:
        pass
    return ""

async def _extract_wisdom_options(page: Page):
    """提取智慧课选项"""
    options = []
    try:
        items = page.locator(".option")
        count = await items.count()
        for i in range(count):
            item = items.nth(i)
            letter_el = item.locator(".class-question-select")
            answer_el = item.locator(".answer")
            if await letter_el.count() > 0 and await answer_el.count() > 0:
                letter = await letter_el.text_content()
                answer = await answer_el.text_content()
                options.append(f"{letter}. {answer}")
    except Exception:
        pass
    return options

async def skip_questions(page: Page, event_loop) -> None:
    await page.wait_for_load_state("domcontentloaded")
    last_question_hash = 0
    while True:
        try:
            if "wisdom-mooc.zhihuishu.com" in page.url:
                await asyncio.sleep(2)
                try:
                    ai_dialog = page.locator(".ai-class-exercise-dialog")
                    if await ai_dialog.count() > 0 and await ai_dialog.is_visible():
                        logger.info("智慧课 - 检测到AI随堂练习弹窗")

                        try:
                            close_btn = ai_dialog.locator(".header-icon").first
                            if await close_btn.count() > 0 and await close_btn.is_visible():
                                await close_btn.click(timeout=1000)
                                await page.wait_for_timeout(800)
                                if await ai_dialog.count() == 0 or not await ai_dialog.is_visible():
                                    logger.info("智慧课 - 直接关闭弹窗成功")
                                    event_loop.set()
                                    continue
                        except Exception:
                            pass

                        logger.info("智慧课 - 弹窗无法直接关闭，随机作答")
                        try:
                            options = ai_dialog.locator(".option")
                            opt_count = await options.count()
                            if opt_count > 0:
                                rand_idx = random.randint(0, opt_count - 1)
                                await options.nth(rand_idx).click(timeout=1000)
                                logger.info(f"智慧课 - 随机选择第 {rand_idx + 1} 个选项")
                                await page.wait_for_timeout(500)
                        except Exception as e:
                            logger.warn(f"智慧课 - 选择选项失败: {repr(e)[:60]}")

                        try:
                            submit_btn = ai_dialog.locator("button.btn, .dialog-footer button").first
                            if await submit_btn.count() > 0 and await submit_btn.is_visible():
                                await submit_btn.click(timeout=2000)
                                logger.info("智慧课 - 已点击提交作答")
                                await page.wait_for_timeout(1000)
                        except Exception as e:
                            logger.warn(f"智慧课 - 提交作答失败: {repr(e)[:60]}")

                        try:
                            await page.wait_for_timeout(500)
                            if await ai_dialog.count() > 0 and await ai_dialog.is_visible():
                                close_btn = ai_dialog.locator(".header-icon").first
                                if await close_btn.count() > 0:
                                    await close_btn.click(timeout=1000)
                                    logger.info("智慧课 - 提交后关闭弹窗")
                                else:
                                    await page.keyboard.press("Escape")
                                    logger.info("智慧课 - ESC关闭弹窗")
                        except Exception:
                            await page.keyboard.press("Escape")

                        event_loop.set()
                except TargetClosedError:
                    logger.write_log("浏览器已关闭,答题模块已下线.\n")
                    return
                except Exception:
                    pass
                continue
            
            if "hike.zhihuishu.com" in page.url:
                logger.warn("当前课程为新版本,不支持自动答题.", shift=True)
                return
            await asyncio.sleep(2)
            ques_element = await page.wait_for_selector(".el-scrollbar__view", state="attached", timeout=1000)
            total_ques = await ques_element.query_selector_all(".number")
            if total_ques:
                logger.write_log(f"检测到{len(total_ques)}道题目.\n")

            for ques in total_ques:
                await ques.click(timeout=500)
                await page.wait_for_timeout(300)

                if await page.query_selector(".answer"):
                    continue  # 已作答

                # 提取题目和选项
                title = await _extract_question_title(page)
                option_texts = await _extract_options(page)

                if title and option_texts:
                    options_joined = "\n".join(option_texts)
                    answer, is_ai = query_question_bank(title, options_joined)

                    if answer:
                        source = "AI" if is_ai else "题库"
                        logger.write_log(f"[{source}] 匹配到答案: {answer[:80]}\n")
                        matched = _match_option(answer, option_texts)

                        if matched:
                            choices = await page.query_selector_all(".topic-item")
                            for idx in matched:
                                if idx < len(choices):
                                    await choices[idx].click(timeout=500)
                                    await page.wait_for_timeout(100)
                            continue

                # 题库未命中或无答案，回退到盲点前2个
                logger.write_log("未匹配到答案,使用默认策略.\n")
                choices = await page.query_selector_all(".topic-item")
                for each in choices[:2]:
                    await each.click(timeout=500)
                    await page.wait_for_timeout(100)

            await page.press(".el-dialog", "Escape", timeout=1000)
            event_loop.set()
        except TargetClosedError:
            logger.write_log("浏览器已关闭,答题模块已下线.\n")
            return
        except Exception as e:
            if "fusioncourseh5" in page.url:
                not_finish_close = await page.query_selector(".el-dialog")
                if not_finish_close:
                    await page.press(".el-dialog", "Escape", timeout=1000)
            elif "hike.zhihuishu.com" in page.url:
                logger.warn("当前课程为新版本,不支持自动答题.", shift=True)
                return
            else:
                not_finish_close = await page.query_selector(".el-message-box__headerbtn")
                if not_finish_close:
                    await not_finish_close.click()
            continue


async def wait_for_verify(page: Page, config, event_loop) -> None:
    await page.wait_for_load_state("domcontentloaded")
    while True:
        try:
            await asyncio.sleep(3)
            await page.wait_for_selector(".yidun_modal__title", state="attached", timeout=1000)
            logger.warn("检测到安全验证,请手动完成验证...", shift=True)
            if config.enableHideWindow:
                await display_window(page)
            await page.wait_for_selector(".yidun_modal__title", state="hidden", timeout=24 * 3600 * 1000)
            event_loop.set()
            if config.enableHideWindow:
                await hide_window(page)
            logger.info("安全验证已完成.", shift=True)
            await asyncio.sleep(30)  # 较长时间内不会再次触发验证
        except TargetClosedError:
            logger.write_log("浏览器已关闭,安全验证模块已下线.\n")
            return
        except Exception as e:
            continue


class TestResponseHandler:
    """测试响应处理器，用于在点击测试卡片前设置监听器"""
    def __init__(self):
        self.questions_data = None
        self.questions_event = asyncio.Event()
        self._handler = None
        self._context = None
        self._debug_count = 0
        self._source = None  # 记录数据来源: "doHomework" 或 "lookHomework"

    def _parse_questions_from_body(self, body):
        """从响应体中解析题目数据"""
        rt = body.get("rt")
        if not rt:
            return None
        exam_base = rt.get("examBase") or rt
        parts = exam_base.get("workExamParts", [])
        all_q = []
        for part in parts:
            for q in part.get("questionDtos", []):
                all_q.append({
                    "name": q.get("name", ""),
                    "type": (q.get("questionType") or {}).get("name", ""),
                    "type_id": (q.get("questionType") or {}).get("id"),
                    "options": [(o.get("id"), o.get("content", "")) for o in (q.get("questionOptions") or [])],
                    "score": q.get("questionScore", ""),
                    "eid": q.get("eid", ""),
                })
        return all_q if all_q else None

    @property
    def is_completed(self):
        """测试是否已完成（lookHomework表示已完成）"""
        return self._source == "lookHomework"

    def setup_listener(self, context, clear_data=True):
        """设置响应监听器 - context级别 + 所有page级别兜底"""
        self._context = context
        if clear_data:
            self.questions_data = None
            self.questions_event.clear()
            self._source = None
        self._debug_count = 0
        self._page_handlers = {}

        async def on_response(response):
            if self.questions_data:
                return
            self._debug_count += 1
            url = response.url
            is_target = any(kw in url for kw in ["doHomework", "lookHomework"])
            if is_target:
                logger.info(f"[响应监听] #{self._debug_count} 命中目标: {url[:100]}")
            
            if not is_target:
                return
            try:
                if response.status != 200:
                    logger.warn(f"目标响应状态码: {response.status}")
                    return
                    
                body = await response.json()
                logger.info(f"响应体keys: {list(body.keys())}")
                questions = self._parse_questions_from_body(body)
                if questions:
                    self.questions_data = questions
                    self._source = "doHomework" if "doHomework" in url else "lookHomework"
                    logger.info(f"从 {self._source} 拦截到 {len(self.questions_data)} 道题目")
                    self.questions_event.set()
                else:
                    logger.warn(f"响应中未找到题目数据 (keys: {list(body.keys())}, has rt: {'rt' in body})")
            except Exception as e:
                logger.warn(f"解析响应失败: {e}")

        self._handler = on_response

        context.on("response", self._handler)

        for p in context.pages:
            try:
                p.on("response", self._handler)
                self._page_handlers[id(p)] = p
                logger.info(f"已为页面注册监听器: {p.url[:60]}")
            except Exception:
                pass

        async def on_new_page(new_page):
            logger.info(f"新页面打开，注册监听器: {new_page.url[:60]}")
            new_page.on("response", self._handler)
            self._page_handlers[id(new_page)] = new_page

        context.on("page", on_new_page)
        self._new_page_handler = on_new_page

        logger.info(f"已设置响应监听器 (context + {len(self._page_handlers)} pages)")

    async def wait_for_questions(self, timeout: float = 25) -> bool:
        """等待题目数据，返回是否成功"""
        logger.info(f"开始等待题目数据，当前已捕获 {self._debug_count} 个响应")
        try:
            await asyncio.wait_for(self.questions_event.wait(), timeout=timeout)
            return self.questions_data is not None
        except asyncio.TimeoutError:
            logger.warn(f"等待响应超时({timeout}s)，共捕获 {self._debug_count} 个响应")
            return False

    def remove_listener(self):
        if self._context and self._handler:
            try:
                self._context.remove_listener("response", self._handler)
            except Exception:
                pass
            for pid, p in list(self._page_handlers.items()):
                try:
                    p.remove_listener("response", self._handler)
                except Exception:
                    pass
            self._page_handlers.clear()
            try:
                self._context.remove_listener("page", self._new_page_handler)
            except Exception:
                pass


async def handle_test_page(page: Page, questions_data: list, auto_submit: bool = False, manual_submit: bool = False) -> bool:
    """
    处理答题页面
    questions_data: 从 doHomework 获取的题目数据
    auto_submit: 是否自动提交（默认False，用户手动点击提交）
    
    注意：调用前 practice_loop 已确保试卷 DOM 已渲染
    """
    await page.wait_for_load_state("domcontentloaded")
    
    if not questions_data:
        logger.warn("[WARN] 没有题目数据")
        return False
    
    try:
        await inject_widget(page)
        logger.info("[OK] 浮动答题助手已注入")
    except Exception as e:
        logger.warn("[WARN] 浮动组件注入失败: %s" % str(e)[:50])
    
    option_selectors = [
        '.nodeLab',
        '.topic-item',
        '.option-item',
        '.el-radio',
        '.el-checkbox',
        '.exam-option',
        '[class*="option"]',
        '[class*="topic"]',
    ]
    max_retries = 3
    option_sel = None
    for retry in range(max_retries):
        for sel in option_selectors:
            try:
                await page.wait_for_selector(sel, timeout=5000, state='visible')
                option_sel = sel
                logger.info(f"[OK] 选项元素已加载: {sel}")
                break
            except Exception:
                continue
        if option_sel:
            break
        if retry < max_retries - 1:
            logger.warn(f"[RETRY] 等待选项加载失败，重试 {retry+1}/{max_retries}")
            await page.wait_for_timeout(3000)
        else:
            page_html = await page.content()
            logger.warn(f"页面HTML前500字符: {page_html[:500]}")
            logger.error(f"[ERROR] 无法加载选项元素")
            return False
    
    total = len(questions_data)
    logger.info("="*60)
    logger.info("[START] 开始答题，共 %d 道题" % total)
    logger.info("="*60)
    
    type_map = {"单选题": "single", "多选题": "multiple", "判断题": "judgement",
                "填空题": "completion", "简答题": "completion", "名词解释": "completion"}
    
    # 【阶段1】批量查询答案
    logger.info("\n[STEP1] 批量查询答案")
    for i, q in enumerate(questions_data):
        q_name = clean_html_tags(q["name"])
        q_type = q["type"]
        options = [clean_html_tags(o[1]) for o in q["options"]]
        options_joined = "\n".join(options)

        qtype_hint = type_map.get(q_type, None) or ("single" if q.get("type_id") == 1 else
                    "multiple" if q.get("type_id") == 2 else "judgement" if q.get("type_id") == 14 else None)

        answer, is_ai = query_question_bank(q_name, options_joined, qtype_hint)
        
        if answer:
            source = "AI" if is_ai else "题库"
            logger.info("\n[%s] 第%d题 [%s]" % (source, i+1, q_type))
            logger.info("题目: %s..." % q_name[:80])
            logger.info("答案: %s" % answer[:60])
            q["answer"] = answer
            q["is_ai"] = is_ai
        else:
            logger.info("\n[NO MATCH] 第%d题 [%s]" % (i+1, q_type))
            logger.info("题目: %s..." % q_name[:80])
            q["answer"] = None
            q["is_ai"] = False
    
    # 【阶段2】逐题答题
    logger.info("\n" + "="*60)
    logger.info("[STEP2] 逐题答题")
    logger.info("="*60)
    
    current_index = 0
    
    while current_index < total:
        i = current_index
        q = questions_data[i]
        q_name = clean_html_tags(q["name"])
        q_type = q["type"]
        answer = q.get("answer", "")
        raw_options = q["options"]
        options = [clean_html_tags(o[1]) for o in raw_options]
        is_ai = q.get("is_ai", False)
        
        logger.info("\n--- 第%d/%d题 [%s] ---" % (i+1, total, q_type))
        logger.info("题目: %s..." % q_name[:60])
        
        # 更新浮动组件显示
        try:
            widget_data = {
                "question": q_name,
                "answer": answer or "未找到答案",
                "options": options,
                "type": q_type,
                "index": i,
                "total": total,
                "isAI": is_ai
            }
            await page.evaluate(f'''
                if (window.AIAnswerWidget) {{
                    AIAnswerWidget.updateQuestion({json.dumps(widget_data, ensure_ascii=False)});
                }}
            ''')
        except Exception as e:
            pass
        
        if not auto_submit:
            # 手动模式：等待用户操作
            logger.info("[手动模式] 请在页面上选择答案")
            
            # 检测是否为多选题
            is_multiple = '多选' in q_type
            
            # 同时监听页面点击和浮动窗口按钮
            action = await wait_for_user_action(page, is_multiple, timeout=180)
            
            if action == "next":
                logger.info("[手动模式] 用户点击下一题")
                # 点击页面上的下一题按钮
                if i < total - 1:
                    await click_next_button(page)
                    await page.wait_for_timeout(300)
                current_index += 1
            elif action == "prev":
                logger.info("[手动模式] 用户点击上一题")
                # 点击页面上的上一题按钮（如果有）
                if i > 0:
                    await click_prev_button(page)
                    await page.wait_for_timeout(300)
                    current_index -= 1
            elif action == "option_click":
                logger.info("[手动模式] 用户选择了选项")
                # 单选题：选择后自动下一题
                if not is_multiple:
                    await page.wait_for_timeout(500)
                    if i < total - 1:
                        await click_next_button(page)
                        await page.wait_for_timeout(300)
                    current_index += 1
                else:
                    # 多选题：继续等待
                    pass
            elif action == "submit":
                logger.info("[手动模式] 用户点击提交试卷")
                break
            else:
                # 超时
                logger.warn("[手动模式] 超时，自动下一题")
                if i < total - 1:
                    await click_next_button(page)
                    await page.wait_for_timeout(300)
                current_index += 1
        else:
            # 自动模式：自动点击答案
            logger.info("[自动模式] 自动选择答案")
            if answer and answer.strip():
                logger.info("答案: %s" % answer[:40])
                await answer_question(page, answer, options, raw_options)
            else:
                logger.warn("[WARN] 答案为空或无法匹配，留空跳过（可手动补充）")
            
            await page.wait_for_timeout(500)
            if i < total - 1:
                await click_next_button(page)
                await page.wait_for_timeout(300)
            current_index += 1
    
    # 【阶段3】提交
    logger.info("\n" + "="*60)
    logger.info("[STEP3] 提交试卷")
    logger.info("="*60)

    if manual_submit:
        logger.info("[手动提交] 已跳过自动提交，请在页面上手动点击提交按钮")
        logger.info("[手动提交] 页面保持打开状态，请检查答案后手动提交")
        return True

    # 检查未答题比例，防止无答案自动交卷
    answered = sum(1 for q in questions_data if q.get("answer") and q["answer"].strip())
    unanswered = total - answered
    if unanswered > total * 0.5:
        logger.warn(f"\n[WARN] 未答题数 {unanswered}/{total} 超过 50%，跳过自动交卷，请手动检查后提交")
        logger.warn("[WARN] 如需强制自动交卷，请调整答题策略或配置 AI")
        return False

    success = await submit_exam(page)
    
    if success:
        logger.info("\n" + "="*60)
        logger.info("[DONE] 答题完成，准备继续课程学习")
        logger.info("="*60)
        
        # 关闭当前页面（测验窗口）
        try:
            await page.wait_for_timeout(1500)
            # 尝试关闭页面
            await page.evaluate('window.close()')
            logger.info("[OK] 已关闭测验页面")
        except Exception as e:
            logger.warn("[WARN] 关闭页面: %s" % str(e)[:30])
    else:
        logger.warn("\n" + "="*60)
        logger.warn("[FAIL] 答题提交失败")
        logger.warn("="*60)
    
    return success


async def wait_for_page_option_click(page: Page, timeout: float = 120) -> bool:
    """等待用户在页面上点击选项"""
    try:
        # 注入点击监听器
        await page.evaluate('''
            window._optionClicked = false;
            document.querySelectorAll('.nodeLab, .examquestions-answer, .flagChecked').forEach(el => {
                el.addEventListener('click', () => {
                    window._optionClicked = true;
                    console.log('选项被点击');
                }, { once: false });
            });
        ''')
        
        # 轮询检测点击
        start_time = asyncio.get_event_loop().time()
        while asyncio.get_event_loop().time() - start_time < timeout:
            clicked = await page.evaluate('window._optionClicked')
            if clicked:
                # 重置状态
                await page.evaluate('window._optionClicked = false')
                await page.wait_for_timeout(300)  # 等待点击效果
                return True
            await asyncio.sleep(0.2)
        return False
    except:
        return False


async def wait_for_user_action(page: Page, is_multiple: bool = False, timeout: float = 180) -> str:
    """等待用户操作，返回操作类型
    
    返回值：
    - "next": 用户点击了下一题按钮
    - "prev": 用户点击了上一题按钮
    - "option_click": 用户点击了页面选项
    - "submit": 用户点击了提交试卷
    - "timeout": 超时
    """
    try:
        # 重置所有状态
        await page.evaluate('''
            window._optionClicked = false;
            window._widgetNextClicked = false;
            window._widgetPrevClicked = false;
            
            // 注入选项点击监听
            document.querySelectorAll('.nodeLab, .examquestions-answer, .flagChecked').forEach(el => {
                el.addEventListener('click', () => {
                    window._optionClicked = true;
                }, { once: false });
            });
        ''')
        
        # 轮询检测各种操作
        start_time = asyncio.get_event_loop().time()
        while asyncio.get_event_loop().time() - start_time < timeout:
            # 检查浮动窗口按钮
            widget_next = await page.evaluate('window._widgetNextClicked')
            if widget_next:
                await page.evaluate('window._widgetNextClicked = false')
                # 判断是最后一题（提交）还是下一题
                is_last = await page.evaluate('''
                    const widget = document.getElementById('ai-answer-widget');
                    if (widget) {
                        const btn = widget.querySelector('.nav-btn.next');
                        return btn && btn.textContent.includes('提交');
                    }
                    return false;
                ''')
                return "submit" if is_last else "next"
            
            widget_prev = await page.evaluate('window._widgetPrevClicked')
            if widget_prev:
                await page.evaluate('window._widgetPrevClicked = false')
                return "prev"
            
            # 检查页面选项点击（仅单选题有效）
            if not is_multiple:
                option_clicked = await page.evaluate('window._optionClicked')
                if option_clicked:
                    await page.evaluate('window._optionClicked = false')
                    await page.wait_for_timeout(300)
                    return "option_click"
            
            await asyncio.sleep(0.2)
        
        return "timeout"
    except:
        return "timeout"


async def click_prev_button(page: Page):
    """点击上一题按钮"""
    try:
        # 尝试多种选择器
        selectors = [
            "text='上一题'",
            "text*='上一题'",
            ".prev-btn",
            "button:has-text('上一题')",
            ".exam-btn-prev"
        ]
        
        for selector in selectors:
            try:
                btn = page.locator(selector).first
                if await btn.count() > 0:
                    await btn.click(timeout=2000)
                    logger.info("[OK] 点击上一题")
                    return
            except:
                continue
        
        logger.warn("[FAIL] 未找到上一题按钮")
    except Exception as e:
        logger.warn("[FAIL] 点击上一题异常: %s" % str(e)[:30])


async def wait_for_widget_next_button(page: Page, timeout: float = 180) -> bool:
    """等待用户点击悬浮窗的下一题按钮"""
    try:
        # 重置状态
        await page.evaluate('window._widgetNextClicked = false')
        
        # 轮询检测点击
        start_time = asyncio.get_event_loop().time()
        while asyncio.get_event_loop().time() - start_time < timeout:
            clicked = await page.evaluate('window._widgetNextClicked')
            if clicked:
                await page.evaluate('window._widgetNextClicked = false')
                return True
            await asyncio.sleep(0.3)
        return False
    except:
        return False


async def wait_for_widget_submit(page: Page, timeout: float = 60) -> bool:
    """等待浮动组件的提交事件"""
    # ⚠️ 注意: answerSubmitted 事件在项目任何地方均未被触发（floating_widget.py 未 dispatch 此事件）
    # 保留此函数以备第三方扩展使用，但默认不会返回 True
    try:
        # 创建一个 Promise 等待提交事件
        result = await page.evaluate(f'''
            new Promise((resolve) => {{
                if (window.AIAnswerWidget) {{
                    const handler = () => {{
                        resolve(true);
                    }};
                    document.getElementById('ai-answer-widget').addEventListener('answerSubmitted', handler, {{ once: true }});
                    setTimeout(() => resolve(false), {timeout * 1000});
                }} else {{
                    resolve(false);
                }}
            }})
        ''')
        return result
    except:
        return False


async def _force_click_locator(locator):
    """对 locator 执行多重点击策略：click → click(force) → dispatchEvent"""
    try:
        await locator.click(timeout=3000)
        return "click"
    except Exception:
        pass
    try:
        await locator.click(force=True, timeout=3000)
        return "click-force"
    except Exception:
        pass
    try:
        await locator.dispatchEvent('click')
        return "dispatchEvent-click"
    except Exception:
        pass
    try:
        await locator.dispatchEvent('mousedown')
        await locator.dispatchEvent('mouseup')
        await locator.dispatchEvent('click')
        return "dispatchEvent-chain"
    except Exception:
        pass
    return None


async def click_option_by_index(page: Page, index: int, option_value: str = None):
    """通过索引点击选项（Playwright locator 层级兜底 + dispatchEvent 最终兜底）"""
    try:
        if option_value:
            # 方案A: 通过 input[value] 定位 → 点击 .label 或 .nodeLab
            sel_info = await page.evaluate(f'''
                (function() {{
                    const input = document.querySelector('input[value="{option_value}"]');
                    if (!input) return null;
                    const nodeLab = input.closest('.nodeLab');
                    const allLabs = document.querySelectorAll('.nodeLab');
                    if (nodeLab) {{
                        const gIdx = Array.from(allLabs).indexOf(nodeLab);
                        const lab = nodeLab.querySelector('.label');
                        if (lab) return {{ what: "label", idx: gIdx }};
                        return {{ what: "nodeLab", idx: gIdx }};
                    }}
                    return null;
                }})()
            ''')
            if sel_info:
                target = page.locator('.nodeLab').nth(sel_info["idx"])
                if sel_info["what"] == "label":
                    target = target.locator('.label')
                r = await _force_click_locator(target)
                if r:
                    logger.info("[OK] 通过value点击: value=%s (%s)" % (option_value, r))
                    await page.wait_for_timeout(200)
                    return

            # 方案A兜底: 直接 force 点 input
            try:
                loc = page.locator('input[value="%s"]' % option_value)
                if await loc.count() > 0:
                    r = await _force_click_locator(loc.first)
                    if r:
                        logger.info("[OK] 通过value点input: value=%s (%s)" % (option_value, r))
                        await page.wait_for_timeout(200)
                        return
            except Exception:
                pass

        # 方案B: 按索引定位
        idx_info = await page.evaluate(f'''
            (function() {{
                const subjects = document.querySelectorAll('.examPaper_subject');
                for (const subject of subjects) {{
                    const p = subject.parentElement;
                    if (p && p.style.display === 'none') continue;
                    const rect = subject.getBoundingClientRect();
                    if (rect.height > 0 && rect.width > 0) {{
                        const nodeLabs = subject.querySelectorAll('.nodeLab');
                        if (nodeLabs[{index}]) {{
                            const allLabs = document.querySelectorAll('.nodeLab');
                            const gIdx = Array.from(allLabs).indexOf(nodeLabs[{index}]);
                            return {{ idx: gIdx }};
                        }}
                    }}
                }}
                const allLabs = document.querySelectorAll('.nodeLab');
                const visLabs = [];
                for (const nl of allLabs) {{
                    const sub = nl.closest('.examPaper_subject');
                    if (sub) {{
                        const p = sub.parentElement;
                        if (p && p.style.display === 'none') continue;
                    }}
                    if (nl.offsetParent !== null) visLabs.push(nl);
                }}
                if (visLabs[{index}]) {{
                    return {{ idx: Array.from(allLabs).indexOf(visLabs[{index}]) }};
                }}
                if (allLabs[{index}]) {{
                    return {{ idx: {index} }};
                }}
                return null;
            }})()
        ''')
        if idx_info:
            main = page.locator('.nodeLab').nth(idx_info["idx"])
            label = main.locator('.label')
            r = await _force_click_locator(label)
            if r:
                logger.info("[OK] 通过索引点击label: 选项%d (%s)" % (index+1, r))
                await page.wait_for_timeout(200)
                return
            r = await _force_click_locator(main)
            if r:
                logger.info("[OK] 通过索引点击nodeLab: 选项%d (%s)" % (index+1, r))
                await page.wait_for_timeout(200)
                return

        # 方案C: .topic-item / .option-item / .el-radio / .el-checkbox
        for sel in ['.topic-item', '.option-item', '.el-radio', '.el-checkbox']:
            loc = page.locator(sel)
            cnt = await loc.count()
            if cnt > 0 and index < cnt:
                r = await _force_click_locator(loc.nth(index))
                if r:
                    logger.info("[OK] 通过%s点击: 选项%d (%s)" % (sel, index+1, r))
                    return

        # 方案D: input[type=radio/checkbox] 全局
        for sel in ['input[type="radio"]', 'input[type="checkbox"]']:
            loc = page.locator(sel)
            cnt = await loc.count()
            if cnt > 0 and index < cnt:
                r = await _force_click_locator(loc.nth(index))
                if r:
                    logger.info("[OK] 通过%s点击: 选项%d (%s)" % (sel, index+1, r))
                    return

        logger.warn("[FAIL] 所有方案失败: index=%d" % index)
    except Exception as e:
        logger.warn("[FAIL] 点击异常: %s" % str(e)[:80])


async def click_option_by_text(page: Page, text: str):
    """通过文本匹配点击选项（_force_click_locator 多重点击策略）"""
    try:
        escaped = text.replace("'", "\\'")
        # 方案A: .nodeLab 按文本匹配
        idx_info = await page.evaluate(f'''
            (function() {{
                const allLabs = document.querySelectorAll('.nodeLab');
                for (let i = 0; i < allLabs.length; i++) {{
                    const lab = allLabs[i];
                    if (!lab.textContent.includes('{escaped}')) continue;
                    const sub = lab.closest('.examPaper_subject');
                    if (sub) {{
                        const p = sub.parentElement;
                        if (p && p.style.display === 'none') continue;
                    }}
                    if (lab.offsetParent === null) continue;
                    return {{ idx: i }};
                }}
                for (let i = 0; i < allLabs.length; i++) {{
                    const lab = allLabs[i];
                    if (!lab.textContent.includes('{escaped}')) continue;
                    const sub = lab.closest('.examPaper_subject');
                    if (sub) {{
                        const p = sub.parentElement;
                        if (p && p.style.display === 'none') continue;
                    }}
                    return {{ idx: i, hidden: true }};
                }}
                return null;
            }})()
        ''')
        if idx_info:
            main = page.locator('.nodeLab').nth(idx_info["idx"])
            label = main.locator('.label')
            for target, name in [(label, "label"), (main, "nodeLab")]:
                r = await _force_click_locator(target)
                if r:
                    logger.info("[OK] 通过文本点击%s: text=%s (%s)" % (name, text, r))
                    await page.wait_for_timeout(200)
                    return

        # 方案B: .topic-item / .option-item 按文本匹配
        for sel in ['.topic-item', '.option-item']:
            locs = page.locator(sel)
            count = await locs.count()
            for i in range(count):
                el_text = await locs.nth(i).text_content()
                if el_text and text in el_text:
                    r = await _force_click_locator(locs.nth(i))
                    if r:
                        logger.info("[OK] 通过文本点击%s: text=%s (%s)" % (sel, text, r))
                        return

        logger.warn("[FAIL] 通过文本所有方法失败: text=%s" % text)
    except Exception as e:
        logger.warn("[FAIL] 通过文本点击异常: %s" % str(e)[:80])


async def answer_question(page: Page, answer: str, options: list = None, raw_options: list = None):
    """
    答题主函数
    answer: 答案文本（如 "对"、"A"、"正确" 等），多选题答案用 ### 分隔
    options: 选项文本列表（用于索引匹配）
    raw_options: 原始选项列表 [(id, text), ...] 用于 input[value] 定位
    """
    # 处理空答案
    if not answer or not answer.strip():
        logger.info("[ANS] 答题: 答案为空，跳过")
        return False
    
    answer = answer.strip()
    logger.info("[ANS] 答题: %s" % answer)
    
    # 打印选项列表，方便调试
    if options:
        logger.info("[ANS] 选项列表: %s" % str(options[:5]))
    
    # 使用 _match_option 智能匹配答案到选项索引
    if options:
        matched_indices = _match_option(answer, options)
        logger.info("[ANS] 智能匹配结果: %s" % str(matched_indices))
        if matched_indices:
            for idx in matched_indices:
                logger.info("[ANS] 尝试点击选项 %d: %s" % (idx+1, options[idx] if idx < len(options) else "?"))
                option_value = str(raw_options[idx][0]) if raw_options and idx < len(raw_options) else None
                await click_option_by_index(page, idx, option_value)
                await page.wait_for_timeout(300)
            return True
        else:
            logger.warn("[ANS] 智能匹配失败，尝试文本匹配")
    
    # 如果没有选项列表或匹配失败，使用传统方法
    # 检查是否是多选题（答案包含 ### 分隔符）
    if '###' in answer:
        answers = answer.split('###')
        logger.info("[ANS] 多选题，共 %d 个答案" % len(answers))
        for idx, ans in enumerate(answers):
            ans = ans.strip()
            if ans:
                logger.info("[ANS] 选择第 %d 个答案: %s" % (idx+1, ans))
                # 判断题特殊处理
                if ans in ['对', '正确', '是', '√', 'True', 'true']:
                    await click_option_by_text(page, '对')
                elif ans in ['错', '错误', '否', '×', 'False', 'false']:
                    await click_option_by_text(page, '错')
                elif ans in ['A', 'B', 'C', 'D']:
                    index = ord(ans) - ord('A')
                    await click_option_by_index(page, index)
                else:
                    await click_option_by_text(page, ans)
                # 多选时每个选项之间稍等
                if idx < len(answers) - 1:
                    await page.wait_for_timeout(300)
        return True
    
    # 单选题：判断题特殊处理
    if answer in ['对', '正确', '是', '√', 'True', 'true']:
        logger.info("[ANS] 判断题答案: 对")
        await click_option_by_text(page, '对')
        return True
    if answer in ['错', '错误', '否', '×', 'False', 'false']:
        logger.info("[ANS] 判断题答案: 错")
        await click_option_by_text(page, '错')
        return True
    
    # 单选题：字母选项
    if answer in ['A', 'B', 'C', 'D']:
        index = ord(answer) - ord('A')
        logger.info("[ANS] 字母选项: %s -> 索引 %d" % (answer, index))
        await click_option_by_index(page, index)
        return True
    
    # 最后：通过文本匹配
    logger.info("[ANS] 文本匹配: %s" % answer)
    await click_option_by_text(page, answer)
    return True


async def click_next_button(page: Page):
    """点击下一题按钮"""
    try:
        # 方法1：通过按钮文本
        next_btn = page.locator("button:has-text('下一题')").first
        await next_btn.click(timeout=3000)
        logger.info("[OK] 点击下一题")
        return
    except:
        pass
    
    try:
        # 方法2：通过 class
        next_btn = page.locator(".switch-btn-box button").last
        await next_btn.click(timeout=3000)
        logger.info("[OK] 点击下一题（备用）")
        return
    except:
        pass
    
    try:
        # 方法3：其他选择器
        next_btn = page.locator(".next-btn, [class*='next']").first
        await next_btn.click(timeout=3000)
        logger.info("[OK] 点击下一题（选择器）")
        return
    except:
        pass
    
    try:
        # 方法4：键盘右键
        await page.keyboard.press("ArrowRight")
        logger.info("[OK] 使用键盘下一题")
    except Exception as e:
        logger.warn("[FAIL] 点击下一题失败: %s" % str(e)[:50])


async def submit_exam(page: Page) -> bool:
    """提交作业
    
    返回值：
    - True: 提交成功
    - False: 提交失败
    """
    try:
        # 1. 点击提交按钮
        submit_btn = page.locator("button:has-text('提交作业'), .btnStyleXSumit").first
        await submit_btn.click(timeout=5000)
        logger.info("[OK] 点击提交作业按钮")
        
        # 2. 等待确认对话框出现并点击确定
        await page.wait_for_timeout(1000)
        
        # 尝试多种确认按钮选择器
        confirm_selectors = [
            "button.el-button--primary:has-text('确定')",
            ".el-message-box__btns button.el-button--primary",
            "button.el-button.el-button--default.el-button--small.el-button--primary",
            ".el-message-box__confirm",
            ".confirm-btn"
        ]
        
        confirmed = False
        for selector in confirm_selectors:
            try:
                confirm_btn = page.locator(selector).first
                if await confirm_btn.count() > 0:
                    await confirm_btn.click(timeout=3000)
                    logger.info("[OK] 点击确认按钮")
                    confirmed = True
                    break
            except:
                continue
        
        if not confirmed:
            logger.warn("[WARN] 未找到确认按钮，可能已自动提交")
        
        # 3. 等待提交完成
        await page.wait_for_timeout(2000)
        logger.info("[OK] 作业提交完成")
        
        # 4. 关闭当前页面（测验窗口）
        try:
            # 检查是否有关闭按钮
            close_selectors = [
                ".close-btn",
                "button:has-text('关闭')",
                ".el-dialog__close",
                ".exam-close-btn"
            ]
            
            for selector in close_selectors:
                try:
                    close_btn = page.locator(selector).first
                    if await close_btn.count() > 0:
                        await close_btn.click(timeout=2000)
                        logger.info("[OK] 关闭测验窗口")
                        break
                except:
                    continue
        except Exception as e:
            logger.warn("[WARN] 关闭窗口失败: %s" % str(e)[:30])
        
        return True
        
    except Exception as e:
        logger.warn("[FAIL] 提交失败：%s" % str(e)[:50])
        return False


async def _extract_test_questions_from_dom(page: Page):
    """从DOM提取测试题目（备用方案）"""
    questions = []
    try:
        q_items = page.locator(".question-item, .exam-question, .topic-item")
        count = await q_items.count()
        for i in range(count):
            item = q_items.nth(i)
            name = await item.locator(".question-name, .topic-title, .stem").first.text_content()
            name = name.strip() if name else ""
            opts = []
            opt_items = item.locator(".option-item, .topic-item .option, li")
            opt_count = await opt_items.count()
            for j in range(opt_count):
                text = await opt_items.nth(j).text_content()
                if text:
                    opts.append((0, text.strip()))
            if name:
                questions.append({
                    "name": name,
                    "type": "未知",
                    "type_id": 0,
                    "options": opts,
                    "score": "",
                    "eid": "",
                })
    except Exception:
        pass
    return questions


async def get_chapter_videos_status(page: Page) -> list:
    """获取当前章节中所有视频的完成状态
    
    返回值：[{name: str, completed: bool, element: ElementHandle}, ...]
    """
    videos = []
    try:
        # 查找所有视频项目
        video_items = page.locator(".catalogue_title, .lesson-item, .video-item, [class*='video']")
        count = await video_items.count()
        
        for i in range(count):
            item = video_items.nth(i)
            text = await item.text_content()
            if not text:
                continue
            
            # 检查是否已完成（通常有"已完成"、"100%"等标记）
            text_lower = text.lower()
            completed = (
                "已完成" in text or 
                "100%" in text or 
                "complete" in text_lower or
                "done" in text_lower
            )
            
            # 检查是否是视频（排除测验等）
            is_video = (
                "video" in text_lower or 
                "视频" in text or
                "观看" in text or
                await item.locator("video, .video-icon, [class*='play']").count() > 0
            )
            
            if is_video or not completed:  # 包含未完成的项目
                videos.append({
                    "name": text.strip()[:50],
                    "completed": completed,
                    "index": i
                })
    except Exception as e:
        logger.warn("[WARN] 获取视频状态失败: %s" % str(e)[:30])
    
    return videos


async def wait_for_video_completion(page: Page, timeout: float = 3600) -> bool:
    """等待当前视频播放完成
    
    timeout: 最大等待时间（秒），默认1小时
    """
    try:
        start_time = asyncio.get_event_loop().time()
        last_progress = 0
        
        while asyncio.get_event_loop().time() - start_time < timeout:
            # 获取视频进度
            status = await page.evaluate('''() => {
                const video = document.querySelector('video');
                if (!video) return null;
                return {
                    currentTime: video.currentTime,
                    duration: video.duration,
                    ended: video.ended,
                    paused: video.paused
                };
            }''')
            
            if not status:
                await asyncio.sleep(2)
                continue
            
            # 检查是否播放完成
            if status['ended'] or (status['duration'] > 0 and status['currentTime'] >= status['duration'] - 1):
                logger.info("[OK] 视频播放完成")
                return True
            
            # 显示进度
            if status['duration'] > 0:
                progress = int(status['currentTime'] / status['duration'] * 100)
                if progress != last_progress and progress % 10 == 0:
                    logger.info("[VIDEO] 播放进度: %d%%" % progress)
                    last_progress = progress
            
            # 如果视频暂停了，尝试继续播放
            if status['paused'] and not status['ended']:
                await page.evaluate('''() => {
                    const video = document.querySelector('video');
                    if (video && !video.ended) {
                        video.play().catch(() => {});
                    }
                }''')
            
            await asyncio.sleep(3)
        
        logger.warn("[WARN] 视频播放超时")
        return False
        
    except Exception as e:
        logger.warn("[WARN] 视频播放异常: %s" % str(e)[:30])
        return False


async def chapter_learning_flow(page: Page, test_handler=None, auto_submit: bool = False, force_video_first: bool = True) -> bool:
    """章节学习流程
    
    流程：
    1. 如果 force_video_first=True，先学习未完成的视频
    2. 完成当前章节的测验
    3. 验证所有内容已完成，才能进入下一章节
    
    参数：
    - page: Playwright Page 对象
    - test_handler: 测验处理器（用于获取题目数据）
    - auto_submit: 是否自动提交测验
    - force_video_first: 是否强制先完成视频（默认True）
    
    返回值：
    - True: 章节学习完成
    - False: 学习失败（有未完成内容）
    """
    logger.info("\n" + "="*60)
    logger.info("[CHAPTER] 开始章节学习流程")
    logger.info("="*60)
    
    try:
        # 【步骤1】学习未完成的视频（强制要求）
        if force_video_first:
            logger.info("\n[CHAPTER] 步骤1: 学习未完成视频（强制）")
            
            videos = await get_chapter_videos_status(page)
            
            if videos:
                incomplete_videos = [v for v in videos if not v['completed']]
                
                if incomplete_videos:
                    logger.info("[CHAPTER] 发现 %d 个未完成视频，必须先完成" % len(incomplete_videos))
                    
                    # 逐个播放未完成的视频
                    for i, video in enumerate(incomplete_videos):
                        logger.info("\n[CHAPTER] 播放视频 %d/%d: %s" % (i+1, len(incomplete_videos), video['name'][:30]))
                        
                        try:
                            video_items = page.locator(".catalogue_title, .lesson-item, .video-item")
                            if video['index'] < await video_items.count():
                                await video_items.nth(video['index']).click()
                                await page.wait_for_timeout(1000)
                                
                                # 等待视频加载
                                await page.wait_for_selector("video", state="attached", timeout=10000)
                                
                                # 播放视频
                                await page.evaluate('''() => {
                                    const video = document.querySelector('video');
                                    if (video) {
                                        video.muted = true;
                                        video.play().catch(() => {});
                                    }
                                }''')
                                
                                # 等待视频完成（强制）
                                video_completed = await wait_for_video_completion(page)
                                
                                if not video_completed:
                                    logger.warn("[CHAPTER] 视频未完成，无法进入测验")
                                    return False
                                
                                # 返回章节列表
                                await page.go_back()
                                await page.wait_for_timeout(1000)
                        except Exception as e:
                            logger.warn("[CHAPTER] 视频播放失败: %s" % str(e)[:30])
                            return False
                else:
                    logger.info("[CHAPTER] 所有视频已完成")
            else:
                logger.info("[CHAPTER] 未找到视频内容")
        
        # 【步骤2】完成测验
        if test_handler and test_handler.questions_data:
            logger.info("\n[CHAPTER] 步骤2: 完成章节测验")
            test_success = await handle_test_page(page, test_handler.questions_data, auto_submit)
            
            if test_success:
                logger.info("[CHAPTER] 测验完成")
            else:
                logger.warn("[CHAPTER] 测验失败")
                return False
        else:
            logger.info("\n[CHAPTER] 步骤2: 无测验或测验已完成，跳过")
        
        # 【步骤3】验证所有内容已完成
        logger.info("\n[CHAPTER] 步骤3: 验证完成状态")
        
        # 再次检查视频状态
        videos = await get_chapter_videos_status(page)
        if videos:
            incomplete = [v for v in videos if not v['completed']]
            if incomplete:
                logger.warn("[CHAPTER] 仍有 %d 个视频未完成，无法进入下一章" % len(incomplete))
                return False
        
        logger.info("\n" + "="*60)
        logger.info("[CHAPTER] 章节学习完成，可进入下一章")
        logger.info("="*60)
        
        return True
        
    except Exception as e:
        logger.error("[CHAPTER] 章节学习异常: %s" % str(e)[:50])
        return False
