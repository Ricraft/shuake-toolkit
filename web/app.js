        const state = { settings: null, runtime: null, currentLogTab: 'system', preferences: {} };
        let currentConfigTab = 'yatori';
        let runtimeTimer = null;
        let exitConfirmed = false;
        let backendConnected = false;
        let backendWaitStartedAt = Date.now();
        let backendHintShown = false;
        let backendInitErrorShown = false;
        let initInFlight = false;
        let initialized = false;
        let initRetryTimer = null;
        let runtimeRefreshInFlight = false;
        let runtimeRequestSequence = 0;
        let runtimeAppliedSequence = 0;
        let currentBgType = 'none';
        const PREFERENCES_STORAGE_KEY = 'launcher_preferences_v1';

        function bridge() { return window.pywebview && window.pywebview.api ? window.pywebview.api : null; }
        async function apiCall(method, ...args) { const api = bridge(); if (!api || typeof api[method] !== 'function') { const e = new Error('Python 后端尚未就绪'); e.silent = true; throw e; } return api[method](...args); }
        function escapeHtml(v) { return String(v ?? '').replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;').replaceAll("'", '&#39;'); }
        function splitLines(v) { return String(v ?? '').split(/\r?\n/).map(s => s.trim()).filter(Boolean); }
        function splitComma(v) { return String(v ?? '').split(',').map(s => s.trim()).filter(Boolean); }

        const YATORI_PLATFORM_OPTIONS = [{ value: 'XUEXITONG', label: '学习通' },{ value: 'YINGHUA', label: '英华学堂' },{ value: 'CANGHUI', label: '仓辉实训' },{ value: 'ENAEA', label: '学习公社' },{ value: 'CQIE', label: '重庆工程学院' },{ value: 'KETANGX', label: '码上研训' },{ value: 'ICVE', label: '智慧职教' },{ value: 'QSXT', label: '青书学堂' },{ value: 'WELEARN', label: 'WeLearn' },{ value: 'HQKJ', label: '海旗科技' }];
        const YATORI_VIDEO_MODE_LABELS = { '0': '不刷视频', '1': '普通模式', '2': '暴力模式', '3': '去红模式' };
        const YATORI_VIDEO_MODE_LABELS_XUEXITONG = { '0': '不刷视频', '1': '普通模式（顺序学习）', '2': '多课程模式', '3': '多任务点同时进行' };
        const YATORI_VIDEO_MODE_LABELS_YINGHUA = { '0': '不刷视频', '1': '普通模式', '2': '暴力模式', '3': '去红模式' };
        const YATORI_VIDEO_MODE_LABELS_WELEARN = { '0': '不刷视频', '1': '刷学时模式', '2': '秒刷完成度' };
        const YATORI_VIDEO_MODE_LABELS_CQIE = { '0': '不刷视频', '1': '常规模式', '2': '暴力模式（秒刷）' };
        const YATORI_VIDEO_MODE_LABELS_ENAEA = { '0': '不刷视频', '1': '普通模式', '2': '暴力模式' };
        const YATORI_VIDEO_MODE_LABELS_HQKJ = { '0': '不刷视频', '1': '普通模式', '2': '快速模式' };
        const YATORI_VIDEO_MODE_LABELS_ICVE = { '0': '不刷视频', '1': '秒刷模式' };
        const YATORI_VIDEO_MODE_LABELS_QSXT = { '0': '不刷视频', '1': '刷学时模式' };
        const YATORI_VIDEO_MODE_LABELS_KETANGX = { '0': '不刷视频', '1': '常规模式（默认秒刷）' };
        const YATORI_EXAM_MODE_LABELS = { '0': '关闭自动考试', '1': 'AI 智能答题', '2': '第三方题库 API', '3': '学习通免费 AI (限平台)' };
        const YATORI_SUBMIT_MODE_LABELS = { '0': '否', '1': '是' };
        const AUTOVISOR_SPEED_OPTIONS = ['1.0','1.25','1.5','1.75','2.0'];
        const YATORI_PLATFORM_MODE_RULES = { XUEXITONG:{videoModes:['0','1','2','3'],examModes:['0','1','2','3'],submitModes:['0','1'],requireUrl:false,urlHint:'按平台默认入口可留空',examHint:'不考:关闭考试 | AI考试:使用AI答题 | 外部题库:对接外部题库 | 免费AI:学习通免费AI(无需配置)'},YINGHUA:{videoModes:['0','1','2','3'],examModes:['0','1','2'],submitModes:['0','1'],requireUrl:true,urlHint:'建议填写登录后对应的站点地址',examHint:'外部题库考试依赖上方题库 API。'},CANGHUI:{videoModes:['0','1'],examModes:['0'],submitModes:['0'],requireUrl:false,urlHint:'按平台默认入口可留空',examHint:'暂不支持自动考试。'},ENAEA:{videoModes:['0','1','2'],examModes:['0','1','2'],submitModes:['0','1'],requireUrl:false,urlHint:'按平台默认入口可留空',examHint:'AI 考试依赖上方 AI 模型配置。'},CQIE:{videoModes:['0','1','2'],examModes:['0','1','2'],submitModes:['0','1'],requireUrl:false,urlHint:'按平台默认入口可留空',examHint:'外部题库考试依赖上方题库 API；AI 考试依赖 AI 模型配置。'},KETANGX:{videoModes:['0','1'],examModes:['0'],submitModes:['0'],requireUrl:false,urlHint:'按平台默认入口可留空',examHint:'暂不支持自动考试。'},ICVE:{videoModes:['0','1'],examModes:['0','1','2'],submitModes:['0','1'],requireUrl:false,urlHint:'按平台默认入口可留空',examHint:'外部题库考试依赖上方题库 API。'},QSXT:{videoModes:['0','1'],examModes:['0','1','2'],submitModes:['0','1'],requireUrl:false,urlHint:'按平台默认入口可留空',examHint:'AI 考试依赖上方 AI 模型配置。'},WELEARN:{videoModes:['0','1','2'],examModes:['0','1','2'],submitModes:['0','1'],requireUrl:false,urlHint:'按平台默认入口可留空',examHint:'外部题库考试依赖上方题库 API。'},HQKJ:{videoModes:['0','1','2'],examModes:['0','1','2'],submitModes:['0','1'],requireUrl:true,urlHint:'建议填写登录后对应的海旗分站地址',examHint:'AI 考试依赖上方 AI 模型配置。'}};

        function getYatoriPlatformLabel(pc) { const m = YATORI_PLATFORM_OPTIONS.find(i => i.value === pc); return m ? m.label : (pc || '学习通'); }
        function getYatoriPlatformModeRule(pc) { return YATORI_PLATFORM_MODE_RULES[pc] || YATORI_PLATFORM_MODE_RULES.XUEXITONG; }
        function getYatoriFilterMode(u) { const c = u.coursesCustom || {}; if (Array.isArray(c.includeCourses) && c.includeCourses.length) return 'include'; if (Array.isArray(c.excludeCourses) && c.excludeCourses.length) return 'exclude'; return ''; }
        function renderMappedOptions(keys, lm, sel) { return keys.map(k => `<option value="${k}" ${k===sel?'selected':''}>${lm[k]||k}</option>`).join(''); }
        function renderYatoriPlatformOptions(sv) { const n = sv||'XUEXITONG'; return YATORI_PLATFORM_OPTIONS.map(i => `<option value="${i.value}" ${i.value===n?'selected':''}>${i.label}</option>`).join(''); }
        function getYatoriVideoModeLabels(pc) { if(pc==='XUEXITONG')return YATORI_VIDEO_MODE_LABELS_XUEXITONG; if(pc==='YINGHUA')return YATORI_VIDEO_MODE_LABELS_YINGHUA; if(pc==='WELEARN')return YATORI_VIDEO_MODE_LABELS_WELEARN; if(pc==='CQIE')return YATORI_VIDEO_MODE_LABELS_CQIE; if(pc==='ENAEA')return YATORI_VIDEO_MODE_LABELS_ENAEA; if(pc==='HQKJ')return YATORI_VIDEO_MODE_LABELS_HQKJ; if(pc==='ICVE')return YATORI_VIDEO_MODE_LABELS_ICVE; if(pc==='QSXT')return YATORI_VIDEO_MODE_LABELS_QSXT; if(pc==='KETANGX')return YATORI_VIDEO_MODE_LABELS_KETANGX; return YATORI_VIDEO_MODE_LABELS; }
        function renderYatoriVideoOptions(pc, sel) { const r = getYatoriPlatformModeRule(pc); return renderMappedOptions(r.videoModes, getYatoriVideoModeLabels(pc), sel||r.videoModes[0]); }
        function renderYatoriExamOptions(pc, sel) { const r = getYatoriPlatformModeRule(pc); return renderMappedOptions(r.examModes, YATORI_EXAM_MODE_LABELS, sel||r.examModes[0]); }
        function renderYatoriSubmitOptions(sel) { return ['0','1'].map(v => `<option value="${v}" ${v===(sel||'0')?'selected':''}>${YATORI_SUBMIT_MODE_LABELS[v]}</option>`).join(''); }

        function normalizeYatoriUser(u = {}, idx = 1) { const c = u.coursesCustom || {}; return { accountType: u.accountType||'XUEXITONG', url: u.url||'', remarkName: u.remarkName||`账号 ${idx}`, account: u.account||'', password: u.password||'', isProxy: Number(u.isProxy||0), informEmails: Array.isArray(u.informEmails)?u.informEmails:[], coursesCustom:{ shuffleSw:Number(c.shuffleSw||0), videoModel:Number(c.videoModel||1), autoExam:Number(c.autoExam||0), examAutoSubmit:Number(c.examAutoSubmit||0), includeCourses:Array.isArray(c.includeCourses)?c.includeCourses:[], excludeCourses:Array.isArray(c.excludeCourses)?c.excludeCourses:[] }}; }
        function normalizeAutovisorAccount(a = {}, idx = 1) { return { account_id:Number(a.account_id||idx), name:a.name||`账号 ${idx}`, username:a.username||'', password:a.password||'', driver:a.driver||'Chrome', exe_path:a.exe_path||'', enable_auto_captcha:a.enable_auto_captcha!==false, enable_hide_window:!!a.enable_hide_window, limit_max_time:String(a.limit_max_time??'30'), limit_speed:String(a.limit_speed??'1.0'), sound_off:a.sound_off!==false, course_urls:Array.isArray(a.course_urls)?a.course_urls:[] }; }
        function normalizeSettings(raw) { const y = raw?.yatori||{}, a = raw?.autovisor||{}; return { yatori:{ setting:{ basicSetting:{ completionTone:Number(y?.setting?.basicSetting?.completionTone??1), colorLog:Number(y?.setting?.basicSetting?.colorLog??1), logOutFileSw:Number(y?.setting?.basicSetting?.logOutFileSw??1), logLevel:y?.setting?.basicSetting?.logLevel||'INFO', logModel:Number(y?.setting?.basicSetting?.logModel??0), WebModel:Number(y?.setting?.basicSetting?.WebModel??0) }, emailInform:{ sw:Number(y?.setting?.emailInform?.sw??0), SMTPHost:y?.setting?.emailInform?.SMTPHost||'', SMTPPort:Number(y?.setting?.emailInform?.SMTPPort??0), userName:y?.setting?.emailInform?.userName||'', password:y?.setting?.emailInform?.password||'' }, aiSetting:{ aiType:y?.setting?.aiSetting?.aiType||'TONGYI', aiUrl:y?.setting?.aiSetting?.aiUrl||'', model:y?.setting?.aiSetting?.model||'', API_KEY:y?.setting?.aiSetting?.API_KEY||'' }, apiQueSetting:{ url:y?.setting?.apiQueSetting?.url||'http://localhost:8083' } }, users:(Array.isArray(y.users)?y.users:[]).map((u,i)=>normalizeYatoriUser(u,i+1)) }, autovisor:{ multi_mode:!!a.multi_mode, browser_driver:a.browser_driver||'Chrome', browser_path:a.browser_path||'', accounts:(Array.isArray(a.accounts)?a.accounts:[]).map((ac,i)=>normalizeAutovisorAccount(ac,i+1)) } }; }

        function showToast(message, type = 'info') { const wrap = document.getElementById('toast-wrap'); const t = document.createElement('div'); t.className = `toast ${type}`; t.textContent = message; wrap.appendChild(t); setTimeout(() => t.remove(), 2800); }

        function setBackendStatus(message) {
            const status = document.getElementById('sidebar-status');
            if (status) status.textContent = message;
        }

        function applyRuntimeState(runtime, requestId = null) {
            if (!runtime) return false;
            const effectiveId = requestId ?? ++runtimeRequestSequence;
            if (effectiveId < runtimeAppliedSequence) return false;
            runtimeAppliedSequence = effectiveId;
            renderRuntime(runtime);
            return true;
        }

        const pageTitles = { dashboard: '中控台', settings: '核心设置', questionbank: '题库设置', preferences: '软件设置', about: '关于' };

        function switchView(viewId, el) { document.querySelectorAll('.nav-item').forEach(e => e.classList.remove('active')); if (el) el.classList.add('active'); else { const n = document.querySelector(`.nav-item[data-view="${viewId}"]`); if (n) n.classList.add('active'); } document.querySelectorAll('.view-page').forEach(e => e.classList.remove('active')); const t = document.getElementById(`view-${viewId}`); if (t) t.classList.add('active'); const ti = document.getElementById('page-title'); if (ti) ti.textContent = pageTitles[viewId]||viewId; if (viewId==='settings') requestAnimationFrame(() => renderSettingsTabContent(currentConfigTab)); if (viewId==='preferences') loadPreferences(); if (viewId==='about') requestAnimationFrame(() => initAboutPageEffects()); }
        function switchConfigTab(tab) { captureCurrentConfigTab(); currentConfigTab = tab; document.getElementById('tab-btn-yatori').classList.toggle('active', tab==='yatori'); document.getElementById('tab-btn-autovisor').classList.toggle('active', tab==='autovisor'); document.getElementById('config-yatori').style.display = tab==='yatori'?'flex':'none'; document.getElementById('config-autovisor').style.display = tab==='autovisor'?'flex':'none'; requestAnimationFrame(() => renderSettingsTabContent(tab)); }
        function openSettingsTab(tab) { switchView('settings'); switchConfigTab(tab); }
        function captureCurrentConfigTab() { const y = document.getElementById('config-yatori'); currentConfigTab = (y && y.style.display !== 'none') ? 'yatori' : 'autovisor'; }
        function switchConsoleTab(el) { document.querySelectorAll('.pill-tab[data-log-tab]').forEach(e => e.classList.remove('active')); el.classList.add('active'); state.currentLogTab = el.dataset.logTab; renderConsole(); }
        function unwrapState(r) { return r?.state?.runtime || r?.state || r?.runtime || null; }
        function renderConsole() { const b = document.getElementById('console-output'); if (!b) return; if (!state.runtime||!state.runtime.logs) { b.textContent = '等待日志输出...'; return; } const tab = state.currentLogTab||'system'; const raw = state.runtime.logs[tab]; const logs = Array.isArray(raw)?raw:String(raw||'').split(/\r?\n/).filter(Boolean); b.textContent = logs.length?logs.join('\n'):`暂无 ${tab} 日志`; b.scrollTop = b.scrollHeight; }
        function exportLogs() { const logs = state.runtime?.logs; if (!logs) { showToast('没有可导出的日志', 'warning'); return; } const tab = state.currentLogTab||'system'; const LABELS = { system: '系统', yatori: 'Yatori', autovisor: 'Autovisor' }; let parts = [`=== ${LABELS[tab]||tab} 日志 ===`, '']; const raw = logs[tab]; const lines = Array.isArray(raw)?raw:String(raw||'').split(/\r?\n/).filter(Boolean); parts = parts.concat(lines); const text = parts.join('\n'); apiCall('export_logs', tab, text).then(r => { if (r?.ok) showToast(r.message||'日志已导出', 'success'); else showToast(r?.message||'导出失败', 'error'); }); }

        function renderAutovisorActivity(activity) { const panel = document.getElementById('autovisor-activity'); if (!panel) return; if (!activity || activity.phase === 'idle') { panel.hidden = true; return; } panel.hidden = false; panel.dataset.phase = activity.phase||'idle'; const label = document.getElementById('autovisor-activity-label'); const detail = document.getElementById('autovisor-activity-detail'); const percent = document.getElementById('autovisor-activity-percent'); const track = document.getElementById('autovisor-progress-track'); const bar = document.getElementById('autovisor-progress-bar'); if (label) label.textContent = activity.label||'运行中'; const parts = []; if (activity.course_index && activity.course_total) parts.push(`第 ${activity.course_index}/${activity.course_total} 门`); if (activity.course) parts.push(activity.course); if (activity.phase === 'failed' && activity.last_error) parts.push(activity.last_error); if (detail) { detail.textContent = parts.join(' · ')||'等待更多运行信息'; detail.title = detail.textContent; } const raw = Number(activity.progress_percent); const hasProgress = activity.progress_percent !== null && activity.progress_percent !== '' && Number.isFinite(raw); const value = hasProgress?Math.max(0, Math.min(100, raw)):0; if (percent) percent.textContent = hasProgress?`${value}%`:''; if (track) track.hidden = !hasProgress; if (bar) bar.style.width = `${value}%`; }

        function renderRuntime(runtime) { if (!runtime) return; const n = { ...runtime, yatori_running: runtime.yatori_running??!!runtime.running?.yatori, autovisor_running: runtime.autovisor_running??!!runtime.running?.autovisor, yatori_version: runtime.yatori_version||runtime.versions?.yatori||'Yatori Core', autovisor_version: runtime.autovisor_version||runtime.versions?.autovisor||'Autovisor Core', about_version: runtime.about_version||'', core_paths: runtime.core_paths||(runtime.paths?`Yatori: ${runtime.paths.yatori||''}\nAutovisor: ${runtime.paths.autovisor||''}`:''), shutdown_pending: !!runtime.shutdown_pending }; state.runtime = n; backendConnected = true; renderConsole(); document.getElementById('sidebar-status').textContent = 'Python 后端已连接'; setCoreStatus('yatori', n.yatori_running, n.yatori_version); setCoreStatus('autovisor', n.autovisor_running, n.autovisor_version); renderAutovisorActivity(n.autovisor_activity); if (n.about_version) { document.getElementById('about-version').textContent = n.about_version; const m = n.about_version.match(/统一启动器\s*([^|]+)/); if (m) document.getElementById('about-title-version').textContent = m[1].trim(); } if (n.core_paths) { document.getElementById('about-paths').textContent = n.core_paths; initTypewriterPaths(); } const qbR = !!runtime.qb_running, qbP = runtime.qb_port||8083, qbS = runtime.qb_stats||{total:0,ai_cached:0}; setQbStatus(qbR, qbP, qbS); const sm = document.getElementById('shutdown-modal'); if (sm) sm.classList.toggle('active', n.shutdown_pending); if (n.shutdown_pending && !window._shutdownTimer) { let sec = 60; const secEl = document.getElementById('shutdown-countdown-sec'); if (secEl) secEl.textContent = sec; window._shutdownTimer = setInterval(() => { const el = document.getElementById('shutdown-countdown-sec'); if (el) el.textContent = --sec; if (sec <= 0) { clearInterval(window._shutdownTimer); window._shutdownTimer = null; document.getElementById('shutdown-modal')?.classList.remove('active'); } }, 1000); } else if (!n.shutdown_pending && window._shutdownTimer) { clearInterval(window._shutdownTimer); window._shutdownTimer = null; } }

        function setCoreStatus(core, running, version) { document.getElementById(`${core}-dot`).classList.toggle('running', running); document.getElementById(`${core}-status`).textContent = running?'运行中':'未启动'; document.getElementById(`${core}-ready`).textContent = running?'运行中':'待命'; document.getElementById(`${core}-ready`).className = 'status-badge '+(running?'ready':'idle'); document.getElementById(`${core}-version`).textContent = version||`${core} Core`; document.getElementById(`${core}-action-btn`).innerHTML = running?'<i class="fas fa-stop"></i> 停止核心':'<i class="fas fa-power-off"></i> 启动核心'; document.getElementById(`${core}-action-btn`).onclick = () => handleCoreAction(core); }

        function setQbStatus(running, port, stats) { const e=(id)=>document.getElementById(id); if(e('qb-dot'))e('qb-dot').classList.toggle('running',running); if(e('qb-status'))e('qb-status').textContent=running?'运行中':'未启动'; if(e('qb-ready')){e('qb-ready').textContent=running?'运行中':'待命';e('qb-ready').className='status-badge '+(running?'ready':'idle');} const localN = stats.local||0, aiN = stats.ai_cached||0, totalN = stats.total||0; const compactStats = `共 ${totalN} 条 (非AI: ${localN} 条 | AI缓存: ${aiN} 条)`; if(e('qb-stats'))e('qb-stats').textContent=compactStats; if(e('qb-port-display'))e('qb-port-display').textContent=`端口: ${port}`; if(e('qb-action-btn'))e('qb-action-btn').innerHTML=running?'<i class="fas fa-stop"></i> 停止题库':'<i class="fas fa-power-off"></i> 启动题库'; ['settings-qb-status','qb-page-status'].forEach(id=>{if(e(id)){e(id).textContent=running?'运行中':'未启动';e(id).className='status-badge '+(running?'ready':'idle');}}); ['settings-qb-btn','qb-page-btn'].forEach(id=>{if(e(id))e(id).innerHTML=running?'<i class="fas fa-stop"></i> 停止题库':'<i class="fas fa-power-off"></i> 启动题库';}); if(e('settings-qb-stats'))e('settings-qb-stats').textContent=compactStats; if(e('qb-page-stats'))e('qb-page-stats').textContent=compactStats; if(e('qb-stat-total'))e('qb-stat-total').textContent=totalN; if(e('qb-stat-local'))e('qb-stat-local').textContent=localN; if(e('qb-stat-ai'))e('qb-stat-ai').textContent=aiN; }

        function renderSettingsTabContent(tab = currentConfigTab) { if (!state.settings) return; if (tab==='yatori') renderYatoriAccounts(state.settings.yatori.users||[]); else renderAutovisorAccounts(state.settings.autovisor.accounts||[]); }

        function renderSettings(settings) { state.settings = normalizeSettings(settings); const y=state.settings.yatori, a=state.settings.autovisor; document.getElementById('y-log-level').value=y.setting.basicSetting.logLevel; document.getElementById('y-log-model').value=String(y.setting.basicSetting.logModel); document.getElementById('y-web-model').value=String(y.setting.basicSetting.WebModel); document.getElementById('y-completion-tone').checked=!!y.setting.basicSetting.completionTone; document.getElementById('y-color-log').checked=!!y.setting.basicSetting.colorLog; document.getElementById('y-log-out-file').checked=!!y.setting.basicSetting.logOutFileSw; document.getElementById('y-email-sw').checked=!!y.setting.emailInform.sw; document.getElementById('y-smtp-host').value=y.setting.emailInform.SMTPHost; document.getElementById('y-smtp-port').value=y.setting.emailInform.SMTPPort||''; document.getElementById('y-email-user').value=y.setting.emailInform.userName; document.getElementById('y-email-password').value=y.setting.emailInform.password; document.getElementById('y-ai-type').value=y.setting.aiSetting.aiType; document.getElementById('y-ai-url').value=y.setting.aiSetting.aiUrl; document.getElementById('y-ai-model').value=y.setting.aiSetting.model; document.getElementById('y-ai-api-key').value=y.setting.aiSetting.API_KEY; document.getElementById('y-api-url').value=y.setting.apiQueSetting.url; syncYatoriAiField(); if(document.getElementById('a-browser-driver'))document.getElementById('a-browser-driver').value=a.browser_driver; if(document.getElementById('a-browser-path'))document.getElementById('a-browser-path').value=a.browser_path; if(document.getElementById('a-multi-mode'))document.getElementById('a-multi-mode').checked=!!a.multi_mode; syncAutovisorMulti(!!a.multi_mode); renderSettingsTabContent(currentConfigTab); if (settings.questionbank) loadQbSettings(settings.questionbank); }

        function renderYatoriAccounts(users) { const c = document.getElementById('yatori-accounts'); if (!users.length) { c.innerHTML = '<div class="empty-tip">还没有账号，点击右上角添加一个。</div>'; return; } c.innerHTML = users.map((user, idx) => { const pc = user.accountType||'XUEXITONG', rule = getYatoriPlatformModeRule(pc), fm = getYatoriFilterMode(user); const vm = String(user.coursesCustom?.videoModel??'1'), ae = String(user.coursesCustom?.autoExam??'0'), sv = String(user.coursesCustom?.examAutoSubmit??'0'); const showExam = rule.examModes.some(v => v!=='0'), isXXT = pc === 'XUEXITONG'; return `<div class="glass-card flex flex-col gap-4 yatori-account-card" data-index="${idx}" data-filter-mode="${fm}"><div class="flex justify-between items-center"><div class="flex items-center gap-2"><h4 class="font-bold text-sm">${escapeHtml(user.remarkName||`账号 ${idx+1}`)}</h4><span class="text-xs" style="color:var(--text-muted);" data-role="platform-badge">(${escapeHtml(getYatoriPlatformLabel(pc))})</span></div><button class="btn btn-outline btn-sm" onclick="removeYatoriAccount(${idx})"><i class="fas fa-trash-alt"></i></button></div><div class="grid grid-cols-1 md:grid-cols-2 gap-4"><div class="input-group"><span class="input-label">账号类型</span><div class="select-wrapper"><select data-field="accountType" class="input-field" onchange="handleYatoriPlatformChange(this)">${renderYatoriPlatformOptions(pc)}</select><i class="fas fa-chevron-down"></i></div><p class="text-xs mt-1" style="color:var(--text-muted);" data-role="url-hint">${escapeHtml(rule.urlHint)}</p></div><div class="input-group"><span class="input-label">备注名称</span><input data-field="remarkName" class="input-field" value="${escapeHtml(user.remarkName)}"></div><div class="input-group"><span class="input-label">登录账号</span><input data-field="account" class="input-field" value="${escapeHtml(user.account)}"></div><div class="input-group"><span class="input-label">密码/Cookie/Token</span><input data-field="password" type="password" class="input-field" value="${escapeHtml(user.password)}"></div><div class="input-group"><span class="input-label">站点地址</span><input data-field="url" class="input-field" value="${escapeHtml(user.url)}" placeholder="${rule.requireUrl?'建议填写该平台或学校分站地址':'按平台默认入口可留空'}"></div><div class="input-group"><span class="input-label">通知邮箱（逗号分隔）</span><input data-field="informEmails" class="input-field" value="${escapeHtml((user.informEmails||[]).join(', '))}"></div></div><div class="grid grid-cols-1 md:grid-cols-2 gap-4"><div class="input-group"><span class="input-label">视频模式</span><div class="select-wrapper"><select data-field="videoModel" class="input-field">${renderYatoriVideoOptions(pc, vm)}</select><i class="fas fa-chevron-down"></i></div></div><div class="input-group"><span class="input-label">自动考试模式</span><div class="select-wrapper"><select data-field="autoExam" class="input-field">${renderYatoriExamOptions(pc, ae)}</select><i class="fas fa-chevron-down"></i></div><p class="text-xs mt-1" style="color:var(--text-muted);" data-role="exam-hint">${escapeHtml(rule.examHint)}</p></div></div><div class="flex flex-wrap gap-4 items-end"><div class="input-group w-full md:w-56" data-role="submit-wrap" ${showExam?'':'style="display:none;"'}><span class="input-label">交卷模式</span><div class="select-wrapper"><select data-field="examAutoSubmit" class="input-field">${renderYatoriSubmitOptions(sv)}</select><i class="fas fa-chevron-down"></i></div></div><div class="flex flex-wrap gap-4 items-center"><label class="flex items-center gap-2 text-xs cursor-pointer" style="color:var(--text-muted);"><input data-field="isProxy" type="checkbox" ${Number(user.isProxy)?'checked':''}> 启用代理</label><label class="flex items-center gap-2 text-xs cursor-pointer" style="color:var(--text-muted);"><input data-field="shuffleSw" type="checkbox" ${Number(user.coursesCustom.shuffleSw)?'checked':''}> 随机打乱课程</label></div></div><div class="flex flex-wrap items-center gap-3"><span class="input-label">课程筛选</span><button class="btn btn-sm ${fm==='include'?'btn-primary':'btn-outline'}" data-filter-button="include" onclick="toggleYatoriCourseFilter(this,'include')">只刷特定课程</button><button class="btn btn-sm ${fm==='exclude'?'btn-primary':'btn-outline'}" data-filter-button="exclude" onclick="toggleYatoriCourseFilter(this,'exclude')">不刷某个课程</button><button class="btn btn-ghost btn-sm" data-filter-clear onclick="clearYatoriCourseFilter(this)" ${fm?'':'style="display:none;"'}>清空</button>${isXXT?`<button id="xxt-course-btn-${idx}" class="btn btn-primary btn-sm" onclick="getXuexitongCourses(${idx})" style="margin-left:auto"><i class="fas fa-download"></i> 获取课程</button>`:''}</div><div class="input-group" data-filter-panel="include" ${fm==='include'?'':'style="display:none;"'}><span class="input-label">只刷这些课程（每行一个）</span><textarea data-field="includeCourses" class="input-field">${escapeHtml((user.coursesCustom.includeCourses||[]).join('\n'))}</textarea></div><div class="input-group" data-filter-panel="exclude" ${fm==='exclude'?'':'style="display:none;"'}><span class="input-label">不刷这些课程（每行一个）</span><textarea data-field="excludeCourses" class="input-field">${escapeHtml((user.coursesCustom.excludeCourses||[]).join('\n'))}</textarea></div></div>`; }).join(''); document.querySelectorAll('.yatori-account-card').forEach(card => { syncYatoriPlatformCard(card); updateYatoriCourseFilterUI(card); }); }

        let courseFetchRunning = false;
        let practiceModeRunning = false;
        function renderAutovisorAccounts(accounts) { const c = document.getElementById('autovisor-accounts'); if (!accounts.length) { c.innerHTML = '<div class="empty-tip">还没有账号，点击右上角添加一个。</div>'; return; } c.innerHTML = accounts.map((a, i) => `<div class="glass-card flex flex-col gap-4 autovisor-account-card" data-index="${i}"><div class="flex justify-between items-center"><h4 class="font-bold text-sm">${escapeHtml(a.name||`账号 ${i+1}`)}</h4><button class="btn btn-outline btn-sm" onclick="removeAutovisorAccount(${i})"><i class="fas fa-trash-alt"></i></button></div><div class="grid grid-cols-1 md:grid-cols-3 gap-4"><div class="input-group"><span class="input-label">卡片名称</span><input data-field="name" class="input-field" value="${escapeHtml(a.name)}"></div><div class="input-group"><span class="input-label">账号/学号</span><input data-field="username" class="input-field" value="${escapeHtml(a.username)}"></div><div class="input-group"><span class="input-label">密码</span><input data-field="password" type="password" class="input-field" value="${escapeHtml(a.password)}"></div><div class="input-group"><span class="input-label">播放倍速</span><div class="select-wrapper"><select data-field="limit_speed" class="input-field">${AUTOVISOR_SPEED_OPTIONS.map(o=>`<option value="${o}" ${String(a.limit_speed)===o?'selected':''}>x${o}</option>`).join('')}</select><i class="fas fa-chevron-down"></i></div></div><div class="input-group"><span class="input-label">最大时长(分钟)</span><input data-field="limit_max_time" type="number" min="1" class="input-field" value="${escapeHtml(a.limit_max_time)}"></div></div><div class="flex flex-col gap-2"><label class="flex items-center gap-2 text-xs cursor-pointer" style="color:var(--text-muted);"><input data-field="enable_auto_captcha" type="checkbox" ${a.enable_auto_captcha?'checked':''}> 自动验证码</label><label class="flex items-center gap-2 text-xs cursor-pointer" style="color:var(--text-muted);"><input data-field="enable_hide_window" type="checkbox" ${a.enable_hide_window?'checked':''}> 隐藏窗口</label><label class="flex items-center gap-2 text-xs cursor-pointer" style="color:var(--text-muted);"><input data-field="sound_off" type="checkbox" ${a.sound_off?'checked':''}> 静音播放</label></div><div class="input-group"><div class="flex justify-between items-center mb-1"><span class="input-label">课程链接（每行一个）</span><div class="flex items-center gap-2"><button id="practice-btn-${i}" class="btn btn-outline btn-sm" onclick="startPracticeMode(${i})"><i class="fas fa-pencil-alt"></i> 刷题模式</button><button class="btn btn-ghost btn-sm" onclick="clearCourseUrls(${i})" title="清空所有课程链接"><i class="fas fa-eraser"></i></button><button id="course-btn-${i}" class="btn btn-primary btn-sm" onclick="getAutovisorCourses(${i})"><i class="fas fa-download"></i> 获取课程</button></div></div><textarea data-field="course_urls" class="input-field" id="course-urls-${i}">${escapeHtml((a.course_urls||[]).join('\n'))}</textarea></div></div>`).join(''); }
        async function getAutovisorCourses(accountIndex) {
            if (courseFetchRunning) { showToast('正在获取课程中，请稍候...', 'warning'); return; }
            let btn = document.getElementById(`course-btn-${accountIndex}`);
            let textarea = document.getElementById(`course-urls-${accountIndex}`);
            if (!btn) return;
            courseFetchRunning = true;
            btn.disabled = true;
            btn.innerHTML = '<i class="fas fa-circle-notch fa-spin"></i> 保存配置...';
            try {
                const saveResult = await saveSettings(false);
                if (!saveResult?.ok) {
                    showToast('保存配置失败，无法获取课程', 'error');
                    btn = document.getElementById(`course-btn-${accountIndex}`);
                    if (btn) { btn.innerHTML = '<i class="fas fa-download"></i> 获取课程'; btn.disabled = false; }
                    courseFetchRunning = false;
                    return;
                }
                btn = document.getElementById(`course-btn-${accountIndex}`);
                textarea = document.getElementById(`course-urls-${accountIndex}`);
                if (!btn) { courseFetchRunning = false; return; }
                btn.disabled = true;
                btn.innerHTML = '<i class="fas fa-circle-notch fa-spin"></i> 获取中...';
                const result = await apiCall('get_autovisor_courses', accountIndex);
                btn = document.getElementById(`course-btn-${accountIndex}`);
                textarea = document.getElementById(`course-urls-${accountIndex}`);
                if (!btn) { courseFetchRunning = false; return; }
                if (result?.ok) {
                    btn.innerHTML = '<i class="fas fa-check"></i> 获取完成';
                    btn.classList.remove('btn-primary');
                    btn.classList.add('btn-success');
                    setTimeout(() => {
                        const btn2 = document.getElementById(`course-btn-${accountIndex}`);
                        if (btn2) {
                            btn2.innerHTML = '<i class="fas fa-download"></i> 获取课程';
                            btn2.classList.remove('btn-success');
                            btn2.classList.add('btn-primary');
                        }
                    }, 3000);
                    if (result.courses && result.courses.length > 0) {
                        showCourseSelectionDialog(accountIndex, result.courses, textarea);
                    } else {
                        showToast('未获取到课程数据', 'warning');
                    }
                } else {
                    showToast(result?.message || '获取课程失败', 'error');
                    btn.innerHTML = '<i class="fas fa-download"></i> 获取课程';
                    btn.classList.remove('btn-success');
                    btn.classList.add('btn-primary');
                }
            } catch (e) {
                showToast(e?.message || '获取课程时发生错误', 'error');
                btn = document.getElementById(`course-btn-${accountIndex}`);
                if (btn) { btn.innerHTML = '<i class="fas fa-download"></i> 获取课程'; btn.disabled = false; }
            } finally {
                courseFetchRunning = false;
                btn = document.getElementById(`course-btn-${accountIndex}`);
                if (btn) btn.disabled = false;
            }
        }
        function clearCourseUrls(accountIndex) {
            const ta = document.getElementById(`course-urls-${accountIndex}`);
            if (!ta) return;
            if (!ta.value.trim()) { showToast('课程链接已为空', 'info'); return; }
            ta.value = '';
            if (state.settings?.autovisor?.accounts?.[accountIndex]) {
                state.settings.autovisor.accounts[accountIndex].course_urls = [];
            }
            showToast('课程链接已清空', 'success');
        }
        async function startPracticeMode(accountIndex) {
            if (practiceModeRunning) { showToast('刷题模式已启动，请勿重复操作', 'warning'); return; }
            let btn = document.getElementById(`practice-btn-${accountIndex}`);
            if (!btn) return;
            practiceModeRunning = true;
            btn.disabled = true;
            btn.innerHTML = '<i class="fas fa-circle-notch fa-spin"></i> 保存配置...';
            try {
                const saveResult = await saveSettings(false);
                if (!saveResult?.ok) {
                    showToast('保存配置失败，无法启动刷题模式', 'error');
                    btn = document.getElementById(`practice-btn-${accountIndex}`);
                    if (btn) { btn.innerHTML = '<i class="fas fa-pencil-alt"></i> 刷题模式'; btn.disabled = false; }
                    practiceModeRunning = false;
                    return;
                }
                btn = document.getElementById(`practice-btn-${accountIndex}`);
                if (!btn) { practiceModeRunning = false; return; }
                btn.innerHTML = '<i class="fas fa-circle-notch fa-spin"></i> 启动中...';
                const result = await apiCall('start_practice_mode');
                btn = document.getElementById(`practice-btn-${accountIndex}`);
                if (!btn) { practiceModeRunning = false; return; }
                if (result?.ok) {
                    btn.innerHTML = '<i class="fas fa-check"></i> 已启动';
                    btn.classList.add('btn-success');
                    setTimeout(() => {
                        const btn2 = document.getElementById(`practice-btn-${accountIndex}`);
                        if (btn2) {
                            btn2.innerHTML = '<i class="fas fa-pencil-alt"></i> 刷题模式';
                            btn2.classList.remove('btn-success');
                            btn2.classList.add('btn-outline');
                            practiceModeRunning = false;
                        }
                    }, 5000);
                    showToast(result.message || '刷题模式已启动', 'success');
                } else {
                    showToast(result?.message || '启动刷题模式失败', 'error');
                    btn.innerHTML = '<i class="fas fa-pencil-alt"></i> 刷题模式';
                    practiceModeRunning = false;
                }
            } catch (e) {
                showToast(e?.message || '启动刷题模式时发生错误', 'error');
                btn = document.getElementById(`practice-btn-${accountIndex}`);
                if (btn) { btn.innerHTML = '<i class="fas fa-pencil-alt"></i> 刷题模式'; btn.disabled = false; }
                practiceModeRunning = false;
            }
        }
        function extractCourseKey(url) {
            if (!url) return '';
            try {
                const urlObj = new URL(url);
                const recruitId = urlObj.searchParams.get('recruitAndCourseId');
                const secret = urlObj.searchParams.get('secret');
                const liveId = urlObj.searchParams.get('liveId');
                if (recruitId) return `recruit:${recruitId}`;
                if (secret) return `secret:${secret}`;
                if (liveId) return `live:${liveId}`;
                return url;
            } catch (e) {
                const recruitMatch = url.match(/recruitAndCourseId=([^&]+)/);
                const secretMatch = url.match(/secret=([^&]+)/);
                const liveMatch = url.match(/liveId=([^&]+)/);
                if (recruitMatch) return `recruit:${recruitMatch[1]}`;
                if (secretMatch) return `secret:${secretMatch[1]}`;
                if (liveMatch) return `live:${liveMatch[1]}`;
                return url;
            }
        }
        function showCourseSelectionDialog(accountIndex, courses, textarea) {
            const existing = document.getElementById('course-select-overlay');
            if (existing) existing.remove();
            
            const existingKeys = new Set(
                (textarea.value || '')
                    .split('\n')
                    .map(s => extractCourseKey(s.trim()))
                    .filter(Boolean)
            );
            
            const coursesWithStatus = courses.map(c => {
                const url = c.url || c.link || '';
                const key = extractCourseKey(url);
                return {
                    ...c,
                    url: url,
                    _key: key,
                    isDuplicate: key && existingKeys.has(key)
                };
            });
            
            const newCount = coursesWithStatus.filter(c => !c.isDuplicate).length;
            const dupCount = coursesWithStatus.filter(c => c.isDuplicate).length;
            
            const overlay = document.createElement('div');
            overlay.id = 'course-select-overlay';
            overlay.className = 'modal-overlay active';
            overlay.innerHTML = `
                <div class="course-modal">
                    <div class="modal-header">
                        <div>
                            <h3>选择课程</h3>
                            <div class="modal-subtitle">共 ${courses.length} 门课程，${newCount} 门新课程，${dupCount} 门已添加</div>
                        </div>
                        <button class="modal-close" onclick="closeCourseSelectDialog()">&times;</button>
                    </div>
                    <div class="modal-body">
                        <div class="course-select-actions">
                            <button class="btn btn-outline btn-sm" onclick="selectAllCourses()"><i class="fas fa-check-double"></i> 全选</button>
                            <button class="btn btn-outline btn-sm" onclick="deselectAllCourses()"><i class="fas fa-times"></i> 取消全选</button>
                            <button class="btn btn-outline btn-sm" onclick="selectNewCoursesOnly()"><i class="fas fa-plus"></i> 仅选新课程</button>
                        </div>
                        <div class="course-select-list">
                            ${coursesWithStatus.map((c, i) => `<div class="course-select-item${c.isDuplicate ? ' course-duplicate' : ''}" onclick="toggleCourseItem(this)">
                                <input type="checkbox" id="course-cb-${i}" ${c.isDuplicate ? '' : 'checked'} ${c.isDuplicate ? 'data-duplicate="true"' : ''}>
                                <div class="course-select-info">
                                    <div class="course-select-name">
                                        ${escapeHtml(c.name || c.title || `课程 ${i+1}`)}
                                        ${c.type ? `<span class="course-type-badge course-type-${typeof c.courseType === 'number' ? c.courseType : 'live'}">${escapeHtml(c.type)}${c.courseType === 'live' ? '（测试中，请自行完成）' : ''}</span>` : ''}
                                        ${c.isDuplicate ? '<span class="course-duplicate-badge">已添加</span>' : ''}
                                    </div>
                                    <div class="course-select-meta">
                                        <span>${escapeHtml(c.id || c.courseId || '')}</span>
                                        ${c.progress && c.progress !== '-' ? `<span class="course-progress">进度: ${escapeHtml(c.progress)}</span>` : ''}
                                    </div>
                                </div>
                            </div>`).join('')}
                        </div>
                    </div>
                    <div class="modal-footer">
                        <button class="btn btn-outline" onclick="closeCourseSelectDialog()">取消</button>
                        <button class="btn btn-primary" onclick="confirmCourseSelection(${accountIndex})"><i class="fas fa-check"></i> 确认添加</button>
                    </div>
                </div>
            `;
            document.body.appendChild(overlay);
            window._courseSelectTextarea = textarea;
            window._courseSelectCourses = coursesWithStatus;
        }
        function closeCourseSelectDialog() {
            const overlay = document.getElementById('course-select-overlay');
            if (overlay) overlay.remove();
        }
        function toggleCourseItem(el) {
            const cb = el.querySelector('input[type="checkbox"]');
            if (cb) cb.checked = !cb.checked;
        }
        function selectAllCourses() {
            document.querySelectorAll('#course-select-overlay input[type="checkbox"]').forEach(cb => cb.checked = true);
        }
        function deselectAllCourses() {
            document.querySelectorAll('#course-select-overlay input[type="checkbox"]').forEach(cb => cb.checked = false);
        }
        function selectNewCoursesOnly() {
            document.querySelectorAll('#course-select-overlay input[type="checkbox"]').forEach(cb => {
                cb.checked = !cb.hasAttribute('data-duplicate');
            });
        }
        async function confirmCourseSelection(accountIndex) {
            const textarea = window._courseSelectTextarea;
            const courses = window._courseSelectCourses;
            if (!textarea || !courses) { closeCourseSelectDialog(); return; }

            const selected = [];
            const checkboxes = document.querySelectorAll('#course-select-overlay input[type="checkbox"]');
            checkboxes.forEach((cb, i) => {
                if (cb.checked && courses[i] && courses[i].url) {
                    selected.push(courses[i].url);
                }
            });

            if (selected.length === 0) {
                showToast('未选择任何课程', 'warning');
                return;
            }

            closeCourseSelectDialog();

            const newValue = selected.join('\n');
            textarea.value = newValue;
            if (state.settings?.autovisor?.accounts?.[accountIndex]) {
                state.settings.autovisor.accounts[accountIndex].course_urls = selected;
            }
            showToast(`已选择 ${selected.length} 门课程`, 'success');
            try {
                await saveSettings(false);
            } catch (e) {}
        }

        let xxtCourseFetchRunning = false;
        async function getXuexitongCourses(accountIndex) {
            if (xxtCourseFetchRunning) { showToast('正在获取课程中，请稍候...', 'warning'); return; }
            let btn = document.getElementById(`xxt-course-btn-${accountIndex}`);
            if (!btn) return;
            xxtCourseFetchRunning = true;
            btn.disabled = true;
            btn.innerHTML = '<i class="fas fa-circle-notch fa-spin"></i> 保存配置...';
            try {
                const saveResult = await saveSettings(false);
                if (!saveResult?.ok) {
                    showToast('保存配置失败，无法获取课程', 'error');
                    btn = document.getElementById(`xxt-course-btn-${accountIndex}`);
                    if (btn) { btn.innerHTML = '<i class="fas fa-download"></i> 获取课程'; btn.disabled = false; }
                    xxtCourseFetchRunning = false;
                    return;
                }
                btn = document.getElementById(`xxt-course-btn-${accountIndex}`);
                if (!btn) { xxtCourseFetchRunning = false; return; }
                btn.innerHTML = '<i class="fas fa-circle-notch fa-spin"></i> 获取中...';
                const result = await apiCall('get_xuexitong_courses', accountIndex);
                btn = document.getElementById(`xxt-course-btn-${accountIndex}`);
                if (!btn) { xxtCourseFetchRunning = false; return; }
                if (result?.ok) {
                    btn.innerHTML = '<i class="fas fa-check"></i> 获取完成';
                    setTimeout(() => {
                        const btn2 = document.getElementById(`xxt-course-btn-${accountIndex}`);
                        if (btn2) { btn2.innerHTML = '<i class="fas fa-download"></i> 获取课程'; btn2.disabled = false; }
                    }, 3000);
                    if (result.courses && result.courses.length > 0) {
                        showXuexitongCourseDialog(accountIndex, result.courses);
                    } else {
                        showToast('未获取到课程数据', 'warning');
                        btn.disabled = false;
                    }
                } else {
                    showToast(result?.message || '获取课程失败', 'error');
                    btn.innerHTML = '<i class="fas fa-download"></i> 获取课程';
                    btn.disabled = false;
                }
            } catch (e) {
                showToast(e?.message || '获取课程时发生错误', 'error');
                btn = document.getElementById(`xxt-course-btn-${accountIndex}`);
                if (btn) { btn.innerHTML = '<i class="fas fa-download"></i> 获取课程'; btn.disabled = false; }
            } finally {
                xxtCourseFetchRunning = false;
            }
        }

        function showXuexitongCourseDialog(accountIndex, courses) {
            const existing = document.getElementById('xxt-course-select-overlay');
            if (existing) existing.remove();
            const card = document.querySelector(`.yatori-account-card[data-index="${accountIndex}"]`);
            const existingNames = new Set(
                (card ? (card.querySelector('[data-field="includeCourses"]')?.value || '') : '')
                    .split('\n').map(s => s.trim()).filter(Boolean)
            );
            const coursesWithStatus = courses.map(c => ({
                ...c,
                isDuplicate: existingNames.has(c.name) || existingNames.has(c.name?.trim())
            }));
            const newCount = coursesWithStatus.filter(c => !c.isDuplicate).length;
            const dupCount = coursesWithStatus.filter(c => c.isDuplicate).length;
            const overlay = document.createElement('div');
            overlay.id = 'xxt-course-select-overlay';
            overlay.className = 'modal-overlay active';
            overlay.innerHTML = `<div class="course-modal"><div class="modal-header"><div><h3 class="modal-title">选择要刷的学习通课程 (账号 ${accountIndex + 1})</h3><div class="modal-subtitle">共 ${courses.length} 门课程，${newCount} 门新课程，${dupCount} 门已添加</div></div><button class="modal-close" onclick="closeXuexitongCourseDialog()">&times;</button></div><div class="modal-body"><div class="course-select-actions"><button class="btn btn-outline btn-sm" onclick="xxtSelectAllCourses()"><i class="fas fa-check-double"></i> 全选</button><button class="btn btn-outline btn-sm" onclick="xxtDeselectAllCourses()"><i class="fas fa-times"></i> 取消全选</button><button class="btn btn-outline btn-sm" onclick="xxtSelectNewCoursesOnly()"><i class="fas fa-plus"></i> 仅选新课程</button></div><div class="course-select-list">${coursesWithStatus.map((c, i) => `<div class="course-select-item${c.isDuplicate ? ' course-duplicate' : ''}" onclick="xxtToggleCourseItem(this)"><input type="checkbox" id="xxt-cb-${i}" ${c.isDuplicate ? '' : 'checked'} ${c.isDuplicate ? 'data-duplicate="true"' : ''}><div class="course-select-info"><div class="course-select-name">${escapeHtml(c.name)}${c.isretire ? '<span class="course-archived-badge">已归档</span>' : ''}${c.isDuplicate ? '<span class="course-duplicate-badge">已添加</span>' : ''}</div><div class="course-select-meta"><span>${escapeHtml(c.courseId || '')}</span>${c.teacher ? `<span>${escapeHtml(c.teacher)}</span>` : ''}${c.school ? `<span>${escapeHtml(c.school)}</span>` : ''}</div></div></div>`).join('')}</div></div><div class="modal-footer"><button class="btn btn-outline" onclick="closeXuexitongCourseDialog()">取消</button><button class="btn btn-primary" onclick="confirmXuexitongCourseSelection(${accountIndex})"><i class="fas fa-check"></i> 确认添加</button></div></div>`;
            document.body.appendChild(overlay);
            window._xxtSelectCourses = coursesWithStatus;
        }

        function closeXuexitongCourseDialog() {
            const overlay = document.getElementById('xxt-course-select-overlay');
            if (overlay) overlay.remove();
        }

        function xxtToggleCourseItem(el) {
            const cb = el.querySelector('input[type="checkbox"]');
            if (cb && !cb.dataset.duplicate) cb.checked = !cb.checked;
            else if (cb && cb.dataset.duplicate) { cb.checked = !cb.checked; cb.dataset.duplicate = cb.checked ? '' : 'true'; el.classList.toggle('course-duplicate', !cb.checked); }
        }

        function xxtSelectAllCourses() {
            document.querySelectorAll('#xxt-course-select-overlay input[type="checkbox"]').forEach(cb => cb.checked = true);
        }

        function xxtDeselectAllCourses() {
            document.querySelectorAll('#xxt-course-select-overlay input[type="checkbox"]').forEach(cb => cb.checked = false);
        }

        function xxtSelectNewCoursesOnly() {
            document.querySelectorAll('#xxt-course-select-overlay input[type="checkbox"]').forEach(cb => {
                cb.checked = !cb.dataset.duplicate;
            });
        }

        async function confirmXuexitongCourseSelection(accountIndex) {
            const courses = window._xxtSelectCourses;
            if (!courses) { closeXuexitongCourseDialog(); return; }
            const checkboxes = document.querySelectorAll('#xxt-course-select-overlay input[type="checkbox"]');
            const selected = [];
            checkboxes.forEach((cb, i) => {
                if (cb.checked && courses[i]) { selected.push(courses[i].name); }
            });
            if (!selected.length) { showToast('请至少选择一门课程', 'warning'); return; }
            closeXuexitongCourseDialog();
            const card = document.querySelector(`.yatori-account-card[data-index="${accountIndex}"]`);
            if (!card) return;
            card.dataset.filterMode = 'include';
            updateYatoriCourseFilterUI(card);
            const ta = card.querySelector('[data-field="includeCourses"]');
            if (ta) ta.value = selected.join('\n');
            showToast(`已选择 ${selected.length} 门课程`, 'success');
        }

        function syncYatoriPlatformCard(card) { if (!card) return; const pc = card.querySelector('[data-field="accountType"]')?.value||'XUEXITONG', rule = getYatoriPlatformModeRule(pc); const b = card.querySelector('[data-role="platform-badge"]'); if(b)b.textContent=`(${getYatoriPlatformLabel(pc)})`; const uh = card.querySelector('[data-role="url-hint"]'); if(uh)uh.textContent=rule.urlHint; const eh = card.querySelector('[data-role="exam-hint"]'); if(eh)eh.textContent=rule.examHint; const vs = card.querySelector('[data-field="videoModel"]'); if(vs)vs.innerHTML=renderMappedOptions(rule.videoModes,getYatoriVideoModeLabels(pc),vs.value); const es = card.querySelector('[data-field="autoExam"]'); if(es)es.innerHTML=renderMappedOptions(rule.examModes,YATORI_EXAM_MODE_LABELS,es.value); const ss = card.querySelector('[data-field="examAutoSubmit"]'); if(ss)ss.innerHTML=renderMappedOptions(rule.submitModes,YATORI_SUBMIT_MODE_LABELS,ss.value); const sw = card.querySelector('[data-role="submit-wrap"]'); if(sw)sw.style.display=rule.examModes.some(v=>v!=='0')?'':'none'; const xxtb = card.querySelector('[id^="xxt-course-btn-"]'); if(xxtb)xxtb.style.display=pc==='XUEXITONG'?'':'none'; }
        function handleYatoriPlatformChange(s) { syncYatoriPlatformCard(s.closest('.yatori-account-card')); }
        function updateYatoriCourseFilterUI(card) { if(!card)return; const m=card.dataset.filterMode||''; const ib=card.querySelector('[data-filter-button="include"]'),eb=card.querySelector('[data-filter-button="exclude"]'),cb=card.querySelector('[data-filter-clear]'); const ip=card.querySelector('[data-filter-panel="include"]'),ep=card.querySelector('[data-filter-panel="exclude"]'); [ib,eb].forEach(b=>{if(!b)return;const a=b.dataset.filterButton===m;b.style.opacity=(!m||a)?'1':'0.45';b.style.pointerEvents=(!m||a)?'auto':'none';b.classList.toggle('btn-primary',a);b.classList.toggle('btn-outline',!a);}); if(cb)cb.style.display=m?'':'none'; if(ip)ip.style.display=m==='include'?'':'none'; if(ep)ep.style.display=m==='exclude'?'':'none'; }
        function toggleYatoriCourseFilter(b,m){const c=b.closest('.yatori-account-card');if(!c)return;c.dataset.filterMode=m;updateYatoriCourseFilterUI(c);}
        function clearYatoriCourseFilter(b){const c=b.closest('.yatori-account-card');if(!c)return;c.dataset.filterMode='';updateYatoriCourseFilterUI(c);}

        function gatherYatoriSettings() { const cards = [...document.querySelectorAll('.yatori-account-card')]; const users = cards.length?cards.map((card,idx)=>{const pc=card.querySelector('[data-field="accountType"]').value.trim()||'XUEXITONG',rule=getYatoriPlatformModeRule(pc),fm=card.dataset.filterMode||'';const vv=card.querySelector('[data-field="videoModel"]').value||rule.videoModes[0];const ev=card.querySelector('[data-field="autoExam"]').value||rule.examModes[0];const ssv=card.querySelector('[data-field="examAutoSubmit"]').value||rule.submitModes[0];const cn=card.querySelector('[data-field="cxNode"]'),st=card.querySelector('[data-field="studyTime"]'),cct=card.querySelector('[data-field="cxChapterTestSw"]'),cw=card.querySelector('[data-field="cxWorkSw"]'),ce=card.querySelector('[data-field="cxExamSw"]');const existingUser=state.settings?.yatori?.users?.[idx];const existingCoursesSettings=existingUser?.coursesCustom?.coursesSettings;return{accountType:pc,url:card.querySelector('[data-field="url"]').value.trim(),remarkName:card.querySelector('[data-field="remarkName"]').value.trim(),account:card.querySelector('[data-field="account"]').value.trim(),password:card.querySelector('[data-field="password"]').value,isProxy:card.querySelector('[data-field="isProxy"]').checked?1:0,informEmails:splitComma(card.querySelector('[data-field="informEmails"]').value),coursesCustom:{shuffleSw:card.querySelector('[data-field="shuffleSw"]').checked?1:0,videoModel:Number(rule.videoModes.includes(String(vv))?vv:rule.videoModes[0]),autoExam:Number(rule.examModes.includes(String(ev))?ev:rule.examModes[0]),examAutoSubmit:Number(rule.submitModes.includes(String(ssv))?ssv:rule.submitModes[0]),includeCourses:fm==='include'?splitLines(card.querySelector('[data-field="includeCourses"]').value):[],excludeCourses:fm==='exclude'?splitLines(card.querySelector('[data-field="excludeCourses"]').value):[],studyTime:st?st.value.trim():'',cxNode:cn?Number(cn.value)||3:3,cxChapterTestSw:cct?(cct.checked?1:0):1,cxWorkSw:cw?(cw.checked?1:0):1,cxExamSw:ce?(ce.checked?1:0):1,coursesSettings:Array.isArray(existingCoursesSettings)?existingCoursesSettings:[]}};}):(state.settings?.yatori?.users||[normalizeYatoriUser({},1)]);return{setting:{basicSetting:{completionTone:document.getElementById('y-completion-tone').checked?1:0,colorLog:document.getElementById('y-color-log').checked?1:0,logOutFileSw:document.getElementById('y-log-out-file').checked?1:0,logLevel:document.getElementById('y-log-level').value,logModel:Number(document.getElementById('y-log-model').value||0),WebModel:Number(document.getElementById('y-web-model').value||0)},emailInform:{sw:document.getElementById('y-email-sw').checked?1:0,SMTPHost:document.getElementById('y-smtp-host').value.trim(),SMTPPort:Number(document.getElementById('y-smtp-port').value||0),userName:document.getElementById('y-email-user').value.trim(),password:document.getElementById('y-email-password').value},aiSetting:{aiType:document.getElementById('y-ai-type').value||'TONGYI',aiUrl:document.getElementById('y-ai-url').value.trim(),model:document.getElementById('y-ai-model').value.trim(),API_KEY:document.getElementById('y-ai-api-key').value},apiQueSetting:{url:document.getElementById('y-api-url').value.trim()||'http://localhost:8083'}},users:users.length?users:[normalizeYatoriUser({},1)]}; }
        function gatherAutovisorSettings() { const cs=[...document.querySelectorAll('.autovisor-account-card')];const acs=cs.length?cs.map(c=>({account_id:Number(state.settings?.autovisor?.accounts?.[Number(c.dataset.index)]?.account_id||Number(c.dataset.index)+1),name:c.querySelector('[data-field="name"]').value.trim(),username:c.querySelector('[data-field="username"]').value.trim(),password:c.querySelector('[data-field="password"]').value,driver:document.getElementById('a-browser-driver')?.value||'Chrome',exe_path:document.getElementById('a-browser-path')?.value||'',enable_auto_captcha:c.querySelector('[data-field="enable_auto_captcha"]').checked,enable_hide_window:c.querySelector('[data-field="enable_hide_window"]').checked,limit_max_time:String(c.querySelector('[data-field="limit_max_time"]').value||'30'),limit_speed:String(c.querySelector('[data-field="limit_speed"]').value||'1.0'),sound_off:c.querySelector('[data-field="sound_off"]').checked,course_urls:splitLines(c.querySelector('[data-field="course_urls"]').value)})):(state.settings?.autovisor?.accounts||[]);return{multi_mode:document.getElementById('a-multi-mode')?.checked??false,browser_driver:document.getElementById('a-browser-driver')?.value||'Chrome',browser_path:document.getElementById('a-browser-path')?.value||'',accounts:acs.length?acs:[{account_id:1,name:'账号 1',username:'',password:'',driver:'Chrome',exe_path:'',enable_auto_captcha:true,enable_hide_window:false,limit_max_time:'30',limit_speed:'1.0',sound_off:true,course_urls:[]}]}; }

        function validateWebSettingsBeforeSave() {
            const a = gatherAutovisorSettings();
            for (let i = 0; i < a.accounts.length; i++) {
                if (a.accounts[i].enable_hide_window && (!a.accounts[i].username || !a.accounts[i].password)) {
                    return `Autovisor 账号 ${i + 1} 开启隐藏窗口后，必须填写账号和密码`;
                }
            }
            return '';
        }

        async function saveSettings(showSuccess = true) {
            captureCurrentConfigTab();
            const msg = validateWebSettingsBeforeSave();
            if (msg) { showToast(msg, 'error'); return { ok: false, message: msg }; }
            try {
                const result = await apiCall('save_settings', { yatori: gatherYatoriSettings(), autovisor: gatherAutovisorSettings(), questionbank: gatherQbSettings() });
                if (result?.ok && result.state) { renderSettings(result.state.settings); applyRuntimeState(unwrapState(result)); if (showSuccess) showToast('配置已保存', 'success'); }
                else { showToast(result?.message || '保存失败', 'error'); }
                return result;
            } catch (error) { if (!error?.silent) showToast(error.message || '保存失败', 'error'); return { ok: false, message: error.message || '保存失败', silent: !!error?.silent }; }
        }

        async function cancelShutdown() {
            try {
                const result = await apiCall('cancel_shutdown');
                if (result?.ok) {
                    showToast('已取消自动关机', 'success');
                    document.getElementById('shutdown-modal')?.classList.remove('active');
                    if (window._shutdownTimer) { clearInterval(window._shutdownTimer); window._shutdownTimer = null; }
                } else {
                    showToast(result?.message || '取消关机失败', 'error');
                }
            } catch (error) {
                showToast(error.message || '取消关机失败', 'error');
            }
        }

        async function handleCoreAction(core) {
            try {
                const running = core === 'yatori' ? !!state.runtime?.yatori_running : !!state.runtime?.autovisor_running;
                const action = running ? 'stop' : 'start';
                if (action === 'start') {
                    const sr = await saveSettings(false);
                    if (!sr?.ok) return;
                }
                const result = await apiCall('perform_action', action, core);                                            
                handleWebActionResult(result, '核心操作失败');
            } catch (error) { if (!error?.silent) showToast(error.message || '操作失败', 'error'); }
        }

        async function toggleQuestionBank() {
            try { const result = await apiCall('perform_action', 'toggle_question_bank'); handleWebActionResult(result, '题库操作失败'); }
            catch (error) { showToast(error.message || '题库操作失败', 'error'); }
        }

        async function saveAndPerform(action) {
            try { const sr = await saveSettings(false); if (!sr?.ok) return; const result = await apiCall('perform_action', action); handleWebActionResult(result, '操作失败'); }
            catch (error) { if (!error?.silent) showToast(error.message || '操作失败', 'error'); }
        }

        function handleWebActionResult(result, fallbackMessage = '操作失败') {
            if (!result?.ok) {
                showToast(result?.message || fallbackMessage, 'error');
                if (result?.state) applyRuntimeState(unwrapState(result));
                return false;
            }
            applyRuntimeState(unwrapState(result));
            if (result?.toast) showToast(result.toast, result.toastType || 'success');
            return true;
        }

        async function performAction(action, ...args) {
            try { const result = await apiCall('perform_action', action, ...args); handleWebActionResult(result); }
            catch (error) { if (!error?.silent) showToast(error.message || '操作失败', 'error'); }
        }

        async function confirmAndPerform(action, message) {
            if (!window.confirm(message)) return;
            await performAction(action);
        }

        function copyApiUrl() {
             const urlInput = document.getElementById('y-api-url');
             if (urlInput) {
                 urlInput.select();
                 navigator.clipboard.writeText(urlInput.value).then(() => {
                     showToast('API 地址已复制到剪贴板', 'success');
                 }).catch(() => {
                     showToast('复制失败，请手动复制', 'error');
                 });
             }
         }

         // 题库页面AI配置同步
         const QB_AI_DEFAULTS = {
             SILICON: { url: 'https://api.siliconflow.cn/v1/chat/completions', model: 'Qwen/Qwen2.5-7B-Instruct', name: '硅基流动' },
             DEEPSEEK: { url: 'https://api.deepseek.com/v1/chat/completions', model: 'deepseek-chat', name: 'DeepSeek' },
             CHATGLM: { url: 'https://open.bigmodel.cn/api/paas/v4/chat/completions', model: 'glm-4-flash', name: '智谱AI' },
             TONGYI: { url: 'https://dashscope.aliyuncs.com/api/v1/services/aigc/text-generation/generation', model: 'qwen-max', name: '通义千问' },
             XINGHUO: { url: 'https://spark-api-open.xf-yun.com/v1/chat/completions', model: 'generalv3.5', name: '讯飞星火' },
             DOUBAO: { url: 'https://ark.cn-beijing.volces.com/api/v3/chat/completions', model: 'doubao-pro-32k', name: '豆包' },
             METAAI: { url: 'https://api.meta.ai/v1/chat/completions', model: 'llama-3-70b', name: 'MetaAI' },
             OPENAI: { url: 'https://api.openai.com/v1/chat/completions', model: 'gpt-3.5-turbo', name: 'OpenAI' },
             OTHER: { url: '', model: '', name: '其他' }
         };

         // 补齐URL为完整端点
         function normalizeApiUrl(url, provider) {
             if (!url || provider !== 'OTHER') return url;
             url = url.trim();
             // 移除末尾的斜杠
             url = url.replace(/\/$/, '');
             // 如果已经包含chat/completions，不再添加
             if (url.includes('/chat/completions')) return url;
             // 如果包含/v1，添加chat/completions
             if (url.endsWith('/v1')) return url + '/chat/completions';
             // 其他情况添加 /v1/chat/completions
             return url + '/v1/chat/completions';
         }

         function syncQbAiField() {
             const type = document.getElementById('qb-ai-type')?.value || 'SILICON';
             const defaults = QB_AI_DEFAULTS[type] || QB_AI_DEFAULTS.OTHER;
             
             const urlEl = document.getElementById('qb-ai-url');
             const modelEl = document.getElementById('qb-ai-model');
             const hintEl = document.getElementById('qb-ai-url-hint');
             
             if (urlEl && !urlEl.value) {
                 urlEl.value = defaults.url;
             }
             if (modelEl && !modelEl.value) {
                 modelEl.value = defaults.model;
             }
             if (hintEl) {
                 const displayUrl = type === 'OTHER' ? '自定义URL' : defaults.url;
                 hintEl.textContent = `预览: ${displayUrl}`;
             }
         }

         // URL输入时自动补齐
         function onQbUrlInput() {
             const type = document.getElementById('qb-ai-type')?.value || 'SILICON';
             const urlEl = document.getElementById('qb-ai-url');
             const hintEl = document.getElementById('qb-ai-url-hint');
             
             if (type === 'OTHER' && urlEl && hintEl) {
                 const normalized = normalizeApiUrl(urlEl.value, type);
                 hintEl.textContent = `预览: ${normalized || '请输入完整的API地址'}`;
             }
         }

         // 获取模型列表
          async function fetchModelList() {
              const btn = document.getElementById('qb-fetch-models-btn');
              const loading = document.getElementById('qb-fetch-loading');
              const icon = document.getElementById('qb-fetch-icon');
              const datalist = document.getElementById('qb-model-list');
              const input = document.getElementById('qb-ai-model');

              // 获取配置
              const provider = document.getElementById('qb-ai-type')?.value || 'SILICON';
              const apiUrl = document.getElementById('qb-ai-url')?.value || '';
              const apiKey = document.getElementById('qb-ai-api-key')?.value || '';

              if (!apiKey.trim()) {
                  showToast('请先输入 API Key', 'error');
                  return;
              }

              if (!apiUrl.trim()) {
                  showToast('请先输入 API 地址', 'error');
                  return;
              }

              // 设置加载状态
              btn.disabled = true;
              icon.style.display = 'none';
              loading.style.display = 'inline-block';

              try {
                  const result = await apiCall('fetch_model_list', {
                      provider: provider,
                      api_url: apiUrl,
                      api_key: apiKey
                  });

                  if (result?.success && result.models && result.models.length > 0) {
                      // 保存当前输入的值
                      const currentValue = input.value;
                      
                      // 清空并重新填充模型列表
                      datalist.innerHTML = '';
                      result.models.forEach(model => {
                          const option = document.createElement('option');
                          option.value = model.id || model;
                          option.textContent = model.name || model.id || model;
                          datalist.appendChild(option);
                      });
                      
                      showToast(`成功获取 ${result.models.length} 个模型`, 'success');
                  } else if (result?.models && result.models.length === 0) {
                      showToast('未获取到模型列表，请手动输入', 'warning');
                  } else {
                      showToast(result?.message || '获取模型列表失败', 'error');
                  }
              } catch (error) {
                  showToast(error?.message || '获取模型列表时发生错误', 'error');
              } finally {
                  btn.disabled = false;
                  icon.style.display = 'inline-block';
                  loading.style.display = 'none';
              }
          }

         function toggleQbApiKeyVisibility() {
             const input = document.getElementById('qb-ai-api-key');
             const icon = document.getElementById('qb-ai-key-icon');
             if (input && icon) {
                 if (input.type === 'password') {
                     input.type = 'text';
                     icon.className = 'fas fa-eye-slash';
                 } else {
                     input.type = 'password';
                     icon.className = 'fas fa-eye';
                 }
             }
         }

         // AI 连通性测试
         async function testAIConnectivity() {
             const btn = document.getElementById('qb-test-ai-btn');
             const loading = document.getElementById('qb-test-loading');
             const resultDiv = document.getElementById('qb-test-result');
             const resultIcon = document.getElementById('qb-test-icon');
             const resultMsg = document.getElementById('qb-test-message');

             // 获取配置
             const provider = document.getElementById('qb-ai-type')?.value || 'SILICON';
             const apiUrl = document.getElementById('qb-ai-url')?.value || '';
             const apiKey = document.getElementById('qb-ai-api-key')?.value || '';
             const model = document.getElementById('qb-ai-model')?.value || '';

             if (!apiKey.trim()) {
                 showToast('请先输入 API Key', 'error');
                 return;
             }

             // 设置测试状态
             btn.disabled = true;
             loading.style.display = 'inline-block';
             resultDiv.style.display = 'none';

             try {
                 const result = await apiCall('test_ai_connectivity', {
                     provider: provider,
                     api_url: apiUrl,
                     api_key: apiKey,
                     model: model
                 });

                 // 显示结果
                 resultDiv.style.display = 'flex';
                 if (result?.success) {
                     resultDiv.className = 'qb-test-result success';
                     resultIcon.className = 'fas fa-check-circle';
                     resultMsg.textContent = result.message || '连接成功！';
                     showToast('AI 连通性测试成功', 'success');
                 } else {
                     resultDiv.className = 'qb-test-result error';
                     resultIcon.className = 'fas fa-exclamation-circle';
                     resultMsg.textContent = result?.message || '连接失败，请检查配置';
                     showToast('AI 连通性测试失败', 'error');
                 }
             } catch (error) {
                 resultDiv.style.display = 'flex';
                 resultDiv.className = 'qb-test-result error';
                 resultIcon.className = 'fas fa-exclamation-circle';
                 resultMsg.textContent = error?.message || '测试过程发生错误';
                 showToast(error?.message || '测试过程发生错误', 'error');
             } finally {
                 btn.disabled = false;
                 loading.style.display = 'none';
             }
         }
 
         // Yatori AI配置默认值（使用完整URL）
         const YATORI_AI_DEFAULTS = {
             SILICON: { url: 'https://api.siliconflow.cn/v1/chat/completions', model: 'Qwen/Qwen2.5-7B-Instruct' },
             DEEPSEEK: { url: 'https://api.deepseek.com/v1/chat/completions', model: 'deepseek-chat' },
             CHATGLM: { url: 'https://open.bigmodel.cn/api/paas/v4/chat/completions', model: 'glm-4-flash' },
             TONGYI: { url: 'https://dashscope.aliyuncs.com/api/v1/services/aigc/text-generation/generation', model: 'qwen-max' },
             XINGHUO: { url: 'https://spark-api-open.xf-yun.com/v1/chat/completions', model: 'generalv3.5' },
             DOUBAO: { url: 'https://ark.cn-beijing.volces.com/api/v3/chat/completions', model: 'doubao-pro-32k' },
             METAAI: { url: 'https://api.meta.ai/v1/chat/completions', model: 'llama-3-70b' },
             OPENAI: { url: 'https://api.openai.com/v1/chat/completions', model: 'gpt-3.5-turbo' },
             OTHER: { url: '', model: '' }
         };

         function syncYatoriAiField() {
             const type = document.getElementById('y-ai-type').value;
             const defaults = YATORI_AI_DEFAULTS[type] || YATORI_AI_DEFAULTS.OTHER;
             const uEl = document.getElementById('y-ai-url');
             const mEl = document.getElementById('y-ai-model');
             const pEl = document.getElementById('y-ai-url-preview');
             
             if (uEl && !uEl.value) uEl.value = defaults.url;
             if (mEl && !mEl.value) {
                 // 如果是空选项，设置为默认值
                 const option = document.createElement('option');
                 option.value = defaults.model;
                 option.textContent = defaults.model;
                 mEl.appendChild(option);
                 mEl.value = defaults.model;
             }
             if (pEl) {
                 const displayUrl = type === 'OTHER' ? '自定义URL' : (defaults.url || '未设置');
                 pEl.textContent = `预览: ${displayUrl}`;
             }
         }

         // Yatori URL输入时自动补齐
         function onYatoriUrlInput() {
             const type = document.getElementById('y-ai-type').value;
             const urlEl = document.getElementById('y-ai-url');
             const pEl = document.getElementById('y-ai-url-preview');
             
             if (type === 'OTHER' && urlEl && pEl) {
                 const normalized = normalizeApiUrl(urlEl.value, type);
                 pEl.textContent = `预览: ${normalized || '请输入完整的API地址'}`;
             }
         }

         // Yatori API Key显示/隐藏切换
         function toggleYatoriApiKeyVisibility() {
             const input = document.getElementById('y-ai-api-key');
             const icon = document.getElementById('y-ai-key-icon');
             if (input && icon) {
                 if (input.type === 'password') {
                     input.type = 'text';
                     icon.className = 'fas fa-eye-slash';
                 } else {
                     input.type = 'password';
                     icon.className = 'fas fa-eye';
                 }
             }
         }

         // Yatori AI连通性测试
         async function testYatoriAIConnectivity() {
             const btn = document.getElementById('y-test-ai-btn');
             const loading = document.getElementById('y-test-loading');
             const resultDiv = document.getElementById('y-test-result');
             const resultIcon = document.getElementById('y-test-result-icon');
             const resultMsg = document.getElementById('y-test-result-message');

             const provider = document.getElementById('y-ai-type').value;
             const apiUrl = document.getElementById('y-ai-url').value;
             const apiKey = document.getElementById('y-ai-api-key').value;
             const model = document.getElementById('y-ai-model').value;

             if (!apiKey.trim()) {
                 showToast('请先输入 API Key', 'error');
                 return;
             }

             btn.disabled = true;
             loading.style.display = 'inline-block';
             resultDiv.style.display = 'none';

             try {
                 const result = await apiCall('test_ai_connectivity', {
                     provider: provider,
                     api_url: apiUrl,
                     api_key: apiKey,
                     model: model
                 });

                 resultDiv.style.display = 'block';
                 if (result?.success) {
                     resultDiv.style.background = 'rgba(16, 185, 129, 0.1)';
                     resultDiv.style.border = '1px solid rgba(16, 185, 129, 0.3)';
                     resultDiv.style.color = 'var(--success)';
                     resultIcon.className = 'fas fa-check-circle';
                     resultMsg.textContent = result.message || '连接成功！';
                     showToast('AI 连通性测试成功', 'success');
                 } else {
                     resultDiv.style.background = 'rgba(239, 68, 68, 0.1)';
                     resultDiv.style.border = '1px solid rgba(239, 68, 68, 0.3)';
                     resultDiv.style.color = 'var(--danger)';
                     resultIcon.className = 'fas fa-exclamation-circle';
                     resultMsg.textContent = result?.message || '连接失败，请检查配置';
                     showToast('AI 连通性测试失败', 'error');
                 }
             } catch (error) {
                 resultDiv.style.display = 'block';
                 resultDiv.style.background = 'rgba(239, 68, 68, 0.1)';
                 resultDiv.style.border = '1px solid rgba(239, 68, 68, 0.3)';
                 resultDiv.style.color = 'var(--danger)';
                 resultIcon.className = 'fas fa-exclamation-circle';
                 resultMsg.textContent = error?.message || '测试过程发生错误';
                 showToast(error?.message || '测试过程发生错误', 'error');
             } finally {
                 btn.disabled = false;
                 loading.style.display = 'none';
             }
         }

         // Yatori获取模型列表
          async function fetchYatoriModelList() {
              const btn = document.getElementById('y-fetch-models-btn');
              const loading = document.getElementById('y-fetch-loading');
              const icon = document.getElementById('y-fetch-icon');
              const datalist = document.getElementById('y-model-list');
              const input = document.getElementById('y-ai-model');

              const provider = document.getElementById('y-ai-type').value;
              const apiUrl = document.getElementById('y-ai-url').value;
              const apiKey = document.getElementById('y-ai-api-key').value;

              if (!apiKey.trim()) {
                  showToast('请先输入 API Key', 'error');
                  return;
              }

              if (!apiUrl.trim()) {
                  showToast('请先输入 API 地址', 'error');
                  return;
              }

              btn.disabled = true;
              icon.style.display = 'none';
              loading.style.display = 'inline-block';

              try {
                  const result = await apiCall('fetch_model_list', {
                      provider: provider,
                      api_url: apiUrl,
                      api_key: apiKey
                  });

                  if (result?.success && result.models && result.models.length > 0) {
                      // 保存当前输入的值
                      const currentValue = input.value;
                      
                      // 清空并重新填充模型列表
                      datalist.innerHTML = '';
                      result.models.forEach(model => {
                          const option = document.createElement('option');
                          option.value = model.id || model;
                          option.textContent = model.name || model.id || model;
                          datalist.appendChild(option);
                      });
                      
                      showToast(`成功获取 ${result.models.length} 个模型`, 'success');
                  } else {
                      showToast(result?.message || '获取模型列表失败', 'error');
                  }
              } catch (error) {
                  showToast(error?.message || '获取模型列表时发生错误', 'error');
              } finally {
                  btn.disabled = false;
                  icon.style.display = 'inline-block';
                  loading.style.display = 'none';
              }
          }

         // ===== 题库设置本地保存 =====
         function gatherQbSettings() {
             return {
                 auto_start: document.getElementById('qb-auto-start')?.checked ?? true,
                 ai_enabled: document.getElementById('qb-ai-enabled')?.checked ?? false,
                 ai_type: document.getElementById('qb-ai-type')?.value || 'SILICON',
                 ai_url: document.getElementById('qb-ai-url')?.value || '',
                 ai_model: document.getElementById('qb-ai-model')?.value || '',
                 ai_api_key: document.getElementById('qb-ai-api-key')?.value || '',
                 auto_save: document.getElementById('qb-auto-save')?.checked ?? true,
                 port: parseInt(document.getElementById('y-qb-port')?.value || '8083'),
             };
         }

         function loadQbSettings(settings) {
             if (!settings || typeof settings !== 'object') return;
             const setCheck = (id, val) => { const el = document.getElementById(id); if (el) el.checked = !!val; };
             const setValue = (id, val) => { const el = document.getElementById(id); if (el && val != null) el.value = val; };
             setCheck('qb-auto-start', settings.auto_start !== false);
             setCheck('qb-ai-enabled', settings.ai_enabled);
             setValue('qb-ai-type', settings.ai_type || 'SILICON');
             setValue('qb-ai-url', settings.ai_url || '');
             setValue('qb-ai-model', settings.ai_model || '');
             setValue('qb-ai-api-key', settings.ai_api_key || '');
             setCheck('qb-auto-save', settings.auto_save !== false);
             setValue('y-qb-port', settings.port || 8083);
             // 触发URL预览更新
             syncQbAiField();
         }

         async function saveQbSettings() {
             try {
                 const settings = gatherQbSettings();
                 const result = await apiCall('save_qb_settings', settings);
                 return result;
             } catch (error) {
                 if (!error?.silent) showToast('题库设置保存失败: ' + (error.message || ''), 'error');
                 return { ok: false };
             }
         }

         async function autoSaveQbSettings() {
              // 防抖：等待输入停止后再保存
              if (window._qbSaveTimer) clearTimeout(window._qbSaveTimer);
              const statusEl = document.getElementById('qb-save-status');
              if (statusEl) statusEl.textContent = '正在保存...';
              window._qbSaveTimer = setTimeout(async () => {
                  const result = await saveQbSettings();
                  if (statusEl) statusEl.textContent = result?.ok ? '已自动保存' : '保存失败';
                  setTimeout(() => { if (statusEl && statusEl.textContent === '已自动保存') statusEl.textContent = 'AI配置将自动保存'; }, 3000);
              }, 800);
          }

        function syncAutovisorMulti(checked) {
            const toggle = document.getElementById('dashboard-autovisor-multi');
            const setting = document.getElementById('a-multi-mode');
            if (toggle && toggle.checked !== checked) toggle.checked = !!checked;
            if (setting && setting.checked !== checked) setting.checked = !!checked;
        }

        function addYatoriAccount() { if (!state.settings) { showToast('请先等待配置加载', 'error'); return; } state.settings.yatori.users.push(normalizeYatoriUser({}, state.settings.yatori.users.length + 1)); renderYatoriAccounts(state.settings.yatori.users); }
        function removeYatoriAccount(index) { if (!state.settings) return; state.settings.yatori.users.splice(index, 1); renderYatoriAccounts(state.settings.yatori.users); }
        function addAutovisorAccount() { if (!state.settings) { showToast('请先等待配置加载', 'error'); return; } const nextId=Math.max(0,...state.settings.autovisor.accounts.map(a=>Number(a.account_id)||0))+1; state.settings.autovisor.accounts.push(normalizeAutovisorAccount({account_id:nextId}, nextId)); renderAutovisorAccounts(state.settings.autovisor.accounts); }
        function removeAutovisorAccount(index) { if (!state.settings) return; state.settings.autovisor.accounts.splice(index, 1); renderAutovisorAccounts(state.settings.autovisor.accounts); }

        async function detectBrowserPath() {
            try { const r = await apiCall('detect_browser_path', document.getElementById('a-browser-driver')?.value || 'Chrome'); if (r?.path) { document.getElementById('a-browser-path').value = r.path; showToast('已自动识别浏览器路径', 'success'); } else { showToast('未能自动识别浏览器', 'error'); } }
            catch (e) { showToast('自动识别失败', 'error'); }
        }
        async function browseBrowserPath() {
            try { const r = await apiCall('browse_browser_path'); if (r?.path) { document.getElementById('a-browser-path').value = r.path; } }
            catch (e) { showToast('选择浏览器失败', 'error'); }
        }

        function loadStoredPreferences() { try { const raw = localStorage.getItem(PREFERENCES_STORAGE_KEY); if (!raw) return {}; const parsed = JSON.parse(raw); return parsed && typeof parsed === 'object' ? parsed : {}; } catch (e) { return {}; } }
        function persistStoredPreferences() { try { localStorage.setItem(PREFERENCES_STORAGE_KEY, JSON.stringify(state.preferences || {})); } catch (e) {} }

        function loadPreferences() {
            state.preferences = { autoStart: false, autoShutdown: false, autoRun: false, minimizeToTray: false, startMinimized: false, alwaysOnTop: false, notifyOnComplete: true, notifyOnError: true, soundEnabled: true, rememberGeometry: false, autoCleanLogs: false, theme: 'dark', bgType: 'none', bgUrl: '', ...loadStoredPreferences() };
            const set = (id, val) => { const el = document.getElementById(id); if (el) el.checked = !!val; };
            set('pref-auto-start', state.preferences.autoStart);
            set('pref-auto-shutdown', state.preferences.autoShutdown);
            set('pref-auto-run', state.preferences.autoRun);
            set('pref-minimize-tray', state.preferences.minimizeToTray);
            set('pref-start-minimized', state.preferences.startMinimized);
            set('pref-always-on-top', state.preferences.alwaysOnTop);
            set('pref-notify-complete', state.preferences.notifyOnComplete);
            set('pref-notify-error', state.preferences.notifyOnError);
            set('pref-sound-enabled', state.preferences.soundEnabled);
            set('pref-remember-geometry', state.preferences.rememberGeometry);
            set('pref-auto-clean-logs', state.preferences.autoCleanLogs);
            applySavedTheme();
            applySavedBg();
            try { apiCall('get_preferences').then(prefs => { if (prefs && typeof prefs === 'object') { state.preferences = { ...state.preferences, ...prefs }; set('pref-auto-start', prefs.autoStart); set('pref-auto-shutdown', prefs.autoShutdown); set('pref-auto-run', prefs.autoRun); set('pref-minimize-tray', prefs.minimizeToTray); set('pref-start-minimized', prefs.startMinimized); set('pref-always-on-top', prefs.alwaysOnTop); set('pref-notify-complete', prefs.notifyOnComplete); set('pref-notify-error', prefs.notifyOnError); set('pref-sound-enabled', prefs.soundEnabled); set('pref-remember-geometry', prefs.rememberGeometry); set('pref-auto-clean-logs', prefs.autoCleanLogs); persistStoredPreferences(); applySavedTheme(); applySavedBg(); } }).catch(() => {}); } catch (e) {}
        }

        async function savePreference(key, value) { state.preferences[key] = value; persistStoredPreferences(); if (!bridge()) return; try { await apiCall('save_preference', { [key]: value }); } catch (e) {} }

        if (typeof window !== 'undefined') { window.launcherAPI = { requestExit: function() { if (!exitConfirmed) showExitModal(); }, getTheme: function() { return getCurrentTheme(); } }; }

        function getCurrentTheme() { return document.documentElement.getAttribute('data-theme') || 'dark'; }
        function setTheme(theme) {
            const root = document.documentElement;
            if (theme === 'light') { root.setAttribute('data-theme', 'light'); }
            else { root.removeAttribute('data-theme'); }
            updateThemeIcons();
            state.preferences.theme = theme;
            persistStoredPreferences();
            if (!bridge()) return;
            try { apiCall('save_preference', { theme: theme }); } catch (e) {}
        }

        function updateThemeIcons() {
            const isDark = getCurrentTheme() === 'dark';
            const themeIcon = document.getElementById('theme-icon');
            const themeIconPref = document.getElementById('theme-icon-pref');
            const themeLabel = document.getElementById('theme-label');
            if (themeIcon) themeIcon.className = isDark ? 'fas fa-moon' : 'fas fa-sun';
            if (themeIconPref) themeIconPref.className = isDark ? 'fas fa-moon' : 'fas fa-sun';
            if (themeLabel) themeLabel.textContent = isDark ? '黑夜模式' : '白天模式';
        }

        function toggleTheme() { setTheme(getCurrentTheme() === 'dark' ? 'light' : 'dark'); }
        function applySavedTheme() { const s = state.preferences?.theme; if (s === 'light' || s === 'dark') setTheme(s); }

        function showExitModal() { exitConfirmed = false; const modal = document.getElementById('exit-modal'); if (modal) modal.classList.add('active'); }
        function hideExitModal() { const modal = document.getElementById('exit-modal'); if (modal) modal.classList.remove('active'); }
        function confirmExit() {
             exitConfirmed = true;
             hideExitModal();
             // 通知后端执行退出（不等待响应）
             const api = bridge();
             if (api && typeof api.perform_action === 'function') {
                 try {
                     // 使用同步方式调用，不等待Promise完成
                     api.perform_action('exit_app');
                 } catch (e) {
                     // 忽略错误
                 }
             }
             // 立即尝试关闭窗口
             try { window.close(); } catch (e) {}
         }

        function showBgModal() { const modal = document.getElementById('bg-modal'); if (!modal) return; syncBgModalState(); modal.classList.add('active'); }
        function hideBgModal() { const modal = document.getElementById('bg-modal'); if (modal) modal.classList.remove('active'); }
        function syncBgModalState() {
            const grids = ['bg-preview-grid', 'bg-modal-grid'];
            grids.forEach(gId => {
                const grid = document.getElementById(gId);
                if (!grid) return;
                grid.querySelectorAll('.bg-preview-item').forEach(item => { item.classList.toggle('active', item.dataset.bg === currentBgType); });
            });
            const customRow = document.getElementById('modal-custom-bg-row');
            if (customRow) customRow.style.display = currentBgType === 'custom' ? '' : 'none';
            const customInput = document.getElementById('modal-custom-bg-url');
            if (customInput) customInput.value = state.preferences?.bgUrl || '';
        }

        function setBackground(type, element) {
            const grid = element.closest('.bg-preview-grid');
            if (grid) { grid.querySelectorAll('.bg-preview-item').forEach(item => item.classList.remove('active')); element.classList.add('active'); }
            applyBackground(type);
        }

        function setBackgroundFromModal(type, element) {
            setBackground(type, element);
            updatePreferencesBg(type);
        }

        function applyBackground(type) {
            currentBgType = type;
            const body = document.body;
            body.classList.remove('has-custom-bg', 'bg-cyber-teal', 'bg-hacker-amber', 'bg-midnight-aurora');
            body.style.background = '';
            body.style.backgroundImage = '';
            if (type === 'gradient1') { body.style.background = 'linear-gradient(135deg, #0a0b0f 0%, #1a1a3e 50%, #0f2027 100%)'; body.classList.add('has-custom-bg'); }
            else if (type === 'gradient2') { body.style.background = 'linear-gradient(135deg, #1a1a2e 0%, #16213e 50%, #0f3460 100%)'; body.classList.add('has-custom-bg'); }
            else if (type === 'cyber-teal' || type === 'hacker-amber' || type === 'midnight-aurora') { body.classList.add('has-custom-bg', 'bg-' + type); }
            else if (type === 'custom') {
                const url = state.preferences?.bgUrl || '';
                if (url) { body.style.backgroundImage = `url(${url})`; body.classList.add('has-custom-bg'); }
            }
        }

        function applyGlassBlur(blurValue) {
            // 应用毛玻璃模糊度到所有glass-card元素
            const cards = document.querySelectorAll('.glass-card');
            const blurPx = parseInt(blurValue) || 16;
            cards.forEach(card => {
                card.style.backdropFilter = `blur(${blurPx}px) saturate(1.2)`;
                card.style.webkitBackdropFilter = `blur(${blurPx}px) saturate(1.2)`;
            });
            // 同时更新modal的模糊度
            const modals = document.querySelectorAll('.modal-box');
            modals.forEach(modal => {
                modal.style.backdropFilter = `blur(${Math.max(blurPx, 20)}px) saturate(1.2)`;
                modal.style.webkitBackdropFilter = `blur(${Math.max(blurPx, 20)}px) saturate(1.2)`;
            });
        }

        function updateGlassBlur(value) {
            const blurValue = parseInt(value);
            state.preferences.glassBlur = blurValue;
            // 更新显示值
            const valueEl = document.getElementById('glass-blur-value');
            if (valueEl) valueEl.textContent = blurValue + 'px';
            // 应用模糊度
            applyGlassBlur(blurValue);
            // 保存偏好
            persistStoredPreferences();
            if (!bridge()) return;
            try { apiCall('save_preference', { glassBlur: blurValue }); } catch (e) {}
        }

        function updateOverlayStrength(value) {
            const strength = parseInt(value);
            state.preferences.overlayStrength = strength;
            const valueEl = document.getElementById('overlay-strength-value');
            if (valueEl) valueEl.textContent = strength + '%';
            const alpha = 0.15 + (strength / 100) * 0.70;
            const blurPx = Math.round((strength / 100) * 24);
            document.body.style.setProperty('--overlay-alpha', String(alpha));
            document.body.style.setProperty('--overlay-blur', blurPx + 'px');
            persistStoredPreferences();
            if (!bridge()) return;
            try { apiCall('save_preference', { overlayStrength: strength }); } catch (e) {}
        }

        function applyOverlayStrengthFromPrefs() {
            const strength = state.preferences?.overlayStrength ?? 70;
            const slider = document.getElementById('overlay-strength-slider');
            const valueEl = document.getElementById('overlay-strength-value');
            if (slider) slider.value = strength;
            if (valueEl) valueEl.textContent = strength + '%';
            const alpha = 0.15 + (strength / 100) * 0.70;
            const blurPx = Math.round((strength / 100) * 24);
            document.body.style.setProperty('--overlay-alpha', String(alpha));
            document.body.style.setProperty('--overlay-blur', blurPx + 'px');
        }

        function updatePreferencesBg(type) {
            state.preferences.bgType = type;
            if (type !== 'custom') state.preferences.bgUrl = '';
            persistStoredPreferences();
            if (!bridge()) return;
            try { apiCall('save_preference', { bgType: type, bgUrl: state.preferences.bgUrl || '' }); } catch (e) {}
            syncBgModalState();
            const prefGrid = document.getElementById('bg-preview-grid');
            if (prefGrid) { prefGrid.querySelectorAll('.bg-preview-item').forEach(item => item.classList.toggle('active', item.dataset.bg === type)); }
            const customRow = document.getElementById('custom-bg-row');
            if (customRow) customRow.style.display = type === 'custom' ? 'flex' : 'none';
            if (type === 'custom') { const el = document.getElementById('custom-bg-url'); if (el) el.value = state.preferences?.bgUrl || ''; }
            // 显示/隐藏背景强度滑块
            const strengthRow = document.getElementById('overlay-strength-row');
            if (strengthRow) strengthRow.style.display = type !== 'none' ? 'flex' : 'none';
            if (type !== 'none') applyOverlayStrengthFromPrefs();
        }

        function applySavedBg() {
            const type = state.preferences?.bgType || 'none';
            currentBgType = type;
            applyBackground(type);
            const grid = document.getElementById('bg-preview-grid');
            if (grid) { grid.querySelectorAll('.bg-preview-item').forEach(item => item.classList.toggle('active', item.dataset.bg === type)); }
            const customRow = document.getElementById('custom-bg-row');
            if (customRow) customRow.style.display = type === 'custom' ? 'flex' : 'none';
            if (type === 'custom') { const el = document.getElementById('custom-bg-url'); if (el) el.value = state.preferences?.bgUrl || ''; }
            // 加载并应用毛玻璃模糊度设置
             const blurValue = state.preferences?.glassBlur ?? 16;
             const slider = document.getElementById('glass-blur-slider');
             const valueEl = document.getElementById('glass-blur-value');
             if (slider) slider.value = blurValue;
             if (valueEl) valueEl.textContent = blurValue + 'px';
             applyGlassBlur(blurValue);
            // 加载并应用背景强度设置
            const strengthRow = document.getElementById('overlay-strength-row');
            if (strengthRow) strengthRow.style.display = type !== 'none' ? 'flex' : 'none';
            if (type !== 'none') applyOverlayStrengthFromPrefs();
        }

        function showCustomBgInput() {
            const row = document.getElementById('custom-bg-row');
            if (row) row.style.display = 'flex';
            const el = document.getElementById('custom-bg-url');
            if (el) { el.value = state.preferences?.bgUrl || ''; el.focus(); }
        }

        function applyCustomBackground() {
            const el = document.getElementById('custom-bg-url');
            const url = el ? el.value.trim() : '';
            if (!url) { showToast('请输入图片 URL', 'error'); return; }
            state.preferences.bgUrl = url;
            currentBgType = 'custom';
            applyBackground('custom');
            updatePreferencesBg('custom');
            hideBgModal();
        }

        function triggerBgFileInput() {
            const input = document.getElementById('bg-file-input');
            if (input) input.click();
        }

        function triggerCustomBgInModal() {
            const row = document.getElementById('modal-custom-bg-row');
            if (row) row.style.display = 'block';
            const el = document.getElementById('modal-custom-bg-url');
            if (el) { el.value = state.preferences?.bgUrl || ''; el.focus(); }
        }

        function applyModalCustomBg() {
            const el = document.getElementById('modal-custom-bg-url');
            const url = el ? el.value.trim() : '';
            if (!url) { showToast('请输入图片 URL', 'error'); return; }
            state.preferences.bgUrl = url;
            currentBgType = 'custom';
            applyBackground('custom');
            updatePreferencesBg('custom');
            hideBgModal();
        }

        function handleBgFileUpload(input) {
            const file = input.files?.[0];
            if (!file) return;
            if (!file.type.startsWith('image/')) { showToast('请选择图片文件', 'error'); return; }
            const reader = new FileReader();
            reader.onload = function(e) {
                const dataUrl = e.target.result;
                state.preferences.bgUrl = dataUrl;
                currentBgType = 'custom';
                applyBackground('custom');
                updatePreferencesBg('custom');
                const modalUrl = document.getElementById('modal-custom-bg-url');
                if (modalUrl) modalUrl.value = dataUrl;
                const customUrl = document.getElementById('custom-bg-url');
                if (customUrl) customUrl.value = dataUrl;
                hideBgModal();
            };
            reader.readAsDataURL(file);
            input.value = '';
        }

        function resetBackground() {
            currentBgType = 'none';
            state.preferences.bgType = 'none';
            state.preferences.bgUrl = '';
            state.preferences.glassBlur = 16;
             applyBackground('none');
             // 重置毛玻璃模糊度滑块
             const slider = document.getElementById('glass-blur-slider');
             const valueEl = document.getElementById('glass-blur-value');
             if (slider) slider.value = 16;
             if (valueEl) valueEl.textContent = '16px';
             applyGlassBlur(16);
             // 重置覆盖层强度
             document.body.style.removeProperty('--overlay-alpha');
             document.body.style.removeProperty('--overlay-blur');
             persistStoredPreferences();
             if (!bridge()) return;
             try { apiCall('save_preference', { bgType: 'none', bgUrl: '', glassBlur: 16 }); } catch (e) {}
            syncBgModalState();
            const prefGrid = document.getElementById('bg-preview-grid');
            if (prefGrid) { prefGrid.querySelectorAll('.bg-preview-item').forEach(item => item.classList.toggle('active', item.dataset.bg === 'none')); }
            const customRow = document.getElementById('custom-bg-row');
            if (customRow) customRow.style.display = 'none';
            const strengthRow = document.getElementById('overlay-strength-row');
            if (strengthRow) strengthRow.style.display = 'none';
            const modalRow = document.getElementById('modal-custom-bg-row');
            if (modalRow) modalRow.style.display = 'none';
            hideBgModal();
        }

        async function refreshRuntime() {
            if (runtimeRefreshInFlight || document.hidden) return;
            if (!bridge()) {
                backendConnected = false;
                initialized = false;
                setBackendStatus('后端连接中断，正在重连...');
                if (runtimeTimer) { clearInterval(runtimeTimer); runtimeTimer = null; }
                scheduleInitRetry(300);
                return;
            }
            runtimeRefreshInFlight = true;
            const requestId = ++runtimeRequestSequence;
            try {
                const runtime = await apiCall('get_runtime_state');
                applyRuntimeState(runtime, requestId);
            } catch (error) {
                backendConnected = false;
                setBackendStatus('后端连接中断，正在重试...');
            } finally {
                runtimeRefreshInFlight = false;
            }
        }

        function scheduleInitRetry(delay = 300) {
            if (initialized || initRetryTimer) return;
            initRetryTimer = setTimeout(() => {
                initRetryTimer = null;
                init();
            }, delay);
        }

        function startRuntimePolling() {
            if (runtimeTimer) clearInterval(runtimeTimer);
            runtimeTimer = setInterval(refreshRuntime, 1500);
        }

        async function init() {
            if (initialized || initInFlight) return;
            if (!bridge()) {
                if (!backendHintShown && Date.now() - backendWaitStartedAt > 3000) {
                    backendHintShown = true;
                    setBackendStatus('请通过统一启动器打开此界面');
                    showToast('此页面需要 Python 后端，直接用浏览器打开只能预览界面。', 'info');
                }
                scheduleInitRetry(backendHintShown ? 1000 : 200);
                return;
            }
            initInFlight = true;
            const requestId = ++runtimeRequestSequence;
            try {
                const payload = await apiCall('get_initial_state');
                if (payload.settings) renderSettings(payload.settings);
                if (payload.runtime) applyRuntimeState(payload.runtime, requestId);
                initialized = true;
                backendConnected = true;
                backendInitErrorShown = false;
                showToast('HTML UI 已连接 Python 后端', 'success');
                startRuntimePolling();
            } catch (error) {
                initialized = false;
                setBackendStatus('后端初始化失败，正在重试...');
                if (!error?.silent && !backendInitErrorShown) {
                    backendInitErrorShown = true;
                    showToast(error.message || '初始化失败，正在重试', 'error');
                }
                scheduleInitRetry(error?.silent ? 200 : 1000);
            } finally {
                initInFlight = false;
            }
        }

        window.addEventListener('pywebviewready', init);
        document.addEventListener('DOMContentLoaded', () => { renderConsole(); loadPreferences(); init(); });
        document.addEventListener('visibilitychange', () => {
            if (!document.hidden) {
                if (initialized) refreshRuntime(); else init();
            }
        });
        window.addEventListener('beforeunload', () => {
            if (runtimeTimer) clearInterval(runtimeTimer);
            if (initRetryTimer) clearTimeout(initRetryTimer);
        });
        // 修复: pywebviewready 可能在 app.js 加载前已派发
        if (window._pvReady) {
            init();
        }

        /* ============================================================
           About Page Visual Effects
           ============================================================ */

        function initAboutPageEffects() {
            initAmbientLight();
            initStaggerReveal();
            initTiltEffect();
            initSpotlightEffect();
            initMagneticButtons();
            initTypewriterPaths();
        }

        function initAmbientLight() {
            const about = document.getElementById('view-about');
            if (!about) return;
            about.addEventListener('mousemove', (e) => {
                const rect = about.getBoundingClientRect();
                const mx = ((e.clientX - rect.left) / rect.width) * 100;
                const my = ((e.clientY - rect.top) / rect.height) * 100;
                about.style.setProperty('--mx', mx + '%');
                about.style.setProperty('--my', my + '%');
            });
        }

        function initStaggerReveal() {
            const items = document.querySelectorAll('#view-about .stagger-item');
            if (!items.length) return;
            const obs = new IntersectionObserver((entries) => {
                entries.forEach(entry => {
                    if (entry.isIntersecting) {
                        entry.target.classList.add('stagger-visible');
                        obs.unobserve(entry.target);
                    }
                });
            }, { threshold: 0.12 });
            items.forEach(el => obs.observe(el));

            // Contributor hover narrative — fade neighbors
            const cards = document.querySelectorAll('#view-about .contributor-card');
            cards.forEach(card => {
                card.addEventListener('mouseenter', () => {
                    cards.forEach(c => { if (c !== card) c.classList.add('fade-neighbors'); });
                });
                card.addEventListener('mouseleave', () => {
                    cards.forEach(c => c.classList.remove('fade-neighbors'));
                });
            });
        }

        function initTiltEffect() {
            const cards = document.querySelectorAll('#view-about .contributor-card[data-tilt]');
            cards.forEach(card => {
                card.addEventListener('mousemove', (e) => {
                    const rect = card.getBoundingClientRect();
                    const x = ((e.clientX - rect.left) / rect.width - 0.5) * 2;
                    const y = ((e.clientY - rect.top) / rect.height - 0.5) * -2;
                    card.style.transform = 'perspective(600px) rotateY(' + (x * 8) + 'deg) rotateX(' + (y * 8) + 'deg)';
                });
                card.addEventListener('mouseleave', () => {
                    card.style.transform = 'perspective(600px) rotateY(0deg) rotateX(0deg)';
                });
            });
        }

        function initSpotlightEffect() {
            const cards = document.querySelectorAll('#view-about .spotlight-card');
            cards.forEach(card => {
                card.addEventListener('mousemove', (e) => {
                    const rect = card.getBoundingClientRect();
                    const sx = ((e.clientX - rect.left) / rect.width) * 100;
                    const sy = ((e.clientY - rect.top) / rect.height) * 100;
                    card.style.setProperty('--sx', sx + '%');
                    card.style.setProperty('--sy', sy + '%');
                });
            });
        }

        function initMagneticButtons() {
            const btns = document.querySelectorAll('#view-about .btn-magnetic');
            btns.forEach(btn => {
                btn.addEventListener('mousemove', (e) => {
                    const rect = btn.getBoundingClientRect();
                    const ox = ((e.clientX - rect.left) / rect.width - 0.5) * 2;
                    const oy = ((e.clientY - rect.top) / rect.height - 0.5) * 2;
                    btn.style.setProperty('--mx-offset', (ox * 8) + 'px');
                    btn.style.setProperty('--my-offset', (oy * 8) + 'px');
                });
                btn.addEventListener('mouseleave', () => {
                    btn.style.setProperty('--mx-offset', '0px');
                    btn.style.setProperty('--my-offset', '0px');
                });
            });
        }

        function initTypewriterPaths() {
            const container = document.getElementById('about-paths');
            if (!container) return;
            // Only run once when real path data is available
            if (container._typewriterDone) return;
            const text = container.textContent;
            if (!text || text === '正在读取版本信息...' || text === '') return;
            container._typewriterDone = true;
            container.innerHTML = '';
            container.style.whiteSpace = 'normal';
            const lines = text.split('\n').filter(Boolean);
            lines.forEach((line, idx) => {
                const span = document.createElement('span');
                span.className = 'typewriter-line';
                span.textContent = line;
                span.style.animationDelay = (idx * 0.8) + 's';
                span.style.display = 'block';
                container.appendChild(span);
            });
            const lastLine = container.lastElementChild;
            if (lastLine) {
                lastLine.addEventListener('animationend', function handler() {
                    lastLine.classList.add('typewriter-done');
                    lastLine.removeEventListener('animationend', handler);
                });
            }
        }
