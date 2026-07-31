import ctypes
import json
import os.path
import traceback
from typing import List
from playwright.async_api import Page, Locator
from playwright.async_api import TimeoutError
from playwright._impl._errors import TargetClosedError
from pygetwindow import Win32Window

from modules.configs import Config
from modules.course_errors import CourseAuthenticationError
from modules.course_portal import is_course_homepage_url, is_login_page
import time
import pygetwindow as gw
from modules.logger import Logger

logger = Logger()
PLAYWRIGHT_WINDOW_TITLE = "Autovisor - Playwright"

# ============================================================
# 深度扫描 JS 函数
# ============================================================

CARD_SCAN_JS = r"""
() => {
    const normalize = (text) => (text || '').replace(/\s+/g, ' ').trim();
    const progressRe = /(?:学习进度|进度|已学|完成度)\s*([0-9]{1,3})%/;
    const progressBarRe = /<div[^>]*style[^>]*width:\s*([0-9]{1,3})%/i;
    const badTitle = /^(学习进度|进度|掌握度|知识模块|知识单元|\d+%?)$/;
    const visible = (el) => {
        if (!el) return false;
        const rect = el.getBoundingClientRect();
        const style = window.getComputedStyle(el);
        return rect.width >= 80 &&
            rect.height >= 40 &&
            style.display !== 'none' &&
            style.visibility !== 'hidden' &&
            style.opacity !== '0';
    };
    const titleFrom = (el) => {
        const walkers = el.querySelectorAll('[title], h1, h2, h3, h4, h5, h6, strong, b, p, span, div');
        for (const node of walkers) {
            const text = normalize(node.getAttribute && node.getAttribute('title') ? node.getAttribute('title') : node.textContent);
            if (!text || text.length > 80 || badTitle.test(text) || text.includes('学习进度') || text.includes('掌握度') || text.includes('进度：')) {
                continue;
            }
            return text;
        }
        const lines = normalize(el.innerText).split(/(?<=\S)\s{2,}|\n/).map((x) => normalize(x)).filter(Boolean);
        for (const line of lines) {
            if (!badTitle.test(line) && !line.includes('学习进度') && !line.includes('掌握度') && !line.includes('进度：') && line.length <= 80) {
                return line;
            }
        }
        return '';
    };
    const sectionFrom = (el) => {
        let parent = el.parentElement;
        while (parent) {
            const text = normalize(parent.innerText || '');
            const lines = text.split(/\n/).map((x) => normalize(x)).filter(Boolean);
            for (const line of lines.slice(0, 6)) {
                if (line && line.length <= 40 && !line.includes('学习进度') && !line.includes('掌握度') && !line.includes('%')) {
                    if (line.includes('知识模块') || line.includes('知识单元')) {
                        continue;
                    }
                    return line;
                }
            }
            parent = parent.parentElement;
        }
        return '';
    };
    const extractProgress = (el) => {
        const text = normalize(el.innerText || '');
        const textMatch = text.match(progressRe);
        if (textMatch) {
            return Math.max(0, Math.min(100, Number(textMatch[1])));
        }
        const html = el.outerHTML || '';
        const htmlMatch = html.match(progressBarRe);
        if (htmlMatch) {
            return Math.max(0, Math.min(100, Number(htmlMatch[1])));
        }
        return null;
    };
    const all = Array.from(document.querySelectorAll('div, article, section, li, a, button, span'));
    const raw = [];
    for (const el of all) {
        if (!visible(el)) continue;
        const progress = extractProgress(el);
        if (progress === null) continue;
        const title = titleFrom(el);
        if (!title) continue;
        const rect = el.getBoundingClientRect();
        raw.push({
            element: el,
            title,
            section: sectionFrom(el),
            progress: progress,
            top: rect.top + window.scrollY,
            left: rect.left + window.scrollX,
            area: rect.width * rect.height,
        });
    }
    const chosen = new Map();
    for (const item of raw) {
        const key = `${item.section}@@${item.title}`;
        const prev = chosen.get(key);
        if (!prev || item.area < prev.area) {
            chosen.set(key, item);
        }
    }
    if (!window.__autovisorCardSeq) {
        window.__autovisorCardSeq = 1;
    }
    const cards = [];
    for (const [key, item] of chosen.entries()) {
        if (!item.element.dataset.autovisorCardId) {
            item.element.dataset.autovisorCardId = `autovisor-${window.__autovisorCardSeq++}`;
        }
        cards.push({
            key,
            card_id: item.element.dataset.autovisorCardId,
            title: item.title,
            section: item.section,
            progress: item.progress,
            top: item.top,
            left: item.left,
        });
    }
    cards.sort((a, b) => a.top - b.top || a.left - b.left);
    return {
        cards,
        scrollTop: window.scrollY,
        viewportHeight: window.innerHeight,
        scrollHeight: Math.max(document.body.scrollHeight, document.documentElement.scrollHeight),
    };
}
"""

SCROLL_ROOTS_JS = r"""
() => {
    const roots = [{scope_id: 'window', kind: 'window', label: 'window'}];
    let seq = 1;
    for (const el of Array.from(document.querySelectorAll('*'))) {
        const style = window.getComputedStyle(el);
        const overflowY = style.overflowY;
        const scrollable = (overflowY === 'auto' || overflowY === 'scroll') &&
            el.scrollHeight > el.clientHeight + 160 &&
            el.clientHeight > 220 &&
            el.clientWidth > 320;
        if (!scrollable) continue;
        if (!el.dataset.autovisorScrollId) {
            el.dataset.autovisorScrollId = `autovisor-scroll-${seq++}`;
        }
        roots.push({
            scope_id: el.dataset.autovisorScrollId,
            kind: 'element',
            label: el.className || el.id || el.tagName.toLowerCase(),
        });
    }
    return roots;
}
"""

