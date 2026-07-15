import os
import http.client  # 兼容外部通过 tasks.http.client 注入连接实现

from playwright.async_api import Page
from modules.logger import Logger
from modules.floating_widget import inject_widget
from modules.chapter_learning import (
    _extract_test_questions_from_dom,
    chapter_learning_flow,
    get_chapter_videos_status,
    wait_for_video_completion,
)
from modules.test_page_controls import (
    _force_click_locator,
    answer_question,
    click_next_button,
    click_option_by_index,
    click_option_by_text,
    click_prev_button,
    has_selected_answer,
    submit_exam,
    wait_for_page_option_click,
    wait_for_user_action,
    wait_for_widget_next_button,
    wait_for_widget_submit,
)
from modules import question_bank_client as _question_bank_client
from modules.in_class_questions import (
    _extract_options,
    _extract_question_title,
    _extract_wisdom_options,
    _extract_wisdom_question,
    skip_questions as _run_skip_questions,
    wait_for_question_resolution,
)
from modules.page_monitor import (
    STATUS_PROBE_JS,
    smart_click_text,
    status_ocr_stream,
    trigger_restart,
    wait_for_verify,
)
from modules.question_bank_client import (
    _build_model_query_prompt,
    _detect_question_kind,
    _extract_answer_from_json,
    _extract_last_balanced_json,
    _is_model_error,
    _normalize_qb,
)
from modules.test_capture import TestResponseHandler
from modules.test_page_flow import (
    clean_html_tags,
    handle_test_page as _run_handle_test_page,
    query_test_answers,
)
from modules.video_tasks import activate_window, play_video, task_monitor, video_optimize

logger = Logger()
QB_URL = os.environ.get("QB_URL", "http://127.0.0.1:8083/query")
QB_TIMEOUT = 15


def _question_bank_endpoint():
    return _question_bank_client._question_bank_endpoint(QB_URL)


def query_question_bank(title, options_text=None, query_type=None):
    """兼容旧入口，并把运行时覆盖的题库地址传给独立客户端。"""
    return _question_bank_client.query_question_bank(
        title,
        options_text,
        query_type,
        qb_url=QB_URL,
        timeout=QB_TIMEOUT,
        logger_instance=logger,
    )


# 页面通用点击与状态监控已拆分至 modules.page_monitor，并在顶部兼容导出。

async def skip_questions(page: Page, event_loop) -> None:
    """兼容旧入口，并传入当前 tasks 题库配置。"""
    await _run_skip_questions(
        page,
        event_loop,
        query_answer=query_question_bank,
        logger_instance=logger,
    )


# 随堂题提取与监听已拆分至 modules.in_class_questions，并在顶部兼容导出。

# 测验响应监听器已拆分至 modules.test_capture，并在本模块顶部兼容导出。


async def handle_test_page(
    page: Page,
    questions_data: list,
    auto_submit: bool = False,
    manual_submit: bool = False,
) -> bool:
    """兼容旧入口，并注入 tasks 当前可替换的页面与题库实现。"""
    return await _run_handle_test_page(
        page,
        questions_data,
        auto_submit=auto_submit,
        manual_submit=manual_submit,
        query_answer=query_question_bank,
        widget_injector=inject_widget,
        wait_action=wait_for_user_action,
        selection_checker=has_selected_answer,
        next_clicker=click_next_button,
        prev_clicker=click_prev_button,
        answer_applier=answer_question,
        submitter=submit_exam,
        logger_instance=logger,
    )


# 测验编排已拆分至 modules.test_page_flow，并在本模块保留兼容入口。

# 测验页面控件已拆分至 modules.test_page_controls，并在本模块顶部兼容导出。
