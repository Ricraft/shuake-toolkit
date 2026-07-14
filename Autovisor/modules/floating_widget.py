"""
浏览器浮动答题助手组件
通过 Playwright 注入到页面中，显示题目和答案
"""

# 浮动组件的 HTML/CSS/JS 代码
FLOATING_WIDGET_CODE = '''
(function() {
    // 防止重复注入
    if (document.getElementById('ai-answer-widget')) return;
    
    // 创建样式
    const style = document.createElement('style');
    style.textContent = `
        @import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;600&family=Noto+Sans+SC:wght@400;500;600&display=swap');
        
        #ai-answer-widget {
            position: fixed;
            top: 80px;
            left: 20px;
            width: 360px;
            max-height: calc(100vh - 100px);
            background: linear-gradient(135deg, rgba(15, 23, 42, 0.95), rgba(30, 41, 59, 0.95));
            border-radius: 16px;
            box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.5),
                        0 0 0 1px rgba(255, 255, 255, 0.1),
                        inset 0 1px 0 rgba(255, 255, 255, 0.1);
            z-index: 999999;
            font-family: 'Noto Sans SC', -apple-system, BlinkMacSystemFont, sans-serif;
            color: #e2e8f0;
            overflow: hidden;
            backdrop-filter: blur(20px);
            -webkit-backdrop-filter: blur(20px);
            transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
        }
        
        #ai-answer-widget.minimized {
            width: 56px;
            height: 56px;
            border-radius: 50%;
            cursor: pointer;
        }
        
        #ai-answer-widget.minimized .widget-content {
            display: none;
        }
        
        #ai-answer-widget.minimized .widget-header {
            padding: 0;
            justify-content: center;
            border-radius: 50%;
        }
        
        #ai-answer-widget.minimized .widget-header .header-title,
        #ai-answer-widget.minimized .widget-header .header-actions {
            display: none;
        }
        
        #ai-answer-widget.minimized .widget-header .toggle-btn {
            margin: 0;
        }
        
        .widget-header {
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 16px 20px;
            background: linear-gradient(135deg, rgba(99, 102, 241, 0.2), rgba(139, 92, 246, 0.2));
            border-bottom: 1px solid rgba(255, 255, 255, 0.1);
            cursor: move;
        }
        
        .header-title {
            display: flex;
            align-items: center;
            gap: 10px;
            font-weight: 600;
            font-size: 14px;
            letter-spacing: 0.5px;
        }
        
        .header-title .icon {
            width: 24px;
            height: 24px;
            background: linear-gradient(135deg, #6366f1, #8b5cf6);
            border-radius: 8px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 12px;
        }
        
        .header-actions {
            display: flex;
            gap: 8px;
        }
        
        .header-btn {
            width: 28px;
            height: 28px;
            border: none;
            background: rgba(255, 255, 255, 0.1);
            border-radius: 8px;
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: center;
            transition: all 0.2s;
            color: #94a3b8;
        }
        
        .header-btn:hover {
            background: rgba(255, 255, 255, 0.2);
            color: #fff;
        }
        
        .widget-content {
            max-height: calc(100vh - 180px);
            overflow-y: auto;
            padding: 16px;
        }
        
        .widget-content::-webkit-scrollbar {
            width: 6px;
        }
        
        .widget-content::-webkit-scrollbar-track {
            background: rgba(255, 255, 255, 0.05);
        }
        
        .widget-content::-webkit-scrollbar-thumb {
            background: rgba(255, 255, 255, 0.2);
            border-radius: 3px;
        }
        
        .question-section {
            margin-bottom: 16px;
        }
        
        .section-label {
            font-size: 11px;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 1px;
            color: #6366f1;
            margin-bottom: 8px;
            display: flex;
            align-items: center;
            gap: 6px;
        }
        
        .section-label::before {
            content: '';
            width: 3px;
            height: 12px;
            background: linear-gradient(180deg, #6366f1, #8b5cf6);
            border-radius: 2px;
        }
        
        .question-text {
            font-size: 14px;
            line-height: 1.7;
            color: #e2e8f0;
            padding: 12px 16px;
            background: rgba(255, 255, 255, 0.05);
            border-radius: 12px;
            border: 1px solid rgba(255, 255, 255, 0.08);
        }
        
        .question-type {
            display: inline-flex;
            align-items: center;
            gap: 4px;
            font-size: 11px;
            padding: 4px 10px;
            background: rgba(99, 102, 241, 0.2);
            border-radius: 20px;
            color: #a5b4fc;
            margin-bottom: 12px;
        }
        
        .answer-section {
            margin-bottom: 16px;
        }
        
        .answer-text {
            font-family: 'JetBrains Mono', monospace;
            font-size: 15px;
            font-weight: 500;
            color: #4ade80;
            padding: 14px 16px;
            background: linear-gradient(135deg, rgba(74, 222, 128, 0.1), rgba(34, 197, 94, 0.1));
            border-radius: 12px;
            border: 1px solid rgba(74, 222, 128, 0.3);
            position: relative;
            overflow: hidden;
        }
        
        .answer-text::before {
            content: '';
            position: absolute;
            left: 0;
            top: 0;
            bottom: 0;
            width: 3px;
            background: linear-gradient(180deg, #4ade80, #22c55e);
        }
        
        .answer-source {
            font-size: 10px;
            color: #64748b;
            margin-top: 8px;
            display: flex;
            align-items: center;
            gap: 4px;
        }
        
        .answer-source.ai {
            color: #f472b6;
        }
        
        .answer-source.db {
            color: #38bdf8;
        }
        
        .options-section {
            margin-bottom: 16px;
        }
        
        .option-item {
            display: flex;
            align-items: flex-start;
            gap: 10px;
            padding: 10px 14px;
            background: rgba(255, 255, 255, 0.03);
            border-radius: 10px;
            margin-bottom: 8px;
            border: 1px solid rgba(255, 255, 255, 0.05);
            transition: all 0.2s;
            cursor: pointer;
        }
        
        .option-item:hover {
            background: rgba(255, 255, 255, 0.08);
            border-color: rgba(99, 102, 241, 0.3);
        }
        
        .option-item.selected {
            background: rgba(99, 102, 241, 0.15);
            border-color: rgba(99, 102, 241, 0.5);
        }
        
        .option-item.correct {
            background: rgba(74, 222, 128, 0.15);
            border-color: rgba(74, 222, 128, 0.5);
        }
        
        .option-letter {
            width: 24px;
            height: 24px;
            background: rgba(255, 255, 255, 0.1);
            border-radius: 6px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 12px;
            font-weight: 600;
            flex-shrink: 0;
        }
        
        .option-item.selected .option-letter {
            background: linear-gradient(135deg, #6366f1, #8b5cf6);
        }
        
        .option-item.correct .option-letter {
            background: linear-gradient(135deg, #4ade80, #22c55e);
        }
        
        .option-text {
            font-size: 13px;
            line-height: 1.5;
            color: #cbd5e1;
        }
        
        .status-bar {
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 12px 16px;
            background: rgba(0, 0, 0, 0.2);
            border-top: 1px solid rgba(255, 255, 255, 0.05);
        }
        
        .status-text {
            font-size: 12px;
            color: #64748b;
            display: flex;
            align-items: center;
            gap: 6px;
        }
        
        .status-dot {
            width: 8px;
            height: 8px;
            border-radius: 50%;
            background: #4ade80;
            animation: pulse 2s infinite;
        }
        
        .hint-text {
            font-size: 11px;
            color: #94a3b8;
            text-align: center;
            padding: 8px;
            background: rgba(99, 102, 241, 0.1);
            border-radius: 8px;
            margin-top: 12px;
        }
        
        .nav-buttons {
            display: flex;
            gap: 10px;
            margin-top: 16px;
            padding-top: 16px;
            border-top: 1px solid rgba(255, 255, 255, 0.1);
        }
        
        .nav-btn {
            flex: 1;
            padding: 10px 16px;
            border: none;
            border-radius: 10px;
            font-size: 13px;
            font-weight: 500;
            cursor: pointer;
            transition: all 0.2s;
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 6px;
        }
        
        .nav-btn.prev {
            background: rgba(255, 255, 255, 0.1);
            color: #94a3b8;
        }
        
        .nav-btn.prev:hover {
            background: rgba(255, 255, 255, 0.2);
            color: #fff;
        }
        
        .nav-btn.prev:disabled {
            opacity: 0.3;
            cursor: not-allowed;
        }
        
        .nav-btn.next {
            background: linear-gradient(135deg, #6366f1, #8b5cf6);
            color: #fff;
        }
        
        .nav-btn.next:hover {
            transform: translateY(-2px);
            box-shadow: 0 10px 20px -10px rgba(99, 102, 241, 0.5);
        }
        
        .nav-btn.next.last {
            background: linear-gradient(135deg, #4ade80, #22c55e);
        }
        
        .progress-bar {
            height: 3px;
            background: rgba(255, 255, 255, 0.1);
            border-radius: 2px;
            overflow: hidden;
            margin-bottom: 16px;
        }
        
        .progress-fill {
            height: 100%;
            background: linear-gradient(90deg, #6366f1, #8b5cf6);
            border-radius: 2px;
            transition: width 0.3s;
        }
        
        .empty-state {
            text-align: center;
            padding: 40px 20px;
            color: #64748b;
        }
        
        .empty-state .icon {
            font-size: 48px;
            margin-bottom: 16px;
            opacity: 0.5;
        }
        
        .empty-state .text {
            font-size: 14px;
        }
        
        .dragging {
            cursor: grabbing !important;
            user-select: none;
        }
        
        /* 动画 */
        @keyframes slideIn {
            from {
                opacity: 0;
                transform: translateX(20px);
            }
            to {
                opacity: 1;
                transform: translateX(0);
            }
        }
        
        #ai-answer-widget {
            animation: slideIn 0.3s ease-out;
        }
    `;
    document.head.appendChild(style);
    
    // 创建组件
    const widget = document.createElement('div');
    widget.id = 'ai-answer-widget';
    widget.innerHTML = `
        <div class="widget-header">
            <div class="header-title">
                <div class="icon">AI</div>
                <span>答题助手</span>
            </div>
            <div class="header-actions">
                <button class="header-btn toggle-btn" title="最小化">−</button>
                <button class="header-btn close-btn" title="关闭">×</button>
            </div>
        </div>
        <div class="widget-content">
            <div class="empty-state">
                <div class="icon">📝</div>
                <div class="text">等待题目数据...</div>
            </div>
        </div>
        <div class="status-bar" style="display: none;">
            <div class="status-text">
                <span class="status-dot"></span>
                <span class="status-message">请在页面上选择答案</span>
            </div>
        </div>
    `;
    document.body.appendChild(widget);
    
    // 状态管理
    const state = {
        minimized: false,
        currentQuestion: null,
        questionIndex: 0,
        totalQuestions: 0
    };
    
    // 元素引用
    const elements = {
        widget,
        header: widget.querySelector('.widget-header'),
        content: widget.querySelector('.widget-content'),
        statusBar: widget.querySelector('.status-bar'),
        statusMessage: widget.querySelector('.status-message'),
        toggleBtn: widget.querySelector('.toggle-btn'),
        closeBtn: widget.querySelector('.close-btn')
    };
    
    // 拖拽功能
    let isDragging = false;
    let dragOffset = { x: 0, y: 0 };
    
    elements.header.addEventListener('mousedown', (e) => {
        if (e.target.closest('.header-btn')) return;
        isDragging = true;
        const rect = widget.getBoundingClientRect();
        dragOffset.x = e.clientX - rect.left;
        dragOffset.y = e.clientY - rect.top;
        widget.classList.add('dragging');
    });
    
    document.addEventListener('mousemove', (e) => {
        if (!isDragging) return;
        const x = Math.max(0, Math.min(window.innerWidth - widget.offsetWidth, e.clientX - dragOffset.x));
        const y = Math.max(0, Math.min(window.innerHeight - widget.offsetHeight, e.clientY - dragOffset.y));
        widget.style.left = x + 'px';
        widget.style.top = y + 'px';
        widget.style.right = 'auto';
    });
    
    document.addEventListener('mouseup', () => {
        isDragging = false;
        widget.classList.remove('dragging');
    });
    
    // 最小化/展开
    elements.toggleBtn.addEventListener('click', () => {
        state.minimized = !state.minimized;
        widget.classList.toggle('minimized', state.minimized);
        elements.toggleBtn.textContent = state.minimized ? '+' : '−';
    });
    
    // 关闭
    elements.closeBtn.addEventListener('click', () => {
        widget.style.display = 'none';
    });
    
    // 更新题目数据
    function updateQuestion(data) {
        state.currentQuestion = data;
        
        const { question, answer, options, type, index, total, isAI } = data;
        state.questionIndex = index || 0;
        state.totalQuestions = total || 1;
        
        elements.statusBar.style.display = 'flex';
        
        // 构建内容
        let html = '';
        
        // 进度条
        if (state.totalQuestions > 1) {
            const progress = ((state.questionIndex + 1) / state.totalQuestions) * 100;
            html += `
                <div class="progress-bar">
                    <div class="progress-fill" style="width: ${progress}%"></div>
                </div>
            `;
        }
        
        // 题目类型
        if (type) {
            html += `<div class="question-type">${getTypeIcon(type)} ${type}</div>`;
        }
        
        // 题目
        if (question) {
            html += `
                <div class="question-section">
                    <div class="section-label">题目</div>
                    <div class="question-text">${escapeHtml(question)}</div>
                </div>
            `;
        }
        
        // 选项（仅显示，不可点击）
        if (options && options.length > 0) {
            html += `
                <div class="options-section">
                    <div class="section-label">选项</div>
                    ${options.map((opt, i) => `
                        <div class="option-item">
                            <div class="option-letter">${String.fromCharCode(65 + i)}</div>
                            <div class="option-text">${escapeHtml(opt)}</div>
                        </div>
                    `).join('')}
                </div>
            `;
        }
        
        // 答案
        if (answer) {
            html += `
                <div class="answer-section">
                    <div class="section-label">参考答案</div>
                    <div class="answer-text">${escapeHtml(answer)}</div>
                    <div class="answer-source ${isAI ? 'ai' : 'db'}">
                        ${isAI ? '🤖 AI 生成' : '📚 题库匹配'}
                    </div>
                </div>
            `;
        }
        
        // 提示
        const isMultiple = type && type.includes('多选');
        if (isMultiple) {
            html += `<div class="hint-text">💡 多选题：请选择完所有选项后点击"下一题"</div>`;
        } else {
            html += `<div class="hint-text">💡 单选题：选择后自动下一题</div>`;
        }
        
        // 导航按钮
        html += `
            <div class="nav-buttons">
                <button class="nav-btn prev" ${state.questionIndex === 0 ? 'disabled' : ''}>
                    ◀ 上一题
                </button>
                <button class="nav-btn next ${state.questionIndex >= state.totalQuestions - 1 ? 'last' : ''}">
                    ${state.questionIndex >= state.totalQuestions - 1 ? '提交试卷' : '下一题 ▶'}
                </button>
            </div>
        `;
        
        elements.content.innerHTML = html;
        elements.statusMessage.textContent = `第 ${state.questionIndex + 1}/${state.totalQuestions} 题`;
        
        // 绑定导航按钮事件
        const prevBtn = elements.content.querySelector('.nav-btn.prev');
        const nextBtn = elements.content.querySelector('.nav-btn.next');
        
        if (prevBtn) {
            prevBtn.addEventListener('click', () => {
                if (state.questionIndex > 0) {
                    window._widgetPrevClicked = true;
                    console.log('点击上一题');
                }
            });
        }
        
        if (nextBtn) {
            nextBtn.addEventListener('click', () => {
                window._widgetNextClicked = true;
                console.log('点击下一题');
            });
        }
    }
    
    // 工具函数
    function escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }
    
    function getTypeIcon(type) {
        const icons = {
            '单选题': '⭕',
            '多选题': '☑️',
            '判断题': '✓✗',
            '填空题': '✏️',
            '简答题': '📝'
        };
        return icons[type] || '❓';
    }
    
    // 暴露 API
    window.AIAnswerWidget = {
        updateQuestion,
        show: () => { widget.style.display = 'block'; },
        hide: () => { widget.style.display = 'none'; },
        toggle: () => { widget.style.display = widget.style.display === 'none' ? 'block' : 'none'; },
        getState: () => ({ ...state }),
        onSubmitted: (callback) => {
            widget.addEventListener('answerSubmitted', callback);
        }
    };
    
    console.log('AI Answer Widget initialized');
})();
'''

# 导出函数
def get_widget_code():
    """获取浮动组件的注入代码"""
    return FLOATING_WIDGET_CODE


async def inject_widget(page):
    """将浮动组件注入到页面"""
    await page.evaluate(FLOATING_WIDGET_CODE)


async def update_widget_question(page, question_data):
    """更新组件显示的题目数据
    
    question_data: {
        question: str,      # 题目文本
        answer: str,        # 答案文本
        options: list,      # 选项列表
        type: str,          # 题目类型
        index: int,         # 当前题目索引
        total: int,         # 总题目数
        isAI: bool          # 是否为AI生成
    }
    """
    await page.evaluate(f'''
        if (window.AIAnswerWidget) {{
            AIAnswerWidget.updateQuestion({question_data});
        }}
    ''')