NATIONAL_WISDOM_CARD_SCAN_JS = r"""
() => {
    const normalize = (text) => (text || '').replace(/\s+/g, ' ').trim();
    const cards = [];
    let seq = window.__autovisorCardSeq || 1;
    let domOrder = 0;
    
    const directChild = (parent, selector) => {
        for (const child of parent.children) {
            if (child.matches(selector)) return child;
        }
        return null;
    };
    const directChildren = (parent, selector) => {
        const result = [];
        for (const child of parent.children) {
            if (child.matches(selector)) result.push(child);
        }
        return result;
    };
    
    const chapterItems = document.querySelectorAll('.chapter-item');
    for (const chapterItem of chapterItems) {
        const subItems = directChildren(chapterItem, '.chapter-content-second');
        
        let section = '';
        const collapseItem = chapterItem.closest('.el-collapse-item');
        if (collapseItem) {
            const chapterFirst = collapseItem.querySelector('.chapter-first');
            if (chapterFirst) {
                section = normalize(chapterFirst.querySelector('.chapter-name')?.textContent || '');
                if (!section) {
                    section = normalize(chapterFirst.querySelector('.chapter-title')?.textContent || '');
                }
            }
        }
        
        if (subItems.length > 0) {
            for (const sub of subItems) {
                const num = normalize(sub.querySelector('.item-num')?.textContent || '');
                const name = normalize(sub.querySelector('.item-name')?.textContent || '');
                if (!name) continue;
                const fullTitle = num ? `${num} ${name}` : name;
                const isDone = !!sub.querySelector('.finish-icon');
                const progress = isDone ? 100 : 0;
                if (!sub.dataset.autovisorCardId) {
                    sub.dataset.autovisorCardId = `autovisor-${seq++}`;
                }
                cards.push({
                    key: `${section}@@${fullTitle}`,
                    card_id: sub.dataset.autovisorCardId,
                    title: fullTitle,
                    section: section || '未分组',
                    progress: progress,
                    dom_order: domOrder++,
                });
            }
        } else {
            const itemBox = directChild(chapterItem, '.item-box');
            if (!itemBox || itemBox.classList.contains('test-box')) continue;
            const num = normalize(itemBox.querySelector('.item-num')?.textContent || '');
            const name = normalize(itemBox.querySelector('.item-name')?.textContent || '');
            if (!name) continue;
            const fullTitle = num ? `${num} ${name}` : name;
            const isDone = !!itemBox.querySelector('.finish-icon');
            const progress = isDone ? 100 : 0;
            if (!itemBox.dataset.autovisorCardId) {
                itemBox.dataset.autovisorCardId = `autovisor-${seq++}`;
            }
            cards.push({
                key: `${section}@@${fullTitle}`,
                card_id: itemBox.dataset.autovisorCardId,
                title: fullTitle,
                section: section || '未分组',
                progress: progress,
                dom_order: domOrder++,
            });
        }
    }
    
    // 独立扫描所有 .test-box 元素（不受 .chapter-item 结构限制）
    const testBoxes = document.querySelectorAll('.test-box');
    for (const testBox of testBoxes) {
        const isDone = !!testBox.querySelector('.finish-icon');
        if (isDone) continue;  // 已完成的测试跳过
        const rect = testBox.getBoundingClientRect();
        if (rect.width < 20 && rect.height < 20) continue;
        let section = '';
        const collapseItem = testBox.closest('.el-collapse-item');
        if (collapseItem) {
            const chapterFirst = collapseItem.querySelector('.chapter-first');
            if (chapterFirst) {
                section = normalize(chapterFirst.querySelector('.chapter-name')?.textContent || '');
                if (!section) {
                    section = normalize(chapterFirst.querySelector('.chapter-title')?.textContent || '');
                }
            }
        }
        const testText = normalize(testBox.querySelector('span')?.textContent || '') || '测试';
        const key = `${section}@@测验-${testText}`;
        if (!testBox.dataset.autovisorCardId) {
            testBox.dataset.autovisorCardId = `autovisor-${seq++}`;
        }
        cards.push({
            key,
            card_id: testBox.dataset.autovisorCardId,
            title: '测验 - ' + testText,
            section: section || '未分组',
            progress: 0,
            type: 'test',
            dom_order: domOrder++,
        });
    }
    
    window.__autovisorCardSeq = seq;
    
    return {
        cards,
        scrollTop: window.scrollY || window.pageYOffset || 0,
        viewportHeight: window.innerHeight,
        scrollHeight: Math.max(document.body.scrollHeight, document.documentElement.scrollHeight),
    };
}
"""

APPLY_VIDEO_SETTINGS_JS = r"""
(settings) => {
    const video = document.querySelector('video');
    if (!video) return { success: false };

    // 兼容旧调用方传入纯数字；新调用方显式传递是否静音。
    const isObject = settings !== null && typeof settings === 'object';
    const speed = isObject ? settings.speed : settings;
    const shouldMute = isObject ? Boolean(settings.mute) : true;
    if (shouldMute) {
        video.muted = true;
        video.volume = 0;
    } else {
        video.muted = false;
        if (video.volume === 0) video.volume = 1;
    }
    
    // 设置倍速，强制设置
    if (speed) {
        video.playbackRate = speed;
    }
    
    return { 
        success: true,
        playbackRate: video.playbackRate, 
        muted: video.muted,
        duration: video.duration,
        currentTime: video.currentTime 
    };
}
"""

EXPAND_NATIONAL_WISDOM_CHAPTERS_JS = r"""
() => {
    const items = document.querySelectorAll('.el-collapse-item');
    let expandedCount = 0;
    for (const item of items) {
        let header = null, wrap = null;
        for (const child of item.children) {
            if (child.matches('.el-collapse-item__header')) header = child;
            if (child.matches('.el-collapse-item__wrap')) wrap = child;
        }
        if (!header || !wrap) continue;
        if (header.classList.contains('is-active')) continue;
        header.classList.add('is-active');
        header.setAttribute('aria-expanded', 'true');
        const arrow = header.querySelector('.el-collapse-item__arrow');
        if (arrow) arrow.classList.add('is-active');
        wrap.setAttribute('aria-hidden', 'false');
        wrap.style.setProperty('display', 'block', 'important');
        wrap.style.setProperty('height', 'auto', 'important');
        wrap.style.setProperty('overflow', 'visible', 'important');
        expandedCount++;
    }
    return expandedCount;
}
"""

