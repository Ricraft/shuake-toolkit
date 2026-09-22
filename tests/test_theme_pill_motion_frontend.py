# encoding=utf-8
"""需求1/2：主题按钮滑动药丸 + 滑杆圆点靠近放大（前端契约）。"""

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FRONTEND = (PROJECT_ROOT / "web" / "app.js").read_text(encoding="utf-8")
STYLES = (PROJECT_ROOT / "web" / "styles.css").read_text(encoding="utf-8")
HTML = (PROJECT_ROOT / "web" / "现代启动器_UI_预览.html").read_text(
    encoding="utf-8"
)


def function_source(name, next_name):
    return FRONTEND.split(f"function {name}", 1)[1].split(
        f"function {next_name}",
        1,
    )[0]


def css_block(selector):
    return STYLES.split(f"{selector} {{", 1)[1].split("}", 1)[0]


# --------------------------------------------------------------- 结构契约


def test_theme_choice_group_keeps_three_buttons_and_their_handlers():
    assert 'id="theme-choice-group"' in HTML
    assert HTML.count('<button type="button" class="theme-choice') == 3
    for theme in ("dark", "light", "tianyi"):
        assert f'data-theme-choice="{theme}"' in HTML
        assert f"setTheme('{theme}')" in HTML
    tianyi_button = HTML.split(
        '<button type="button" class="theme-choice tianyi-theme-option"', 1
    )[1].split(">", 1)[0]
    assert 'data-theme-choice="tianyi"' in tianyi_button
    assert "setTheme('tianyi')" in tianyi_button
    assert "hidden" in tianyi_button


def test_theme_group_has_positioned_pill_layer_with_existing_transition_vars():
    assert "position: relative;" in css_block(".theme-choice-group")
    pill = css_block(".theme-choice-group::before")
    assert "width: var(--theme-pill-w, 0px);" in pill
    assert "transform: translateX(var(--theme-pill-x, 0px));" in pill
    assert "background: var(--accent-gradient);" in pill
    assert "pointer-events: none;" in pill
    assert "opacity: 0;" in pill
    assert "transform var(--transition-normal)" in pill
    assert "width var(--transition-normal)" in pill
    assert "opacity var(--transition-fast)" in pill
    assert ".theme-choice-group.has-theme-pill::before { opacity: 1; }" in STYLES
    assert (
        ".theme-choice-group.theme-pill-instant::before { transition: none; }"
        in STYLES
    )


def test_active_button_highlight_is_handed_over_to_the_pill():
    assert "position: relative;" in css_block(".theme-choice")
    assert "z-index: 1;" in css_block(".theme-choice")
    override = css_block(".theme-choice-group.has-theme-pill .theme-choice.active")
    assert "background: transparent;" in override
    assert "box-shadow: none;" in override


def test_pill_sync_measures_only_the_active_button():
    source = FRONTEND.split("function syncThemeChoicePill", 1)[1].split(
        "function scheduleGlassBlurApply",
        1,
    )[0]
    assert "document.getElementById('theme-choice-group')" in source
    assert "group.querySelector('.theme-choice.active')" in source
    assert "!active.offsetWidth" in source
    assert "active.offsetLeft" in source
    assert "--theme-pill-w" in source
    assert "--theme-pill-x" in source
    assert "theme-pill-instant" in source
    assert "has-theme-pill" in source
    assert "requestAnimationFrame" in source


def test_pill_is_synced_from_theme_icon_updates_only():
    icons = function_source("updateThemeIcons", "toggleTheme")
    assert "syncThemeChoicePill()" in icons
    # 天依未解锁时 setTheme 提前 return，走不到 updateThemeIcons，药丸不会移动
    theme = function_source("setTheme", "updateThemeIcons")
    assert "洛天依主题还未解锁" in theme
    assert "showToast(" in theme
    assert "syncThemeChoicePill" not in theme


