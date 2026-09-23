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