NATIONAL_WISDOM_DIAGNOSE_JS = r"""
 () => {
     const items = document.querySelectorAll('.item-box, .chapter-content-second');
     const skipped = [];
     let visibleCount = 0;
     for (const item of items) {
         if (item.classList.contains('test-box')) continue;
         const rect = item.getBoundingClientRect();
         const name = (item.querySelector('.item-name')?.textContent || '').trim().substring(0, 30);
         if (rect.width < 20 || rect.height < 20) {
             let reason = `rect: ${rect.width.toFixed(0)}x${rect.height.toFixed(0)}`;
             let parent = item.parentElement;
             let depth = 0;
             while (parent && depth < 6) {
                 const style = window.getComputedStyle(parent);
                 if (style.display === 'none') { reason += ` | parent[${depth}] display:none (${parent.className?.substring(0,40)})`; break; }
                 if (style.height === '0px') { reason += ` | parent[${depth}] height:0`; break; }
                 if (style.overflow === 'hidden' && parent.clientHeight === 0) { reason += ` | parent[${depth}] overflow:hidden+0h`; break; }
                 parent = parent.parentElement;
                 depth++;
             }
             skipped.push({ name, reason });
         } else {
             visibleCount++;
         }
     }
     return { total: items.length, visible: visibleCount, skipped: skipped.slice(0, 12) };
 }
 """

CARD_SCAN_SCOPE_JS = r"""
(scopeId) => {
    const normalize = (text) => (text || '').replace(/\s+/g, ' ').trim();
    const progressRe = /(?:学习进度|进度|已学|完成度)\s*([0-9]{1,3})%/;
    const progressBarRe = /<div[^>]*style[^>]*width:\s*([0-9]{1,3})%/i;
    const badTitle = /^(学习进度|进度|掌握度|知识模块|知识单元|\d+%?)$/;
    const root = scopeId === 'window'
        ? window
        : document.querySelector(`[data-autovisor-scroll-id="${scopeId}"]`);
    if (!root) {
        return { cards: [], scrollTop: 0, viewportHeight: 0, scrollHeight: 0 };
    }
    const rootRect = scopeId === 'window'
        ? { top: 0, left: 0, bottom: window.innerHeight, right: window.innerWidth }
        : root.getBoundingClientRect();
    const visibleInScope = (el) => {
        if (!el) return false;
        const rect = el.getBoundingClientRect();
        const style = window.getComputedStyle(el);
        const intersects = rect.bottom > rootRect.top + 8 &&
            rect.top < rootRect.bottom - 8 &&
            rect.right > rootRect.left + 8 &&
            rect.left < rootRect.right - 8;
        return rect.width >= 80 &&
            rect.height >= 40 &&
            intersects &&
            style.display !== 'none' &&
            style.visibility !== 'hidden' &&
            style.opacity !== '0';
    };
    const titleFrom = (el) => {
        const walkers = el.querySelectorAll('[title], h1, h2, h3, h4, h5, h6, strong, b, p, span, div');
        for (const node of walkers) {
            const text = normalize(node.getAttribute && node.getAttribute('title') ? node.getAttribute('title') : node.textContent);
            if (!text || text.length > 80 || badTitle.test(text) || text.includes('学习进度') || text.includes('掌握度') || text.includes('进度：')) {
                continue;
            }
            return text;
        }
        const lines = normalize(el.innerText).split(/(?<=\S)\s{2,}|\n/).map((x) => normalize(x)).filter(Boolean);
        for (const line of lines) {
            if (!badTitle.test(line) && !line.includes('学习进度') && !line.includes('掌握度') && !line.includes('进度：') && line.length <= 80) {
                return line;
            }
        }
        return '';
    };
    const sectionFrom = (el) => {
        let parent = el.parentElement;
        while (parent) {
            const text = normalize(parent.innerText || '');
            const lines = text.split(/\n/).map((x) => normalize(x)).filter(Boolean);
            for (const line of lines.slice(0, 6)) {
                if (line && line.length <= 40 && !line.includes('学习进度') && !line.includes('掌握度') && !line.includes('%')) {
                    if (line.includes('知识模块') || line.includes('知识单元')) continue;
                    return line;
                }
            }
            parent = parent.parentElement;
        }
        return '';
    };
    const extractProgress = (el) => {
        const text = normalize(el.innerText || '');
        const textMatch = text.match(progressRe);
        if (textMatch) {
            return Math.max(0, Math.min(100, Number(textMatch[1])));
        }
        const html = el.outerHTML || '';
        const htmlMatch = html.match(progressBarRe);
        if (htmlMatch) {
            return Math.max(0, Math.min(100, Number(htmlMatch[1])));
        }
        return null;
    };
    const source = scopeId === 'window' ? document : root;
    const raw = [];
    for (const el of Array.from(source.querySelectorAll('div, article, section, li, a, button, span'))) {
        if (!visibleInScope(el)) continue;
        const progress = extractProgress(el);
        if (progress === null) continue;
        const title = titleFrom(el);
        if (!title) continue;
        const rect = el.getBoundingClientRect();
        raw.push({
            element: el,
            title,
            section: sectionFrom(el),
            progress: progress,
            top: scopeId === 'window' ? rect.top + window.scrollY : rect.top - rootRect.top + root.scrollTop,
            left: scopeId === 'window' ? rect.left + window.scrollX : rect.left - rootRect.left + root.scrollLeft,
            area: rect.width * rect.height,
        });
    }
    const chosen = new Map();
    for (const item of raw) {
        const key = `${scopeId}@@${item.section}@@${item.title}`;
        const prev = chosen.get(key);
        if (!prev || item.area < prev.area) {
            chosen.set(key, item);
        }
    }
    if (!window.__autovisorCardSeq) {
        window.__autovisorCardSeq = 1;
    }
    const cards = [];
    for (const [key, item] of chosen.entries()) {
        if (!item.element.dataset.autovisorCardId) {
            item.element.dataset.autovisorCardId = `autovisor-${window.__autovisorCardSeq++}`;
        }
        cards.push({
            key,
            scope_id: scopeId,
            card_id: item.element.dataset.autovisorCardId,
            title: item.title,
            section: item.section,
            progress: item.progress,
            top: item.top,
            left: item.left,
        });
    }
    cards.sort((a, b) => a.top - b.top || a.left - b.left);
    return {
        cards,
        scrollTop: scopeId === 'window' ? window.scrollY : root.scrollTop,
        viewportHeight: scopeId === 'window' ? window.innerHeight : root.clientHeight,
        scrollHeight: scopeId === 'window'
            ? Math.max(document.body.scrollHeight, document.documentElement.scrollHeight)
            : root.scrollHeight,
    };
}
"""

