# encoding=utf-8
"""官方生成器 UI 契约：Node 执行模式、配置回传，不启动真实核心。"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest


SOURCE = (Path(__file__).resolve().parents[1] / "web" / "app.js").read_text(encoding="utf-8")
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node unavailable")


def run_js(body):
    helpers = SOURCE[SOURCE.index("        const YATORI_PLATFORM_OPTIONS ="):SOURCE.index("        function normalizeAutovisorAccount(")]
    gather = SOURCE[SOURCE.index("        function gatherYatoriSettings()"):SOURCE.index("        function gatherAutovisorSettings()")]
    sync = SOURCE[SOURCE.index("        function syncYatoriPlatformCard("):SOURCE.index("        function gatherYatoriSettings()")]
    script = "const escapeHtml = v => String(v ?? '');\n" + helpers + sync + gather + "\n" + body
    process = subprocess.run([NODE, "-e", script], text=True, encoding="utf-8", capture_output=True, timeout=30)
    assert process.returncode == 0, process.stderr
    return json.loads(process.stdout)


def test_ten_platform_options_and_zero_video_round_trip():
    result = run_js("""
        const platforms = YATORI_PLATFORM_OPTIONS.map(({value}) => ({
            platform: value,
            label: YATORI_PLATFORM_OPTIONS.find(item => item.value === value).label,
            video: getYatoriPlatformModeRule(value).videoModes,
            exam: getYatoriPlatformModeRule(value).examModes,
            labels: renderYatoriVideoOptions(value, '0'),
        }));
        console.log(JSON.stringify({platforms,
            zero: normalizeYatoriUser({coursesCustom:{videoModel:0}}).coursesCustom.videoModel,
            node: renderYatoriXxtSettings({coursesCustom:{cxNode:-1}},true)}));
    """)
    assert len(result["platforms"]) == 10
    labels = {p["platform"]: p["label"] for p in result["platforms"]}
    assert labels["CANGHUI"] == "仓辉"
    assert labels["CQIE"] == "重庆工学院"
    assert labels["ENAEA"] == "学习公社(ENAEA)"
    assert labels["WELEARN"] == "随行课堂(Welearn)"
    for platform in result["platforms"]:
        assert platform["video"][:3] == ["0", "1", "2"]
        assert '<option value="0" selected>' in platform["labels"]
        name = platform["platform"]
        assert platform["video"] == (["0", "1", "2", "3"] if name in ("YINGHUA", "XUEXITONG") else ["0", "1", "2"])
        assert platform["exam"] == (["0"] if name == "HQKJ" else ["0", "1", "2", "3"] if name == "XUEXITONG" else ["0", "1", "2"])
    assert result["zero"] == 0
    assert 'min="-1" max="9999"' in result["node"]
    assert 'value="-1"' in result["node"]
    assert "不是章节数" in result["node"]


def test_settings_gather_preserves_both_course_lists_ai_question_bank_and_modes():
    result = run_js("""
        const values = {
            accountType:'XUEXITONG', videoModel:'0', autoExam:'3', examAutoSubmit:'1', cxNode:'-1',
            studyTime:'10-30', includeCourses:'包含A\\n包含B', excludeCourses:'排除C',
            url:'', remarkName:'test',account:'user',password:'password',informEmails:'',
            isProxy:false,shuffleSw:false,cxChapterTestSw:true,cxWorkSw:false,cxExamSw:true
        };
        const card = {querySelector(selector){const name=selector.match(/data-field="([^"]+)"/)[1];return {value:values[name],checked:values[name]};}};
        const globalFields = {
            'y-completion-tone':{checked:true},'y-color-log':{checked:true},'y-log-out-file':{checked:false},
            'y-log-level':{value:'INFO'},'y-log-model':{value:'0'},'y-web-model':{value:'0'},
            'y-email-sw':{checked:false},'y-smtp-host':{value:''},'y-smtp-port':{value:'0'},
            'y-email-user':{value:''},'y-email-password':{value:''},
            'y-ai-type':{value:'OPENAI'},'y-ai-url':{value:'https://model.example'},
            'y-ai-model':{value:'model-name'},'y-ai-api-key':{value:'secret'},
            'y-api-url':{value:'http://localhost:8083'}
        };
        const document={querySelectorAll:()=>[card], getElementById:id=>globalFields[id]};
        const splitLines=s=>s.split('\\n').map(v=>v.trim()).filter(Boolean);
        const splitComma=s=>s.split(',').map(v=>v.trim()).filter(Boolean);
        const state={settings:{yatori:{users:[normalizeYatoriUser({coursesCustom:{coursesSettings:[{courseName:'legacy'}]}},1)]}}};
        const outputs=[];
        for (const exam of ['0','1','2','3']) {
            values.autoExam=exam;
            outputs.push(gatherYatoriSettings());
        }
        console.log(JSON.stringify(outputs));
    """)
    assert [r["users"][0]["coursesCustom"]["autoExam"] for r in result] == [0, 1, 2, 3]
    for r in result:
        user = r["users"][0]["coursesCustom"]
        assert user["videoModel"] == 0
        assert user["examAutoSubmit"] == 1
        assert user["cxNode"] == -1
        assert user["includeCourses"] == ["包含A", "包含B"]
        assert user["excludeCourses"] == ["排除C"]
        assert user["coursesSettings"] == [{"courseName": "legacy"}]
        assert r["setting"]["apiQueSetting"]["url"] == "http://localhost:8083"
        assert r["setting"]["aiSetting"]["model"] == "model-name"
        assert r["setting"]["aiSetting"]["API_KEY"] == "secret"


def test_visibility_and_course_selection_only_updates_include():
    sync = SOURCE[SOURCE.index("function syncYatoriPlatformCard("):SOURCE.index("function gatherYatoriSettings()")]
    selection = SOURCE[SOURCE.index("async function confirmXuexitongCourseSelection("):SOURCE.index("function syncYatoriPlatformCard(")]
    assert "pc === 'XUEXITONG' && video?.value === '3'" in sync
    assert "if (chapter) chapter.style.display = hasExam" in sync
    assert "if (examWrap) examWrap.style.display = pc === 'HQKJ'" in sync
    assert "const hasExam = exam?.value !== '0'" in sync
    assert "card.querySelector('[data-field=\"includeCourses\"]')" in selection
    assert "excludeCourses" not in selection
    assert "data-filter-mode" not in SOURCE[SOURCE.index("function renderYatoriAccounts("):SOURCE.index("let courseFetchRunning")]


def test_sync_visibility_changes_with_platform_video_and_exam_mode():
    result = run_js("""
        const elements={};
        for (const role of ['platform-badge','url-hint','exam-hint','exam-mode-wrap',
                            'submit-wrap','exam-chapter-switches','xxt-study-time','xxt-cx-node']) {
            elements['[data-role="'+role+'"]']={style:{}};
        }
        elements['[id^="xxt-course-btn-"]']={style:{}};
        for(const field of ['accountType','videoModel','autoExam','examAutoSubmit']) {
            let selected = field==='accountType'?'XUEXITONG':'0';
            elements['[data-field="'+field+'"]']={
                get value(){return selected;}, set value(value){selected=value;},
                set innerHTML(html){
                    const options=[...html.matchAll(/<option value="([^"]+)"/g)].map(m=>m[1]);
                    if(!options.includes(selected))selected=options[0];
                }, style:{}
            };
        }
        const card={querySelector(selector){return elements[selector]||null;}};
        const view=()=>({node:elements['[data-role="xxt-cx-node"]'].style.display,
            chapters:elements['[data-role="exam-chapter-switches"]'].style.display,
            submit:elements['[data-role="submit-wrap"]'].style.display,
            exam:elements['[data-role="exam-mode-wrap"]'].style.display,
            examValue:elements['[data-field="autoExam"]'].value});
        syncYatoriPlatformCard(card); const idle=view();
        elements['[data-field="videoModel"]'].value='3';
        elements['[data-field="autoExam"]'].value='2';
        syncYatoriPlatformCard(card); const active=view();
        elements['[data-field="accountType"]'].value='CANGHUI';
        syncYatoriPlatformCard(card); const canghui=view();
        elements['[data-field="accountType"]'].value='HQKJ';
        syncYatoriPlatformCard(card); const hqkj=view();
        console.log(JSON.stringify({idle,active,canghui,hqkj}));
    """)
    assert result["idle"]["node"] == "none"
    assert result["idle"]["chapters"] == "none"
    assert result["idle"]["submit"] == "none"
    assert result["active"]["node"] == ""
    assert result["active"]["chapters"] == ""
    assert result["active"]["submit"] == ""
    assert result["canghui"]["node"] == "none"
    assert result["canghui"]["chapters"] == ""
    assert result["canghui"]["examValue"] == "2"
    assert result["hqkj"]["exam"] == "none"
    assert result["hqkj"]["chapters"] == "none"
    assert result["hqkj"]["examValue"] == "0"


def _toggle_script():
    xxt = SOURCE[SOURCE.index("        function xxtToggleCourseItem("):SOURCE.index("        function xxtSelectAllCourses(")]
    zhs = SOURCE[SOURCE.index("        function toggleCourseItem("):SOURCE.index("        function selectAllCourses(")]
    return xxt + "\n" + zhs


def test_course_checkbox_is_clickable_without_double_toggle():
    script = _toggle_script() + """
        let summaryUpdates = 0;
        function xxtUpdateCourseSelectSummary() { summaryUpdates += 1; }
        function row(options) {
            const { ended = false, disabled = false, checked = false } = options || {};
            const cb = { checked, disabled };
            cb.contains = t => t === cb;
            return {
                row: {
                    classList: { contains: name => ended && name === 'course-select-item-ended' },
                    querySelector: selector => selector === 'input[type="checkbox"]' ? cb : null,
                },
                cb,
            };
        }
        const out = {};
        { const it = row({}); xxtToggleCourseItem(it.row, { target: it.row }); out.rowClick = it.cb.checked; }
        { const it = row({}); xxtToggleCourseItem(it.row, { target: it.cb }); out.boxClick = it.cb.checked; }
        { const it = row({ ended: true }); xxtToggleCourseItem(it.row, { target: it.row }); out.endedRow = it.cb.checked; }
        { const it = row({ disabled: true }); xxtToggleCourseItem(it.row, { target: it.row }); out.disabledBox = it.cb.checked; }
        { const it = row({}); toggleCourseItem(it.row, { target: it.cb }); out.zhsBoxClick = it.cb.checked;
          toggleCourseItem(it.row, { target: it.row }); out.zhsRowClick = it.cb.checked; }
        out.summaryUpdates = summaryUpdates;
        console.log(JSON.stringify(out));
    """
    process = subprocess.run([NODE, "-e", script], text=True, encoding="utf-8", capture_output=True, timeout=30)
    assert process.returncode == 0, process.stderr
    result = json.loads(process.stdout)
    assert result["rowClick"] is True
    assert result["boxClick"] is False, "点方框时不应再翻转一次（浏览器已原生切换）"
    assert result["endedRow"] is False
    assert result["disabledBox"] is False
    assert result["zhsBoxClick"] is False
    assert result["zhsRowClick"] is True
    assert result["summaryUpdates"] >= 2


def _update_progress_script():
    return SOURCE[
        SOURCE.index("        let yatoriUpdateConfirming = false;"):
        SOURCE.index("        async function confirmAndPerform(")
    ]


def test_update_dialog_shows_progress_and_closes_after_install():
    script = _update_progress_script() + """
        const elements = {
            'yatori-update-modal': {
                classList: {
                    values: new Set(['active']),
                    add(v) { this.values.add(v); },
                    remove(v) { this.values.delete(v); },
                    contains(v) { return this.values.has(v); },
                },
            },
            'yatori-update-confirm-btn': { disabled: false, innerHTML: '', onclick: null, setAttribute() {} },
            'yatori-update-cancel-btn': { disabled: false },
            'yatori-update-progress': { hidden: true, dataset: {} },
            'yatori-update-progress-label': { textContent: '' },
            'yatori-update-progress-percent': { textContent: '' },
            'yatori-update-progress-bar': { style: {} },
            'yatori-update-progress-detail': { textContent: '' },
        };
        const window = {};
        const toasts = [];
        const timers = [];
        const document = { getElementById: id => elements[id] || null };
        function escapeHtml(value) { return String(value === undefined || value === null ? '' : value); }
        function showToast(message, type) { toasts.push([message, type]); }
        setTimeout = fn => { timers.push(fn); };
        const out = {};

        yatoriUpdateInstalling = true;
        yatoriUpdateLastPhase = 'downloading';
        handleCoreUpdateRuntime({
            core: 'yatori', phase: 'downloading', percent: 42.5,
            downloaded: 50 * 1024 * 1024, total: 120 * 1024 * 1024,
            speed: 2 * 1024 * 1024, eta: 35, mode: 'ghfast.top 加速',
        });
        out.bar = elements['yatori-update-progress-bar'].style.width;
        out.label = elements['yatori-update-progress-label'].textContent;
        out.percent = elements['yatori-update-progress-percent'].textContent;
        out.detail = elements['yatori-update-progress-detail'].textContent;
        out.phaseAttr = elements['yatori-update-progress'].dataset.phase;
        out.hidden = elements['yatori-update-progress'].hidden;

        handleCoreUpdateRuntime({ core: 'yatori', phase: 'completed', percent: 100 });
        out.completedLabel = elements['yatori-update-progress-label'].textContent;
        out.completedPercent = elements['yatori-update-progress-percent'].textContent;
        out.completedToast = toasts[toasts.length - 1];
        out.installingAfterCompleted = yatoriUpdateInstalling;
        out.pendingTimers = timers.length;
        timers.forEach(fn => fn());
        out.modalActiveAfterTimer = elements['yatori-update-modal'].classList.contains('active');
        out.hiddenAfterTimer = elements['yatori-update-progress'].hidden;
        out.lastPhaseAfterTimer = yatoriUpdateLastPhase;

        yatoriUpdateInstalling = true;
        yatoriUpdateLastPhase = 'downloading';
        handleCoreUpdateRuntime({ core: 'yatori', phase: 'failed', percent: 63, message: '网络中断' });
        out.failedPhaseAttr = elements['yatori-update-progress'].dataset.phase;
        out.failedLabel = elements['yatori-update-progress-label'].textContent;
        out.failedDetail = elements['yatori-update-progress-detail'].textContent;
        out.failedButton = elements['yatori-update-confirm-btn'].innerHTML;
        out.failedButtonDisabled = elements['yatori-update-confirm-btn'].disabled;
        out.failedButtonHasHandler = typeof elements['yatori-update-confirm-btn'].onclick === 'function';
        out.failedToast = toasts[toasts.length - 1];
        out.failedHidden = elements['yatori-update-progress'].hidden;

        const beforeIgnore = elements['yatori-update-progress-label'].textContent;
        handleCoreUpdateRuntime({ core: 'autovisor', phase: 'downloading', percent: 5 });
        out.ignoredOtherCore = elements['yatori-update-progress-label'].textContent === beforeIgnore;

        console.log(JSON.stringify(out));
    """
    process = subprocess.run([NODE, "-e", script], text=True, encoding="utf-8", capture_output=True, timeout=30)
    assert process.returncode == 0, process.stderr
    result = json.loads(process.stdout)
    assert result["bar"] == "42.5%"
    assert result["label"] == "正在下载"
    assert result["percent"] == "42.5%"
    assert result["phaseAttr"] == "downloading"
    assert result["hidden"] is False
    assert "50.0 MB / 120.0 MB" in result["detail"]
    assert "2.0 MB/s" in result["detail"]
    assert "剩余 35 秒" in result["detail"]
    assert result["detail"].startswith("ghfast.top 加速"), "进度详情应显示实际下载源"

    assert result["completedLabel"] == "更新完成"
    assert result["completedPercent"] == "100%"
    assert result["completedToast"] == ["Yatori 更新完成", "success"]
    assert result["installingAfterCompleted"] is False
    assert result["pendingTimers"] >= 1
    assert result["modalActiveAfterTimer"] is False, "安装完成后应自动关闭更新弹窗"
    assert result["hiddenAfterTimer"] is True
    assert result["lastPhaseAfterTimer"] == "idle"

    assert result["failedPhaseAttr"] == "failed"
    assert result["failedLabel"] == "更新失败"
    assert "网络中断" in result["failedDetail"]
    assert "重新检查更新" in result["failedButton"]
    assert result["failedButtonDisabled"] is False, "失败后按钮要能再次点击"
    assert result["failedButtonHasHandler"] is True
    assert result["failedToast"] == ["Yatori 更新失败，请查看系统日志", "error"]
    assert result["failedHidden"] is False, "失败面板不能被 busy(false) 隐藏"

    assert result["ignoredOtherCore"] is True
