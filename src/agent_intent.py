"""Conservative Chinese command routing; unknown wording stays with the model."""
from __future__ import annotations
import re

NEGATED = re.compile(r'(?:不要|别|先别|暂时不|先不|不用|无需|不需要|不想|不)(?:再|去|自动)?(?:启动|停止|停|打开|关闭|运行|修改|更改|设置|关机)')
QUESTION = re.compile(r'怎么|如何|为什么|是什么|如果|能不能|能否|是否|可以吗|能.*吗|可以.*吗|会怎样|会不会|教程|介绍|说明|了解|[?？]')


def target(text):
    found = []
    for core, pattern in [
        ('zhs', r'zhs|新版智慧树|智慧树新版'),
        ('autovisor', r'autovisor|旧版智慧树|老版智慧树|智慧树专区'),
        ('yatori', r'yatori|雅托里|通用平台|通用核心|学习通|超星|英华|仓辉|学习公社|智慧职教|青书|welearn|海旗'),
    ]:
        if re.search(pattern, text, re.I): found.append(core)
    return found


def recognize(text):
    """Return a local action/plan, clarification, read query, or None."""
    text = str(text or '').strip()
    if NEGATED.search(text):
        return {'reply': '我不会执行被否定的操作。若要改做其他操作，请明确告诉我目标和动作。'}
    if re.search(r'还是|或者|或是|不包括|除了|不要.{0,12}(?:zhs|yatori|autovisor|核心)', text, re.I):
        return {'reply': '操作目标包含选择或排除条件，请明确要操作哪一个核心，我暂未执行。'}
    if read_only_question(text):
        return None
    if '智慧树' in text and not re.search(r'zhs|autovisor|新版|旧版|老版|专区', text, re.I) and re.search('启动|停止|关闭|运行|修改|设置|倍速', text):
        return {'reply': '请说明是 ZHS 新版核心还是 Autovisor 旧版核心，我暂未执行。'}
    if re.fullmatch(r'(?:请|帮我)?(?:查看|查询|汇报|看看)?(?:当前|现在)?(?:运行)?状态[？?。！!]*', text):
        return {'read': 'get_status'}
    pieces = [p.strip(' ，,。.!！') for p in re.split(r'然后|接着|(?:再|并且|同时|和|并)(?=启动|停止|打开|关闭|把|将|切到|切换|修改|设置)|[，,；;](?=(?:再|先)?(?:启动|停止|打开|关闭|把|将|开启|切换|切到|修改|设置))', text) if p.strip()]
    if len(pieces) > 1:
        steps = []
        for piece in pieces:
            item = recognize(re.sub(r'^(?:先|再)', '', piece))
            if not item: return None  # Let tool-calling handle complex settings.
            if not item.get('steps'): return item
            steps.extend(item['steps'])
        return {'steps': steps} if len(steps) <= 6 else {'reply': '一次最多确认 6 步操作，请拆分请求。'}
    clean = re.sub(r'^(?:(?:请|帮我|麻烦你|麻烦|给我|替我|现在|立刻|马上|先)\s*)+', '', text)
    clean = re.sub(r'(?:一下|吧|。|！|!)$', '', clean).strip()
    if clean in ('一键刷课', '一键启动', '全部启动', '全都启动', '开始刷课'):
        return {'steps': [{'name': 'control_runtime', 'args': {'action': 'start_all'}}]}
    if clean in ('一键停止', '全部停止', '停止刷课', '停止听课'):
        return {'steps': [{'name': 'control_runtime', 'args': {'action': 'stop_all'}}]}
    # Settings take precedence over verbs such as “关闭自动关机”.
    for noun, key in [('自动关机', 'autoShutdown'), ('刷完关闭程序', 'closeLauncherOnComplete'),
                      ('刷完关掉程序', 'closeLauncherOnComplete'), ('提示音', 'soundEnabled'),
                      ('声音', 'soundEnabled'), ('音效', 'soundEnabled'),
                      ('开机自启', 'autoStart'), ('开机启动', 'autoStart'),
                      ('启动后自动运行', 'autoRun'), ('启动后最小化', 'startMinimized'),
                      ('完成通知', 'notifyOnComplete'), ('失败通知', 'notifyOnError'),
                      ('记住窗口位置', 'rememberGeometry'), ('最小化到托盘', 'minimizeToTray'),
                      ('窗口置顶', 'alwaysOnTop'), ('自动清理日志', 'autoCleanLogs')]:
        verbs = r'(?:开启|启用|打开|关闭|禁用|关掉)'
        if re.fullmatch(r'(?:把)?(?:' + re.escape(noun) + ')' + verbs + '|' + verbs + re.escape(noun), clean):
            on = bool(re.search(r'开启|启用|打开', clean))
            off = bool(re.search(r'关闭|禁用|关掉', clean))
            if on != off:
                return {'steps': [{'name': 'set_preference', 'args': {'key': key, 'value': on}}]}
    if re.fullmatch(r'(?:把)?(?:主题)?(?:切换到|切到|换成|改成|设为|设置为)(?:深色|暗色|浅色|亮色)(?:主题|模式)?', clean):
        value = 'dark' if re.search('深色|暗色', clean) else 'light'
        return {'steps': [{'name': 'update_settings', 'args': {'scope': 'launcher', 'changes': [{'path': 'theme', 'value': value}]}}]}
    match = re.fullmatch(r'(?:把)?(?:yatori|通用核心|通用平台)(?:的)?(?:最长运行时间|运行时限|运行时间上限)(?:设为|改成|设置为|改为)(\d+(?:\.\d+)?)分钟', clean, re.I)
    if match:
        return {'steps': [{'name': 'update_settings', 'args': {'scope': 'launcher', 'changes': [{'path': 'yatoriMaxRuntimeMinutes', 'value': float(match[1])}]}}]}
    action = re.match(r'^(启动|运行|打开|开启|停止|暂停|关闭|关掉|结束)\s*(.+)$', clean)
    if action:
        verb, subject = action.groups()
        cores = target(subject)
        if re.search(r'设置|配置|教程|页面|按钮|日志|更新|课程|毛概|高数', subject): return None
        if '智慧树' in subject and not cores:
            return {'reply': '你要操作 ZHS 新版核心，还是 Autovisor 旧版核心？请明确一下，我暂未执行。'}
        operation = 'start' if verb in ('启动', '运行', '打开', '开启') else 'stop'
        if subject in ('题库', '题库服务器', '本地题库'):
            return {'steps': [{'name': 'control_runtime', 'args': {'action': operation, 'core': 'questionbank'}}]}
        if subject in ('全部', '所有核心', '全部核心', '所有', '全部刷课核心'):
            cores = ['yatori', 'autovisor', 'zhs'] if operation == 'start' else []
            if operation == 'stop': return {'steps': [{'name': 'control_runtime', 'args': {'action': 'stop_all'}}]}
            return {'steps': [{'name': 'control_runtime', 'args': {'action': 'start_all'}}]}
        if cores:
            residue = re.sub(r'zhs|新版智慧树|智慧树新版|autovisor|旧版智慧树|老版智慧树|智慧树专区|yatori|雅托里|通用平台|通用核心|学习通|超星|英华(?:学堂)?|仓辉(?:实训)?|学习公社|智慧职教|青书(?:学堂)?|welearn|海旗(?:科技)?', '', subject, flags=re.I)
            if re.sub(r'核心|刷课|和|与|、|\s', '', residue): return None
            return {'steps': [{'name': 'control_runtime', 'args': {'action': operation, 'core': core}} for core in cores]}
    return None


def read_only_question(text):
    if NEGATED.search(text): return True
    if re.search(r'怎么|如何|为什么|是什么|如果|教程|介绍|说明|了解', text): return True
    # “可以帮我刷一下吗” is a polite request, unlike “怎么刷/能刷吗”.
    if re.search(r'请|帮我|替我|麻烦', text): return False
    return bool(QUESTION.search(text))