# ============================================================
# 深度扫描辅助函数
# ============================================================

def summarize_cards(cards: list[dict]) -> dict:
    summary = {
        "total": len(cards),
        "pending": 0,
        "done": 0,
        "sections": {},
    }
    for card in cards:
        done = card["progress"] >= 100
        if done:
            summary["done"] += 1
        else:
            summary["pending"] += 1
        section = card.get("section") or "未分组"
        section_state = summary["sections"].setdefault(section, {"total": 0, "pending": 0, "done": 0})
        section_state["total"] += 1
        if done:
            section_state["done"] += 1
        else:
            section_state["pending"] += 1
    return summary

async def _ensure_national_scan_authenticated(page: Page) -> None:
    if await is_login_page(page) or is_course_homepage_url(
        getattr(page, "url", "")
    ):
        raise CourseAuthenticationError(
            "全国共享课等待课程列表时登录状态失效"
        )


async def _raise_national_auth_if_needed(
    page: Page,
    error: Exception,
) -> None:
    try:
        await _ensure_national_scan_authenticated(page)
    except CourseAuthenticationError as auth_error:
        raise auth_error from error


async def _find_national_wisdom_context(
    page: Page,
    *,
    max_attempts: int = 15,
    retry_delay_ms: int = 2000,
):
    """查找全国智慧共享课的课程列表所在上下文（主页面或 iframe Frame）"""
    for attempt in range(max_attempts):
        await _ensure_national_scan_authenticated(page)
        for frame in page.frames:
            try:
                if await frame.locator('.chapter-item').count() > 0:
                    if attempt > 0:
                        logger.info(f"课程列表加载完成 (第 {attempt + 1} 次尝试)")
                    logger.info("检测到课程列表在 iframe 中，切换上下文")
                    return frame, True
            except TargetClosedError:
                raise
            except Exception:
                continue
        try:
            if await page.locator('.chapter-item').count() > 0:
                logger.info("课程列表在主页面中")
                return page, False
        except TargetClosedError:
            raise
        except Exception:
            pass
        logger.write_log(
            f"等待课程列表加载... ({attempt + 1}/{max_attempts})\n"
        )
        await page.wait_for_timeout(retry_delay_ms)
        await _ensure_national_scan_authenticated(page)
    logger.warn("超时未检测到课程列表，使用主页面作为上下文")
    return page, False


async def scan_national_wisdom_cards(page: Page, max_rounds: int = 30) -> tuple[list[dict], dict, bool]:
    """全国智慧共享课专用扫描函数"""
    merged: dict[str, dict] = {}
    
    # 查找课程列表所在上下文（主页面或 iframe Frame）
    context, is_in_iframe = await _find_national_wisdom_context(page)
    await _ensure_national_scan_authenticated(page)
    
    # DOM 强制展开所有折叠章节
    try:
        expanded = await context.evaluate(EXPAND_NATIONAL_WISDOM_CHAPTERS_JS)
        if expanded:
            logger.info(f"DOM强制展开了 {expanded} 个折叠章节")
            await context.wait_for_function('''() => {
                const items = document.querySelectorAll('.chapter-content-second');
                return items.length > 0;
            }''', timeout=5000)
        await page.wait_for_timeout(500)
    except (CourseAuthenticationError, TargetClosedError):
        raise
    except Exception as e:
        await _raise_national_auth_if_needed(page, e)
        logger.write_log(f"展开章节失败: {repr(e)}\n")
    
    # 诊断：展开后 item-box 为什么不可见
    try:
        diag = await context.evaluate(NATIONAL_WISDOM_DIAGNOSE_JS)
        if diag.get("skipped"):
            for s in diag["skipped"]:
                logger.write_log(f"  [跳过] {s['name']} → {s['reason']}\n")
        logger.write_log(f"诊断: {diag['total']}个.item-box | {diag['visible']}个可见 | {len(diag.get('skipped',[]))}个被跳过\n")
    except (CourseAuthenticationError, TargetClosedError):
        raise
    except Exception as e:
        await _raise_national_auth_if_needed(page, e)
        logger.write_log(f"诊断失败: {repr(e)}\n")
    
    # 滚动到顶部
    try:
        await context.evaluate("window.scrollTo(0, 0)")
    except TargetClosedError:
        raise
    except Exception as exc:
        await _raise_national_auth_if_needed(page, exc)
        raise
    await page.wait_for_timeout(500)
    
    # 滚动扫描
    for round_num in range(max_rounds):
        await _ensure_national_scan_authenticated(page)
        try:
            result = await context.evaluate(NATIONAL_WISDOM_CARD_SCAN_JS)
        except TargetClosedError:
            raise
        except Exception as exc:
            await _raise_national_auth_if_needed(page, exc)
            raise
        cards = result["cards"]
        
        for card in cards:
            key = card["key"]
            prev = merged.get(key)
            if not prev or card["progress"] != prev.get("progress"):
                merged[key] = card
        
        scroll_top = result["scrollTop"]
        viewport_height = result["viewportHeight"]
        scroll_height = result["scrollHeight"]
        bottom_reached = scroll_top + viewport_height >= scroll_height - 8
        
        if bottom_reached and round_num > 0:
            break
        
        step = max(300, int(viewport_height * 0.8))
        try:
            await context.evaluate("(step) => window.scrollBy(0, step)", step)
        except TargetClosedError:
            raise
        except Exception as exc:
            await _raise_national_auth_if_needed(page, exc)
            raise
        await page.wait_for_timeout(800)
        await _ensure_national_scan_authenticated(page)
    
    # 回到顶部
    try:
        await context.evaluate("window.scrollTo(0, 0)")
    except TargetClosedError:
        raise
    except Exception as exc:
        await _raise_national_auth_if_needed(page, exc)
        raise
    await page.wait_for_timeout(300)
    
    cards = list(merged.values())
    cards.sort(key=lambda item: (item["progress"], 0 if item.get("type") == "test" else 1, item.get("dom_order", 9999)))
    summary = summarize_cards(cards)
    
    logger.info(f"全国智慧共享课扫描完成: 总卡片 {summary['total']} | 未完成 {summary['pending']} | 已完成 {summary['done']}")
    
    return cards, summary, is_in_iframe