def test_first_paint_and_tianyi_unlock_resync_without_animation():
    switch = function_source("switchView", "switchConfigTab")
    assert "syncThemeChoicePill({ animate: false })" in switch
    assert "initSliderThumbProximity()" in switch
    sync = FRONTEND.split("function syncThemeChoicePill", 1)[1].split(
        "function scheduleGlassBlurApply",
        1,
    )[0]
    assert "ResizeObserver" in sync
    assert "themePillObserver.observe(group)" in sync
    assert "syncThemeChoicePill({ animate: false })" in sync


def test_slider_proximity_uses_distance_threshold_and_throttled_pointer_moves():
    assert "const SLIDER_THUMB_NEAR_RADIUS = 30;" in FRONTEND
    source = FRONTEND.split("function initSliderThumbProximity", 1)[1]
    assert "querySelectorAll('.opacity-slider')" in source
    assert "pointermove" in source
    assert "pointerenter" in source
    assert "pointerleave" in source
    assert "slider-active" in source
    assert "pointerdown" in source
    assert "getBoundingClientRect()" in source
    assert "Math.hypot" in source
    assert "slider-near" in source
    assert "distance <= SLIDER_THUMB_NEAR_RADIUS" in source
    assert "frame = requestAnimationFrame(evaluate)" in source


def test_slider_thumb_states_use_existing_transition_vars():
    thumb = css_block(".opacity-slider::-webkit-slider-thumb")
    assert (
        "transition: transform var(--transition-fast), box-shadow var(--transition-fast);"
        in thumb
    )
    assert ".opacity-slider.slider-near::-webkit-slider-thumb {" in STYLES
    assert ".opacity-slider.slider-active::-webkit-slider-thumb {" in STYLES
    near = css_block(".opacity-slider.slider-near::-webkit-slider-thumb")
    assert "scale(1.22)" in near
    active = css_block(".opacity-slider.slider-active::-webkit-slider-thumb")
    assert "scale(1.1)" in active
    assert ".opacity-slider.slider-near::-moz-range-thumb {" in STYLES
    assert ".opacity-slider.slider-active::-moz-range-thumb {" in STYLES


def test_glass_blur_writes_are_coalesced_into_one_frame():
    blur = function_source("updateGlassBlur", "updateOverlayStrength")
    assert "scheduleGlassBlurApply(blurValue);" in blur
    assert "persistStoredPreferences();" in blur
    assert "savePreferencesInBackground({ glassBlur: blurValue })" in blur
    schedule = FRONTEND.split("function scheduleGlassBlurApply", 1)[1].split(
        "function cancelPendingGlassBlur",
        1,
    )[0]
    assert "requestAnimationFrame" in schedule
    assert "applyGlassBlur(value);" in schedule
    cancel = FRONTEND.split("function cancelPendingGlassBlur", 1)[1].split(
        "function initSliderThumbProximity",
        1,
    )[0]
    assert "cancelAnimationFrame" in cancel
    # 唯一的逐元素内联 backdrop-filter 写入点仍然是 applyGlassBlur
    assert FRONTEND.count("card.style.backdropFilter") == 1


def test_reset_and_background_switch_discard_pending_blur_frames():
    reset = function_source("resetBackground", "markBackendDisconnected")
    assert "cancelPendingGlassBlur();" in reset
    assert "applyGlassBlur(16);" in reset
    assert "state.preferences.glassBlur = 16" in reset
    assert "if (strengthSlider) strengthSlider.value = 70;" in reset
    # 切背景时也要丢弃待执行帧，否则旧值会覆盖新背景的模糊度
    apply_bg = function_source("applyBackground", "showCustomBgInput")
    assert "cancelPendingGlassBlur();" in apply_bg
    assert "applyGlassBlur(blurValue);" in apply_bg


def test_collapsed_changelog_drops_the_trailing_divider():
    assert (
        "#changelog-list:not(.is-expanded) .changelog-item:nth-child(15) { border-bottom: none; }"
        in STYLES
    )
    assert (
        "#changelog-list:not(.is-expanded) .changelog-item:has(+ .changelog-item.is-overflow) { border-bottom: none; }"
        in STYLES
    )
    assert (
        "#changelog-list.is-expanded .changelog-item.is-overflow:last-child { border-bottom: none; }"
        in STYLES
    )