async def collect_scrollable_cards(page: Page, max_rounds: int = 30) -> list[dict]:
    roots = await page.evaluate(SCROLL_ROOTS_JS)
    logger.info(f"检测到滚动容器: {len(roots)} 个")
    merged: dict[str, dict] = {}

    for root in roots:
        scope_id = root["scope_id"]
        logger.info(f"开始扫描容器: {root['label']} ({scope_id})")
        if scope_id == "window":
            await page.evaluate("window.scrollTo(0, 0)")
        else:
            await page.evaluate(
                """(scopeId) => {
                    const el = document.querySelector(`[data-autovisor-scroll-id="${scopeId}"]`);
                    if (el) el.scrollTop = 0;
                }""",
                scope_id,
            )
        await page.wait_for_timeout(400)

        last_signature = None
        stable_rounds = 0
        for _ in range(max_rounds):
            result = await page.evaluate(CARD_SCAN_SCOPE_JS, scope_id)
            cards = result["cards"]
            for card in cards:
                key = card["key"]
                prev = merged.get(key)
                if not prev or card["progress"] != prev["progress"] or card["top"] < prev["top"]:
                    merged[key] = card

            signature = (scope_id, len(cards), result["scrollTop"], result["scrollHeight"])
            if signature == last_signature:
                stable_rounds += 1
            else:
                stable_rounds = 0
            last_signature = signature

            bottom_reached = result["scrollTop"] + result["viewportHeight"] >= result["scrollHeight"] - 8
            if bottom_reached and stable_rounds >= 1:
                break

            step = max(240, int(result["viewportHeight"] * 0.85))
            if scope_id == "window":
                await page.evaluate("(step) => window.scrollBy(0, step)", step)
            else:
                await page.evaluate(
                    """([scopeId, step]) => {
                        const el = document.querySelector(`[data-autovisor-scroll-id="${scopeId}"]`);
                        if (el) el.scrollBy(0, step);
                    }""",
                    [scope_id, step],
                )
            await page.wait_for_timeout(650)
        logger.info(f"容器扫描完成: {root['label']} | 当前累计卡片 {len(merged)}")

    await page.evaluate("window.scrollTo(0, 0)")
    for root in roots:
        scope_id = root["scope_id"]
        if scope_id == "window":
            continue
        await page.evaluate(
            """(scopeId) => {
                const el = document.querySelector(`[data-autovisor-scroll-id="${scopeId}"]`);
                if (el) el.scrollTop = 0;
            }""",
            scope_id,
        )
    await page.wait_for_timeout(300)
    cards = list(merged.values())
    cards.sort(key=lambda item: (item["progress"], item["top"], item["left"]))
    return cards

async def scan_pending_lessons_deep(page: Page) -> tuple[list[dict], dict]:
    cards = await collect_scrollable_cards(page)
    summary = summarize_cards(cards)
    pending = [card for card in cards if card["progress"] < 100]
    return pending, summary