# ------------------------------------------------- 行为契约（node 直跑真实函数）

PILL_BLOCK = "let themePillObserver = null;" + FRONTEND.split(
    "let themePillObserver = null;", 1
)[1].split("function scheduleGlassBlurApply", 1)[0]

GLASS_BLUR_BLOCK = "let glassBlurFrame = 0;" + FRONTEND.split(
    "let glassBlurFrame = 0;", 1
)[1].split("// 滑杆圆点「靠近即放大」", 1)[0]


@pytest.fixture()
def sandbox_dir():
    """本机 pytest 临时基目录不可访问，这里使用独立临时目录。"""
    path = tempfile.mkdtemp(prefix="dsh-theme-pill-")
    try:
        yield Path(path)
    finally:
        shutil.rmtree(path, ignore_errors=True)


def _run_node(sandbox_dir, filename, source):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is required for the motion behaviour contract")
    harness = sandbox_dir / filename
    harness.write_text(source, encoding="utf-8")
    completed = subprocess.run(
        [node, str(harness)],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


GLASS_BLUR_HARNESS = r"""
const frameQueue = new Map();
let nextFrameId = 1;
function requestAnimationFrame(cb) {
  const id = nextFrameId;
  nextFrameId += 1;
  frameQueue.set(id, cb);
  return id;
}
function cancelAnimationFrame(id) { frameQueue.delete(id); }
const writes = [];
function applyGlassBlur(value) { writes.push(value); }
const runFrames = () => {
  const callbacks = [...frameQueue.values()];
  frameQueue.clear();
  callbacks.forEach((cb) => cb());
};
const out = {};

scheduleGlassBlurApply(12);
scheduleGlassBlurApply(20);
scheduleGlassBlurApply(28);
out.writesBeforeFrame = writes.slice();
out.queuedFrames = frameQueue.size;
runFrames();
out.writesAfterFrame = writes.slice();

scheduleGlassBlurApply(24);
scheduleGlassBlurApply(8);
cancelPendingGlassBlur();
runFrames();
out.writesAfterCancel = writes.slice();

scheduleGlassBlurApply(16);
runFrames();
out.writesAfterApply = writes.slice();
console.log(JSON.stringify(out));
"""


def test_glass_blur_events_collapse_into_a_single_write_per_frame(sandbox_dir):
    out = _run_node(
        sandbox_dir,
        "glass_blur_harness.js",
        GLASS_BLUR_BLOCK + GLASS_BLUR_HARNESS,
    )

    # 同一帧内 3 次 input 事件只排队一帧、且一次都没写（不再逐事件全量写）
    assert out["writesBeforeFrame"] == []
    assert out["queuedFrames"] == 1
    # 帧结束时只写一次，且写的是最新值
    assert out["writesAfterFrame"] == [28]
    # 复位前 cancel 掉待执行帧后，旧值不会覆盖
    assert out["writesAfterCancel"] == [28]
    assert out["writesAfterApply"] == [28, 16]


PILL_HARNESS = r"""
const observedGroups = [];
class ResizeObserver {
  constructor(callback) { this.callback = callback; }
  observe(element) { observedGroups.push(element); }
}
const frameQueue = new Map();
let nextFrameId = 1;
function requestAnimationFrame(cb) { const id = nextFrameId; nextFrameId += 1; frameQueue.set(id, cb); return id; }
function cancelAnimationFrame(id) { frameQueue.delete(id); }
const runFrames = () => {
  const callbacks = [...frameQueue.values()];
  frameQueue.clear();
  callbacks.forEach((cb) => cb());
};
const makeClassList = (set) => ({
  add: (name) => { set.add(name); },
  remove: (name) => { set.delete(name); },
  contains: (name) => set.has(name),
  toggle: (name, force) => {
    const want = force === undefined ? !set.has(name) : !!force;
    if (want) { set.add(name); } else { set.delete(name); }
    return want;
  },
});
const groupClasses = new Set();
const groupStyle = {};
const buttons = {
  dark: { offsetLeft: 4, offsetWidth: 74 },
  light: { offsetLeft: 84, offsetWidth: 74 },
  tianyi: { offsetLeft: 164, offsetWidth: 92 },
  hidden: { offsetLeft: 0, offsetWidth: 0 },
};
let activeButton = buttons.dark;
const group = {
  classList: makeClassList(groupClasses),
  style: { setProperty: (name, value) => { groupStyle[name] = value; } },
  querySelector: (selector) => (selector === '.theme-choice.active' ? activeButton : null),
  offsetWidth: 260,
};
const document = {
  getElementById: (id) => (id === 'theme-choice-group' ? group : null),
};
const snapshot = () => Object.assign({}, groupStyle);
const out = {};

out.firstSync = syncThemeChoicePill({ animate: false });
out.firstVars = snapshot();
out.instantDuringFirstSync = groupClasses.has('theme-pill-instant');
out.pillVisible = groupClasses.has('has-theme-pill');
runFrames();
out.instantAfterFirstFrame = groupClasses.has('theme-pill-instant');

activeButton = buttons.light;
out.animatedSync = syncThemeChoicePill();
out.animatedVars = snapshot();
out.instantDuringAnimatedSync = groupClasses.has('theme-pill-instant');
out.observerCount = observedGroups.length;

activeButton = buttons.tianyi;
out.tianyiSync = syncThemeChoicePill();
out.tianyiVars = snapshot();

activeButton = buttons.hidden;
out.hiddenViewSync = syncThemeChoicePill();
out.hiddenViewVars = snapshot();
out.observerCountAfterHidden = observedGroups.length;
console.log(JSON.stringify(out));
"""


def test_theme_pill_geometry_follows_the_active_button_at_runtime(sandbox_dir):
    out = _run_node(
        sandbox_dir,
        "theme_pill_harness.js",
        PILL_BLOCK + PILL_HARNESS,
    )

    assert out["firstSync"] is True
    assert out["firstVars"] == {"--theme-pill-w": "74px", "--theme-pill-x": "4px"}
    assert out["instantDuringFirstSync"] is True
    assert out["pillVisible"] is True
    assert out["instantAfterFirstFrame"] is False
    # 点击切换主题时只更新变量、不加瞬时类 => 由 CSS transition 完成滑动
    assert out["animatedSync"] is True
    assert out["animatedVars"] == {"--theme-pill-w": "74px", "--theme-pill-x": "84px"}
    assert out["instantDuringAnimatedSync"] is False
    # 洛天依解锁后按钮更宽/更靠右，宽度与位移都要跟着变
    assert out["tianyiVars"] == {"--theme-pill-w": "92px", "--theme-pill-x": "164px"}
    # 设置页不可见（offsetWidth 为 0）时不测量、不写变量
    assert out["hiddenViewSync"] is False
    assert out["hiddenViewVars"] == out["tianyiVars"]
    assert out["observerCount"] == 1
    assert out["observerCountAfterHidden"] == 1


PROXIMITY_BLOCK = "const sliderProximityZones = new WeakSet();" + FRONTEND.split(
    "const sliderProximityZones = new WeakSet();", 1
)[1].split("// 这里刻意不注册 DOMContentLoaded", 1)[0]

PROXIMITY_HARNESS = r"""
const makeClassList = (set) => ({
  add: (name) => { set.add(name); },
  remove: (name) => { set.delete(name); },
  contains: (name) => set.has(name),
  toggle: (name, force) => {
    const want = force === undefined ? !set.has(name) : !!force;
    if (want) { set.add(name); } else { set.delete(name); }
    return want;
  },
});
const frameQueue = new Map();
let nextFrameId = 1;
let rafCalls = 0;
function requestAnimationFrame(cb) { rafCalls += 1; const id = nextFrameId; nextFrameId += 1; frameQueue.set(id, cb); return id; }
function cancelAnimationFrame(id) { frameQueue.delete(id); }
const runFrames = () => {
  const callbacks = [...frameQueue.values()];
  frameQueue.clear();
  callbacks.forEach((cb) => cb());
};

const zoneListeners = {};
const sliderListeners = {};
const windowListeners = {};
const zone = {
  addEventListener: (type, handler) => { (zoneListeners[type] = zoneListeners[type] || []).push(handler); },
};
const sliderClasses = new Set();
const slider = {
  min: '0',
  max: '40',
  value: '16',
  classList: makeClassList(sliderClasses),
  getBoundingClientRect: () => ({ left: 100, top: 200, width: 200, height: 6 }),
  closest: () => zone,
  addEventListener: (type, handler) => { (sliderListeners[type] = sliderListeners[type] || []).push(handler); },
};
const document = {
  querySelectorAll: (selector) => (selector === '.opacity-slider' ? [slider] : []),
  addEventListener: () => {},
};
const window = {
  addEventListener: (type, handler) => { (windowListeners[type] = windowListeners[type] || []).push(handler); },
};
const fireZone = (type, event) => (zoneListeners[type] || []).forEach((handler) => handler(event));
const fireSlider = (type, event) => (sliderListeners[type] || []).forEach((handler) => handler(event));
const fireWindow = (type, event) => (windowListeners[type] || []).forEach((handler) => handler(event));

// 圆点中心 = left + 9 + (16/40) * (200 - 18) = 181.8，中心 y = 203
const out = {};
initSliderThumbProximity();

fireZone('pointerenter', { clientX: 320, clientY: 203 });
runFrames();
out.nearWhenFar = sliderClasses.has('slider-near');

rafCalls = 0;
fireZone('pointermove', { clientX: 212, clientY: 203 });
fireZone('pointermove', { clientX: 210, clientY: 203 });
fireZone('pointermove', { clientX: 195, clientY: 203 });
out.rafCallsForThreeMoves = rafCalls;
runFrames();
out.nearWhenClose = sliderClasses.has('slider-near');

fireSlider('pointerdown');
out.activeWhileDragging = sliderClasses.has('slider-active');
out.nearStaysWhileDragging = sliderClasses.has('slider-near');

fireSlider('pointerup');
out.activeAfterRelease = sliderClasses.has('slider-active');

fireSlider('pointerdown');
fireWindow('pointerup');
out.activeAfterWindowRelease = sliderClasses.has('slider-active');

fireZone('pointermove', { clientX: 181, clientY: 203 });
runFrames();
out.nearOnThumb = sliderClasses.has('slider-near');
fireZone('pointerleave');
out.nearAfterLeave = sliderClasses.has('slider-near');
console.log(JSON.stringify(out));
"""


def test_slider_proximity_threshold_and_drag_state_at_runtime(sandbox_dir):
    out = _run_node(
        sandbox_dir,
        "slider_proximity_harness.js",
        PROXIMITY_BLOCK + PROXIMITY_HARNESS,
    )

    # 离圆点 138px：不放大
    assert out["nearWhenFar"] is False
    # 同一帧内 3 次 pointermove 只排一次 rAF（节流）
    assert out["rafCallsForThreeMoves"] == 1
    # 最后一次位于圆点 13px 内：放大
    assert out["nearWhenClose"] is True
    # 按住拖动期间保持放大，且带 slider-active 抓取态
    assert out["activeWhileDragging"] is True
    assert out["nearStaysWhileDragging"] is True
    assert out["activeAfterRelease"] is False
    # 在窗口层面松手也能收尾（拖到面板外松手不会卡住）
    assert out["activeAfterWindowRelease"] is False
    assert out["nearOnThumb"] is True
    # 移开后恢复（视觉复原由 CSS var(--transition-fast) 过渡完成）
    assert out["nearAfterLeave"] is False