async def click_card_by_id(
    page: Page,
    card_id: str,
    title: str | None = None,
    is_in_iframe: bool = False,
    *,
    scope_id: str | None = None,
    top: float | int | None = None,
) -> bool:
    selector = f'[data-autovisor-card-id="{card_id}"]'
    
    # 获取操作上下文（主页面或 iframe Frame）
    context = page
    if is_in_iframe:
        try:
            for frame in page.frames:
                if await frame.locator('.chapter-item').count() > 0:
                    context = frame
                    break
        except Exception:
            pass

    if scope_id is not None and isinstance(top, (int, float)):
        try:
            await page.evaluate(
                """([scopeId, cardTop]) => {
                    const offset = Math.max(0, cardTop - 120);
                    if (scopeId === 'window') {
                        window.scrollTo(0, offset);
                        return;
                    }
                    const root = document.querySelector(
                        `[data-autovisor-scroll-id="${scopeId}"]`
                    );
                    if (root) root.scrollTop = offset;
                }""",
                [scope_id, top],
            )
            await page.wait_for_timeout(300)
        except Exception:
            pass
    
    # 展开该卡片所在的折叠章节
    try:
        await context.evaluate("""([cardId]) => {
            const card = document.querySelector(`[data-autovisor-card-id="${cardId}"]`);
            if (!card) return;
            const collapseItem = card.closest('.el-collapse-item');
            if (!collapseItem) return;
            let header = null, wrap = null;
            for (const child of collapseItem.children) {
                if (child.matches('.el-collapse-item__header')) header = child;
                if (child.matches('.el-collapse-item__wrap')) wrap = child;
            }
            if (header && !header.classList.contains('is-active')) {
                header.classList.add('is-active');
                header.setAttribute('aria-expanded', 'true');
                if (wrap) {
                    wrap.style.setProperty('display', 'block', 'important');
                    wrap.removeAttribute('aria-hidden');
                }
            }
        }""", [card_id])
        await page.wait_for_timeout(500)
    except Exception:
        pass
    
    try:
        locator = context.locator(selector).first
        if await locator.count():
            await locator.scroll_into_view_if_needed()
            await page.wait_for_timeout(300)
            # test-box 本身不能点击导航，需点击其父级或文本
            if await locator.evaluate("el => el.classList.contains('test-box')"):
                logger.write_log(f"test-box 点击策略: 尝试多层点击\n")
                clicked = False
                # 策略1: 点击 test-box 内的 <span>文本
                try:
                    span = locator.locator("span").first
                    if await span.count() > 0:
                        await span.click(timeout=2000)
                        clicked = True
                        logger.write_log("test-box 点击成功: span\n")
                except Exception:
                    pass
                # 策略2: 点击祖先 .chapter-item
                if not clicked:
                    try:
                        chapter = locator.locator("xpath=ancestor::*[contains(@class,'chapter-item')]").first
                        if await chapter.count() > 0:
                            await chapter.click(timeout=2000)
                            clicked = True
                            logger.write_log("test-box 点击成功: chapter-item\n")
                    except Exception:
                        pass
                # 策略3: 直接点击 test-box 自身
                if not clicked:
                    try:
                        await locator.click(timeout=2000)
                        clicked = True
                        logger.write_log("test-box 点击成功: 自身\n")
                    except Exception:
                        pass
                # 策略4: 页面全局搜索"测试"文字点击
                if not clicked:
                    try:
                        test_text = context.get_by_text("测试", exact=True).first
                        if await test_text.count() > 0:
                            await test_text.click(timeout=2000)
                            clicked = True
                            logger.write_log("test-box 点击成功: get_by_text 测试\n")
                    except Exception:
                        pass
                if not clicked:
                    logger.write_log("test-box 点击失败: 所有策略均未生效\n")
                    return False
            else:
                await locator.click(timeout=3000)
            return True
    except Exception:
        pass

    if title:
        try:
            title_locator = context.get_by_text(title, exact=True).first
            if await title_locator.count():
                await title_locator.scroll_into_view_if_needed()
                await page.wait_for_timeout(300)
                await title_locator.click(timeout=3000)
                return True
        except Exception:
            pass
    return False

def save_cookies(cookies, filename="cookies.json"):
    """保存登录Cookies到文件"""
    with open(filename, 'w') as f:
        json.dump(cookies, f)

def load_cookies(filename="cookies.json"):
    """从文件加载 Cookies"""
    try:
        with open(filename, 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        return None
    except json.JSONDecodeError:
        return None

# 将python终端前置
def bring_console_to_front():
    # 获取当前控制台窗口句柄
    hwnd = ctypes.windll.kernel32.GetConsoleWindow()
    if hwnd:
        ctypes.windll.user32.ShowWindow(hwnd, 5)  # SW_SHOW
        ctypes.windll.user32.SetForegroundWindow(hwnd)


async def display_window(page: Page) -> None:
    window = await get_browser_window(page, retries=20, delay_ms=400)
    if window:
        try:
            if window.isMinimized:
                window.restore()
                await page.wait_for_timeout(300)
        except Exception:
            pass
        window.moveTo(100, 100)
        logger.info("播放窗口已自动前置.", shift=True)
    else:
        logger.warn("未找到播放窗口!")


async def hide_window(page: Page) -> Win32Window | None:
    window = await get_browser_window(page, retries=20, delay_ms=400)
    if window:
        try:
            if window.isMinimized:
                window.restore()
                await page.wait_for_timeout(300)
        except Exception:
            pass
        try:
            window.moveTo(-32000, -32000)
        except Exception:
            window.moveTo(-3200, -3200)
        logger.info("播放窗口已自动隐藏.")
    else:
        logger.warn("未找到播放窗口!")


    return window


def is_playwright_window(window: Win32Window) -> bool:
    try:
        return (window.title or "").strip() == PLAYWRIGHT_WINDOW_TITLE
    except Exception:
        return False


async def get_browser_window(page: Page, retries: int = 10, delay_ms: int = 500) -> Win32Window | None:
    custom_title = PLAYWRIGHT_WINDOW_TITLE
    await page.wait_for_load_state("domcontentloaded")
    try:
        await page.evaluate(f'document.title = "{custom_title}"')
    except Exception:
        pass
    # 获取所有窗口并尝试匹配 Playwright 窗口
    for _ in range(max(retries, 1)):
        await page.wait_for_timeout(delay_ms)
        win_list = gw.getWindowsWithTitle(custom_title)
        for window in win_list:
            if is_playwright_window(window):
                return window
    return None


async def evaluate_js(page: Page, wait_selector, js: str, timeout=None, is_hike_class=False) -> None:
    try:
        if wait_selector and is_hike_class is False:
            await page.wait_for_selector(wait_selector, timeout=timeout)
        if is_hike_class is False:
            await page.evaluate(js)
    except Exception as e:
        logger.write_log(f"Exec JS failed: {js} Selector:{wait_selector} Error:{repr(e)}\n")
        logger.write_log(traceback.format_exc())
        return


async def evaluate_on_element(page: Page, selector: str, js: str, timeout: float = None,
                              is_hike_class=False) -> None:
    try:
        if selector and is_hike_class is False:
            element = page.locator(selector).first
            await element.evaluate(js, timeout=timeout)
    except Exception as e:
        logger.write_log(f"Exec JS failed: Selector:{selector} JS:{js} Error:{repr(e)}\n")
        logger.write_log(traceback.format_exc())
        return


async def optimize_page(page: Page, config: Config, is_new_version=False, is_hike_class=False, is_national_wisdom=False, is_meeting_class=False) -> None:
    try:
        if not is_hike_class and not is_national_wisdom and not is_meeting_class:
            await evaluate_js(page, ".studytime-div", config.pop_js, timeout=3000, is_hike_class=is_hike_class)
        if not is_new_version:
            if not is_hike_class and not is_national_wisdom and not is_meeting_class:
                hour = time.localtime().tm_hour
                if hour >= 18 or hour < 7:
                    await evaluate_on_element(page, ".Patternbtn-div", "el=>el.click()", timeout=1500)
                await evaluate_on_element(page, ".exploreTip", "el=>el.remove()", timeout=1500)
                await evaluate_on_element(page, ".ai-helper-Index2", "el=>el.remove()", timeout=1500)
                await evaluate_on_element(page, ".aiMsg.once", "el=>el.remove()", timeout=1500)
                logger.info("页面优化完成!")

    except Exception as e:
        logger.write_log(f"Exec optimize_page failed. Error:{repr(e)}\n")
        logger.write_log(traceback.format_exc())
        return


async def get_video_attr(page, attr: str) -> any:
    try:
        await page.wait_for_selector("video", state="attached", timeout=1000)
        attr = await page.evaluate(f'''document.querySelector('video').{attr}''')
        return attr
    except Exception as e:
        logger.write_log(f"Exec get_video_attr failed. Error:{repr(e)}\n")
        logger.write_log(traceback.format_exc())
        return None


async def get_lesson_name(page: Page, is_hike_class=False, is_national_wisdom=False) -> str:
    if is_hike_class:
        title_ele = await page.wait_for_selector("span")
        await page.wait_for_timeout(500)
        title = await title_ele.get_attribute("title")
    elif is_national_wisdom:
        try:
            try:
                for frame in page.frames:
                    current_item = frame.locator('.chapter-item.current .item-name').first
                    if await current_item.count() > 0:
                        title = await current_item.text_content()
                        if title and title.strip():
                            return title.strip()
                        break
            except Exception:
                pass
            
            # 如果 iframe 中找不到，尝试从主页面获取
            title_selectors = ["#lessonOrder", ".lesson-title", ".current-lesson", "h1", "h2", ".course-title"]
            for selector in title_selectors:
                try:
                    title_ele = await page.wait_for_selector(selector, timeout=2000)
                    title = await title_ele.get_attribute("title") or await title_ele.text_content()
                    if title and title.strip():
                        return title.strip()
                except TimeoutError:
                    continue
            return None  # 返回 None 让调用方使用备用值
        except Exception:
            return None  # 返回 None 让调用方使用备用值
    else:
        try:
            chapter_ele = await page.wait_for_selector(".videotop_lesson", timeout=3000)
            chapter_name = await chapter_ele.get_attribute("title") or await chapter_ele.text_content()
        except Exception:
            chapter_name = ""

        title_ele = await page.wait_for_selector("#lessonOrder")
        await page.wait_for_timeout(500)
        lesson_name = await title_ele.get_attribute("title")

        if chapter_name and chapter_name.strip():
            title = f"{chapter_name.strip()} - {lesson_name.strip()}" if lesson_name else chapter_name.strip()
        else:
            title = lesson_name
    return title


async def expand_wisdom_chapters(page: Page) -> None:
    """智慧课专用：展开所有折叠的章节"""
    try:
        await page.wait_for_timeout(3000)
        # 尝试多种可能的选择器
        selectors = [
            '.chapter-wrapper .chapter-first button[id^="el-collapse-head"]',
            'button[id^="el-collapse-head"]',
            '.el-collapse-head',
            '[aria-expanded]'
        ]
        headers = None
        for sel in selectors:
            headers = page.locator(sel)
            count = await headers.count()
            if count > 0:
                logger.write_log(f"智慧课 - 使用选择器 '{sel}' 找到 {count} 个章节\n")
                break

        if not headers or await headers.count() == 0:
            logger.write_log("智慧课 - 未找到章节折叠按钮，跳过展开步骤\n")
            return

        expanded = 0
        for i in range(await headers.count()):
            try:
                hdr = headers.nth(i)
                expanded_attr = await hdr.get_attribute('aria-expanded')
                if expanded_attr != 'true':
                    await hdr.click(timeout=2000)
                    await page.wait_for_timeout(300)
                expanded += 1
            except Exception:
                continue
        logger.write_log(f"智慧课 - 展开 {expanded} 个章节\n")
    except Exception as e:
        logger.write_log(f"智慧课 - 展开章节失败: {repr(e)}\n")


async def get_filtered_class(page: Page, is_new_version=False, is_hike_class=False, is_national_wisdom=False, include_all=False) -> List[Locator]:
    try:
        if is_new_version:
            await page.wait_for_selector(".progress-num", timeout=2000)
        elif is_hike_class:
            await page.wait_for_selector(".icon-finish", timeout=2000)
        else:
            await page.wait_for_selector(".time_icofinish", timeout=2000)
    except TimeoutError:
        pass

    if is_hike_class:
        all_class = await page.locator(".file-item").all()
        if include_all:
            pass
            # logger.write_log(f"Get to-review class: {len(all_class)}\n")
            # return all_class
        else:
            to_learn_class = []
            for each in all_class:
                isDone = await each.locator(".icon-finish").count()
                if not isDone:
                    to_learn_class.append(each)
            logger.write_log(f"Get to-learn class: {len(all_class)}\n")
            return to_learn_class
    else:
        # 【修改】同时获取视频项和测验项
        all_video = await page.locator(".clearfix.video").all()
        all_tests = await page.locator(".chapter-test").all()
        
        # 合并所有项目（视频在前，测验在后）
        all_class = all_video + all_tests
        
        if include_all:
            logger.write_log(f"Get all items: {len(all_video)} videos + {len(all_tests)} tests = {len(all_class)} total\n")
            return all_class
        else:
            to_learn_class = []
            
            # 处理视频项
            for each in all_video:
                if is_new_version:
                    progress = await each.locator(".progress-num").text_content()
                    isDone = progress == "100%"
                else:
                    isDone = await each.locator(".time_icofinish").count()
                if not isDone:
                    to_learn_class.append(each)
            
            # 处理测验项（只添加未完成的）
            for each in all_tests:
                isDone = await each.locator("b.finish").count()
                if not isDone:
                    to_learn_class.append(each)
            
            # 统计视频和测验数量
            video_count = 0
            test_count = 0
            for item in to_learn_class:
                class_attr = await item.get_attribute('class')
                if class_attr:
                    if 'video' in class_attr:
                        video_count += 1
                    elif 'chapter-test' in class_attr:
                        test_count += 1
            
            logger.write_log(f"Get to-learn items: {len(to_learn_class)} (videos: {video_count}, tests: {test_count})\n")
            return to_learn_class


async def scan_normal_class_tests(page: Page, is_new_version: bool = False) -> list[dict]:
    """扫描普通课页面中的测验项
    
    返回值：[{title, completed, element, index, type: 'test'}, ...]
    
    HTML结构示例：
    - 已完成测验: <li class="chapter-test">...<b class="finish"></b>...</li>
    - 未完成测验: <li class="chapter-test">...<span class="iconfont ..."></span>...</li>
    """
    tests = []
    try:
        test_items = await page.locator(".chapter-test").all()
        
        for i, item in enumerate(test_items):
            try:
                text = await item.text_content()
                if not text:
                    continue
                
                title_el = item.locator(".name").first
                title = await title_el.text_content() if await title_el.count() > 0 else "平时测试"
                title = title.strip()
                
                completed = await item.locator("b.finish").count() > 0
                
                # 设置唯一标识
                try:
                    await item.evaluate(f'el => el.dataset.autovisorTestId = "test-{i}"')
                except Exception:
                    pass
                
                tests.append({
                    "title": title,
                    "completed": completed,
                    "index": i,
                    "type": "test",
                    "element": item
                })
                
                logger.write_log(f"测验项: {title} - {'已完成' if completed else '未完成'}\n")
                
            except Exception as e:
                logger.write_log(f"扫描测验项失败: {e}\n")
                continue
        
        if tests:
            logger.info(f"扫描到 {len(tests)} 个测验项 (已完成: {sum(1 for t in tests if t['completed'])}, 未完成: {sum(1 for t in tests if not t['completed'])})")
        
    except Exception as e:
        logger.warn(f"扫描测验失败: {str(e)[:50]}")
    
    return tests


async def detect_test_in_current_lesson(page: Page) -> bool:
    """检测当前课程是否包含测验
    
    返回值：True 如果检测到测验按钮或链接
    """
    try:
        test_selectors = [
            "button:has-text('测验')",
            "button:has-text('考试')",
            "a:has-text('测验')",
            "a:has-text('考试')",
            ".test-btn",
            ".exam-btn",
            "[class*='test-btn']",
            "[class*='exam-btn']"
        ]
        
        for selector in test_selectors:
            count = await page.locator(selector).count()
            if count > 0:
                logger.info(f"检测到测验按钮: {selector}")
                return True
        
        return False
        
    except Exception as e:
        logger.write_log(f"检测测验失败: {e}\n")
        return False


async def scan_meeting_class_videos(page: Page) -> list[dict]:
    """扫描见面课视频列表
    
    返回值：[{title, progress, element, index, completed}, ...]
    
    HTML结构：
    <ul id="videoList">
        <li class="videomenu current_player">
            <div class="videoCurrent"><span>1%</span></div>
            <h3 class="video_name">视频标题</h3>
            <h3 class="video_time">时长:1:00:02</h3>
        </li>
    </ul>
    """
    videos = []
    try:
        video_items = await page.locator("#videoList .videomenu").all()
        
        for i, item in enumerate(video_items):
            try:
                # 获取视频标题
                title_el = item.locator(".video_name").first
                title = await title_el.text_content() if await title_el.count() > 0 else f"视频{i+1}"
                title = title.strip()
                
                # 获取视频时长
                time_el = item.locator(".video_time").first
                duration = await time_el.text_content() if await time_el.count() > 0 else ""
                duration = duration.strip()
                
                # 获取进度
                progress_el = item.locator(".videoCurrent span").first
                progress_text = await progress_el.text_content() if await progress_el.count() > 0 else "0%"
                progress_text = progress_text.strip()
                
                # 解析进度百分比
                try:
                    progress = int(progress_text.replace("%", ""))
                except ValueError:
                    progress = 0
                
                # 判断是否完成（80%阈值 - 签到进度达到80%即完成）
                completed = progress >= 80
                
                # 检查是否是当前播放的视频
                is_current = "current_player" in (await item.get_attribute("class") or "")
                
                videos.append({
                    "title": title,
                    "duration": duration,
                    "progress": progress,
                    "completed": completed,
                    "is_current": is_current,
                    "index": i,
                    "element": item
                })
                
                logger.write_log(f"视频: {title} - 进度: {progress}% - {'已完成' if completed else '未完成'} - {'当前播放' if is_current else ''}\n")
                
            except Exception as e:
                logger.write_log(f"扫描视频项失败: {e}\n")
                continue
        
        if videos:
            completed_count = sum(1 for v in videos if v['completed'])
            logger.info(f"扫描到 {len(videos)} 个视频 (已完成: {completed_count}, 未完成: {len(videos) - completed_count})")
        
    except Exception as e:
        logger.warn(f"扫描视频列表失败: {str(e)[:50]}")
    
    return videos
