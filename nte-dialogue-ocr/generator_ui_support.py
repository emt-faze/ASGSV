"""Consistent light colors and contextual help for the generator dialogs."""
from html import escape

from PySide6 import QtCore, QtGui, QtWidgets


def light_palette():
    # Fusion still inherits the OS palette. Define every surface/foreground pair,
    # including inactive and disabled controls, so Windows dark mode cannot mix in.
    palette = QtGui.QPalette()
    colors = {
        'Window': '#0f1218', 'WindowText': '#f1f5f9',
        'Base': '#161a24', 'AlternateBase': '#1b202d', 'Text': '#f1f5f9',
        'Button': '#1e2333', 'ButtonText': '#f1f5f9',
        'Highlight': '#4f46e5', 'HighlightedText': '#ffffff',
        'ToolTipBase': '#1e2333', 'ToolTipText': '#f1f5f9',
        'PlaceholderText': '#64748b', 'Link': '#38bdf8', 'LinkVisited': '#a78bfa',
        'Light': '#262d42', 'Midlight': '#1e2538', 'Mid': '#2a3248',
        'Dark': '#0a0d14', 'Shadow': '#05070a', 'BrightText': '#ffffff',
    }
    if hasattr(QtGui.QPalette.ColorRole, 'Accent'):
        colors['Accent'] = '#6366f1'
    for group in (QtGui.QPalette.Active, QtGui.QPalette.Inactive, QtGui.QPalette.Disabled):
        for role, color in colors.items():
            palette.setColor(group, getattr(QtGui.QPalette.ColorRole, role), QtGui.QColor(color))
    for role in ('WindowText', 'Text', 'ButtonText', 'PlaceholderText'):
        palette.setColor(QtGui.QPalette.Disabled, getattr(QtGui.QPalette.ColorRole, role),
                         QtGui.QColor('#cbd5e1'))
    for role in ('Base', 'Button'):
        palette.setColor(QtGui.QPalette.Disabled, getattr(QtGui.QPalette.ColorRole, role),
                         QtGui.QColor('#141720'))
    palette.setColor(QtGui.QPalette.Disabled, QtGui.QPalette.Highlight, QtGui.QColor('#252d40'))
    palette.setColor(QtGui.QPalette.Disabled, QtGui.QPalette.HighlightedText, QtGui.QColor('#cbd5e1'))
    return palette


DIALOG_STYLE = """
    QDialog, QMainWindow {
        background: #0f1218;
    }
    QWidget[dialogueLightTheme="true"], QScrollArea {
        background: #0f1218;
    }
    QWidget {
        color: #f1f5f9;
        font-family: "Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI", sans-serif;
    }
    QWidget:disabled {
        color: #94a3b8;
    }
    QPushButton {
        color: #f1f5f9;
        background: #1a1e2b;
        border: 1px solid #283044;
        border-radius: 6px;
        padding: 6px 14px;
        font-weight: 500;
        min-height: 22px;
    }
    QPushButton:hover {
        background: #23293b;
        border-color: #6366f1;
        color: #ffffff;
    }
    QPushButton:pressed {
        background: #141722;
        border-color: #4f46e5;
        color: #e2e8f0;
    }
    QPushButton:focus {
        border-color: #6366f1;
        outline: none;
    }
    QPushButton:disabled {
        color: #94a3b8;
        background: #12151e;
        border-color: #1e2433;
    }
    QPushButton#primary {
        background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #4f46e5, stop:0.5 #6366f1, stop:1 #8b5cf6);
        color: #ffffff;
        font-weight: 700;
        border: 1px solid #6366f1;
        border-radius: 6px;
        padding: 7px 18px;
    }
    QPushButton#primary:hover {
        background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #4338ca, stop:0.5 #4f46e5, stop:1 #7c3aed);
        border-color: #818cf8;
    }
    QPushButton#primary:pressed {
        background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #3730a3, stop:0.5 #4338ca, stop:1 #6d28d9);
        border-color: #4f46e5;
    }
    QPushButton#primary:disabled {
        background: #232538;
        color: #94a3b8;
        border-color: #2e3348;
    }
    QLineEdit, QPlainTextEdit, QAbstractSpinBox {
        padding: 6px 10px;
        color: #f1f5f9;
        background: #141722;
        border: 1px solid #283044;
        border-radius: 6px;
        selection-color: #ffffff;
        selection-background-color: #4f46e5;
    }
    QLineEdit:focus, QPlainTextEdit:focus, QAbstractSpinBox:focus {
        border: 1.5px solid #6366f1;
        background: #161a26;
    }
    QLineEdit:disabled, QPlainTextEdit:disabled, QAbstractSpinBox:disabled {
        color: #94a3b8;
        background: #0d0f15;
        border-color: #1a1e2a;
    }
    QComboBox {
        padding: 5px 28px 5px 10px;
        color: #f1f5f9;
        background: #141722;
        border: 1px solid #283044;
        border-radius: 6px;
        min-height: 22px;
        selection-color: #ffffff;
        selection-background-color: #4f46e5;
    }
    QComboBox:hover {
        border-color: #6366f1;
    }
    QComboBox:focus {
        border: 1.5px solid #6366f1;
    }
    QComboBox:disabled {
        color: #94a3b8;
        background: #0d0f15;
        border-color: #1a1e2a;
    }
    QComboBox::drop-down {
        subcontrol-origin: padding;
        subcontrol-position: top right;
        width: 24px;
        border-left: none;
    }
    QComboBox::down-arrow {
        image: url(:/qt-project.org/styles/commonstyle/images/arrow-down-32.png);
        width: 12px;
        height: 12px;
    }
    QComboBox QAbstractItemView {
        background: #141722;
        border: 1px solid #283044;
        border-radius: 6px;
        padding: 4px;
        color: #f1f5f9;
        selection-background-color: #23293d;
        selection-color: #a78bfa;
        outline: none;
    }
    QGroupBox {
        background: #141722;
        border: 1px solid #252b3d;
        border-radius: 10px;
        margin-top: 14px;
        padding: 10px;
        font-weight: 600;
        color: #f1f5f9;
    }
    QGroupBox::title {
        subcontrol-origin: margin;
        subcontrol-position: top left;
        left: 14px;
        top: 2px;
        padding: 0 8px;
        background: #141722;
        color: #f1f5f9;
        font-size: 13px;
        font-weight: 700;
        border-radius: 4px;
    }
    QTableWidget, QTableView, QListWidget {
        background: #12151e;
        alternate-background-color: #161a25;
        gridline-color: #1f2535;
        border: 1px solid #252b3d;
        border-radius: 8px;
        color: #f1f5f9;
        selection-color: #ffffff;
        selection-background-color: #4f46e5;
        outline: none;
    }
    QHeaderView::section {
        background: #181c28;
        color: #94a3b8;
        font-weight: 600;
        font-size: 12px;
        padding: 8px 6px;
        border: none;
        border-bottom: 2px solid #252b3d;
        border-right: 1px solid #1f2535;
    }
    QTableCornerButton::section {
        background: #181c28;
        border: none;
        border-bottom: 2px solid #252b3d;
    }
    QTabWidget::pane {
        border: 1px solid #252b3d;
        border-radius: 8px;
        background: #12151e;
        top: -1px;
    }
    QTabBar::tab {
        background: #161a24;
        color: #94a3b8;
        border: 1px solid #252b3d;
        border-bottom: none;
        border-top-left-radius: 6px;
        border-top-right-radius: 6px;
        padding: 8px 18px;
        font-weight: 600;
        font-size: 12px;
        margin-right: 4px;
    }
    QTabBar::tab:hover {
        background: #1f2536;
        color: #a78bfa;
    }
    QTabBar::tab:selected {
        background: #12151e;
        color: #6366f1;
        border-color: #252b3d;
        border-top: 3px solid #6366f1;
    }
    QProgressBar {
        background: #1e2434;
        border: none;
        border-radius: 4px;
        text-align: center;
        font-size: 11px;
        font-weight: 600;
        color: #cbd5e1;
        max-height: 12px;
    }
    QProgressBar::chunk {
        background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #06b6d4, stop:0.5 #6366f1, stop:1 #8b5cf6);
        border-radius: 4px;
    }
    QScrollBar:vertical {
        background: transparent;
        width: 8px;
        margin: 0;
    }
    QScrollBar::handle:vertical {
        background: #2d354b;
        min-height: 24px;
        border-radius: 4px;
    }
    QScrollBar::handle:vertical:hover {
        background: #475569;
    }
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
        height: 0;
    }
    QScrollBar:horizontal {
        background: transparent;
        height: 8px;
        margin: 0;
    }
    QScrollBar::handle:horizontal {
        background: #2d354b;
        min-width: 24px;
        border-radius: 4px;
    }
    QScrollBar::handle:horizontal:hover {
        background: #475569;
    }
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
        width: 0;
    }
    QToolTip {
        background: #1e2333;
        color: #f1f5f9;
        border: 1px solid #3b4562;
        padding: 7px;
        border-radius: 4px;
    }
    QToolButton {
        background: #1a1e2b;
        color: #f1f5f9;
        border: 1px solid #283044;
        border-radius: 6px;
        padding: 6px 10px;
    }
    QToolButton:hover, QToolButton:checked {
        background: #23293b;
        border-color: #6366f1;
        color: #a78bfa;
    }
    QSplitter::handle {
        background: #1e2434;
        border-radius: 3px;
    }
    QSplitter::handle:hover {
        background: #4f46e5;
    }
        background: #b8d8d2;
    }
"""


def configure_application(application):
    application.setStyle('Fusion')
    hints = application.styleHints()
    # Older PySide6 versions do not expose this preference. The explicit palette
    # and dialog styles remain sufficient for readable client-area controls.
    if hasattr(hints, 'setColorScheme'):
        hints.setColorScheme(QtCore.Qt.ColorScheme.Light)
    application.setPalette(light_palette())
    QtWidgets.QToolTip.setPalette(light_palette())


def fit_window_to_screen(window, width, height):
    """Leave room for the native frame and taskbar at the current screen scale."""
    available = window.screen().availableGeometry()
    window.resize(min(width, max(320, available.width() - 32)),
                  min(height, max(240, available.height() - 48)))


def set_help(widget, text, title=None):
    title = title if title is not None else widget.text().replace('&', '')
    body = escape(text).replace('\n', '<br/>')
    widget.setToolTip(f'<qt><b>{escape(title)}</b><br/>{body}</qt>')
    widget.setToolTipDuration(25000)
    widget.setAccessibleDescription(text)


def apply_button_help(dialog, group):
    descriptions = BUTTON_HELP[group]
    for button in dialog.findChildren(QtWidgets.QPushButton):
        if button.text() in descriptions:
            set_help(button, descriptions[button.text()])


def configure_dialog(dialog, group):
    # Set the palette locally as well: the generator can be opened inside the
    # OCR application, and several shared dialogs can be opened independently.
    dialog.setPalette(light_palette())
    dialog.setAttribute(QtCore.Qt.WA_WindowPropagation, True)
    dialog.setAttribute(QtCore.Qt.WA_AlwaysShowToolTips, True)
    ancestor = dialog.parentWidget()
    while ancestor is not None and not ancestor.property('dialogueLightTheme'):
        ancestor = ancestor.parentWidget()
    # Reapplying the same stylesheet on a child dialog makes Qt resolve native
    # button/checkbox surfaces against the application palette on some builds.
    # Inherit the existing sheet instead; independent dialogs get their own.
    if ancestor is None and not dialog.property('dialogueLightTheme'):
        dialog.setStyleSheet(DIALOG_STYLE)
    dialog.setProperty('dialogueLightTheme', True)
    # Stylesheet-managed children can otherwise resolve unstyled roles against
    # QApplication again when its palette changes, bypassing their parent.
    for widget in dialog.findChildren(QtWidgets.QWidget):
        widget.setPalette(light_palette())
    QtWidgets.QToolTip.setPalette(light_palette())
    apply_button_help(dialog, group)


BUTTON_HELP = {
    'organizer': {
        '读取本机历史日志': '读取设置中的异环日志文件夹，解析历史台词编号和原声记录。完成后可按历史日志筛选。',
        '选择日志文件…': '选择一个或多个异环日志文件，导入其中能够定位的台词记录。',
        '查看未定位编号': '查看日志中尚未匹配到原文库的台词编号，以及可用的定位信息。',
        '更新游戏原文库': '从已安装的异环资源中更新中文原文库，完成后刷新当前查询。',
        '数据设置': '选择原文库与日志文件夹，设置玩家称呼和主角称谓分支。',
        '使用帮助': '查看从检索原文、整理台词到建立配音项目的操作说明。',
        '清空筛选': '恢复关键词、角色、资源分组、来源和原声状态的默认筛选条件。',
        '上一页': '显示当前筛选或整理清单的上一页，切换前保存当前编辑。',
        '下一页': '显示当前筛选或整理清单的下一页，切换前保存当前编辑。',
        '加入选中条目': '把原文表格中高亮选中的条目加入整理清单。可按住 Ctrl 或 Shift 选择多行。',
        '勾选本页': '勾选整理清单当前页的所有条目，供导出和建立配音项目使用。',
        '取消本页勾选': '取消整理清单当前页条目的勾选，其他页面的勾选状态单独保留。',
        '上移': '将当前高亮的一条台词在整理清单中向前移动一个位置。',
        '下移': '将当前高亮的一条台词在整理清单中向后移动一个位置。',
        '移出选中条目': '将高亮选中的条目移出整理清单。可重新从原文库加入。',
        '导出 CSV…': '按整理清单顺序导出全部已勾选条目，可使用表格软件打开。范围包含其他页面的勾选项。',
        '导出 TXT…': '按整理清单顺序导出全部已勾选条目的角色和台词。范围包含其他页面的勾选项。',
        '导出 JSON…': '导出全部已勾选条目及其来源信息，便于继续处理和核对。',
        '建立配音项目…': '将整理清单中全部已勾选条目建立为异环配音项目，随后可在配音工具中核对和生成。',
        '在配音工具中打开': '打开最近从整理清单建立的配音项目。请先建立项目，再进入配音工具。',
        '取消当前操作': '请求结束当前导入、导出或原文库更新，等待界面显示操作结果。',
    },
    'organizer_settings': {
        '选择原文库…': '选择异环适配版的 SQLite 中文原文库。',
        '选择文件夹…': '选择包含异环历史日志的文件夹，供读取本机历史日志使用。',
        '保存设置': '保存数据路径、玩家称呼和称谓分支，并重新载入查询结果。',
        '取消': '关闭设置窗口。',
        '确定建立': '按当前名称和生成模式建立包含已勾选台词的配音项目。',
        '关闭': '关闭当前信息窗口。',
    },
    'nte_live': {
        '开始实时配音': '连接所选游戏窗口，通过日志和字幕定位原文。优先播放已有配音，缺少音频时按当前声音设置生成并播放。',
        '停止实时配音': '结束当前实时配音和画面采集，停止当前播放，等待任务结束后可再次开始。',
        '暂停 F8': '暂停实时配音播放，可按 F8 或点击同一按钮继续。',
        '重播 F9': '重新播放当前台词。连接就绪后可点击此按钮或按 F9。',
        '跳过 F10': '停止当前台词的播放，等待下一条台词。连接就绪后也可按 F10。',
        '系统中文声设置': '为角色选择系统中文声音并调整试听设置。',
        '更新游戏原文库': '读取已安装的异环资源并更新台词编号与中文原文索引。',
        '关闭': '结束当前窗口的任务并关闭实时配音窗口。',
    },
    'calibration': {
        '一键打开游戏并开始': '通过已选择的启动器连接异环，加载字幕区域并开始识别。',
        '选择启动器': '选择本机的异环官方启动器，供一键连接游戏使用。',
        '参考音频与音量': '打开角色参考音频和音量设置，保存后重新开始识别以应用修改。',
        '暂停配音': '暂停当前识别流程的语音播放，点击同一按钮可以继续。',
        '跳过当前配音': '结束正在播放的当前句，继续等待后续台词。',
        '重播当前台词': '再次播放当前已识别的台词。',
        '系统声线与试听': '选择系统中文声音、角色绑定和试听效果。',
        '刷新游戏窗口': '重新查找当前已打开的异环游戏窗口。',
        '框选正文': '从游戏画面中选择台词正文区域，保存后用于识别文字。',
        '框选角色名（可选）': '从游戏画面中选择角色姓名区域，帮助匹配角色声线。',
        '不识别角色名': '清除角色名识别区域，继续使用台词正文进行识别。',
        '启动识别': '开始读取所选游戏窗口的字幕，按当前配音设置处理识别结果。',
        '停止识别和配音': '请求结束画面识别和语音播放，等待后台任务完成清理。',
        '打开识别记录': '打开本机已保存的台词识别记录文件夹。',
        '真实台词验收': '打开识别结果核对窗口，查看原图与识别文字。',
        '台词生成工具': '打开项目台词编辑、核对、批量生成和试听窗口。',
    },
    'generator': {
        '设置': '打开当前游戏的连接与配音设置。',
        '打开项目': '选择已有项目文件并打开对应游戏的台词编辑窗口。',
        '继续上次项目': '读取当前游戏最近使用的项目，继续核对、生成或试听。',
        '开始配音': '打开当前项目的配音播放窗口，播放已生成的台词。',
        '识别游戏台词…': '打开游戏字幕采集窗口，把识别到的角色和台词加入当前项目。\n先选择游戏窗口并框选区域；采集后返回列表核对、生成配音。',
        '自动识别并播放…': '识别游戏当前字幕，匹配并播放本项目已有的有效配音。\n请先生成台词音频；没有匹配音频的句子会提示，不会在此临时生成。',
        '添加角色声线…': '从一段音频或录屏中截取 3–30 秒，建立一个新角色声线。\n先试听并填写片段中实际说出的文字；保存后可在角色声线模式中使用。',
        '批量建立声线…': '按角色文件夹整理多个声线，也可从视频选择片段。\n程序先准备片段和转写草稿，经你试听、核对后才保存为可用声线。',
        '系统角色声线设置…': '为角色指定本机系统中文声音，并设置默认声音或试听效果。\n用于“系统中文声”模式；角色参考录音声线在“添加角色声线”中管理。',
        '导入最近记录': '把最近一次识别记录中的台词、角色和核对信息加入当前项目。\n导入后可修改文字与声线，再勾选生成；需要较早的记录请点“选择识别记录”。',
        '选择识别记录…': '选择已有的 dialogue.jsonl 识别记录并加入当前项目。\n保留旁边的 images 文件夹，方便通过“查看原图”核对识别文字。',
        '手动添加台词…': '手动填写角色、声线和一条完整台词。\n换行仍属于同一条；添加后视为已人工核对，可直接勾选生成。',
        '粘贴台词…': '打开文本输入窗口，可粘贴多行台词，每个非空行作为一条。\n支持“角色：台词”格式；导入后可逐行修改角色和声线。',
        '导出台词…': '把全部台词或左侧勾选项导出为 TXT、CSV 或 ZIP。\nZIP 可带走已有有效配音；没有配音的条目仍保留文字，导出不会生成新配音。',
        '新建项目': '保存当前修改并创建一个空白台词项目。\n之前的项目及配音仍保留，可通过“打开项目”继续使用。',
        '打开项目…': '选择已保存项目的 project.json，恢复台词、声线设置和已有配音。\n继续生成时会检查并复用仍有效的音频；跨电脑使用请复制完整项目文件夹。',
        '打开输出文件夹': '打开当前项目所在文件夹，查看项目文件、配音和导出结果。\n迁移项目时请一起保留 project.json 和 audio 文件夹。',
        '应用声线': '将旁边选择的声线应用到表格左侧打勾的台词。\n声线改变后需要重新生成对应配音；高亮当前行与左侧勾选范围相互独立。',
        '全选': '勾选表格中的全部台词，供批量生成、声线设置、核对或导出使用。\n只改变勾选范围，不会立即开始生成。',
        '全不选': '取消表格中全部台词的左侧勾选。\n台词和配音仍保留，也不影响当前高亮行的选择。',
        '确认勾选项已核对': '将左侧勾选台词的文字和角色标记为已人工核对。\n请先检查内容是否正确；这一步不代替“核对声线”。',
        '核对声线…': '检查勾选台词中不明确的声线，为同类角色问题统一选择声音。\n选择“暂不处理”的条目保留待核对状态，生成时会跳过。',
        '删除当前行': '从项目列表移除当前高亮的一条台词。\n删除后可用“撤销删除”恢复；批量删除请使用“删除勾选项”。',
        '删除勾选项': '从项目列表移除左侧打勾的全部台词。\n原始识别记录和音频文件保留；删除后可用“撤销删除”恢复。',
        '撤销删除': '恢复本项目最近一次删除的台词，可连续撤销多次删除。\n没有可恢复的删除记录时按钮不可用。',
        '查看原图': '打开当前台词对应的识别截图，用来核对文字和角色。\n手动添加的台词没有原图；移动识别记录时需保留 images 文件夹。',
        '保存当前修改': '立即保存当前行的角色、台词、声线及核对状态。\n修改文字或声线后，旧配音可能失效，需要重新核对并生成。',
        '生成 / 继续勾选项': '为左侧勾选且核对通过的台词生成配音，复用仍有效的已完成音频。\n可接着上次停止的位置继续；生成过程不播放声音，完成后可试听。',
        '重试失败项': '仅重试左侧已勾选、且生成结果为失败的台词。\n请先处理错误提示并确认文字和声线；未勾选的失败项不会重试。',
        '重新生成当前行': '为当前高亮的一条台词强制重新生成配音，不受左侧勾选范围限制。\n请先核对文字和声线；成功后更新这一行使用的配音。',
        '停止生成': '请求停止当前生成任务，并保留已经完成的有效配音。\n正在收尾时请稍候；之后可点“生成 / 继续勾选项”继续。',
        '试听当前行': '播放当前高亮台词的已有配音，不是播放全部勾选项。\n请先选择一行并完成生成；修改文字或声线后需重新生成才能试听。',
        '停止试听': '停止当前正在播放的试听音频。\n不删除配音；需要重听时再次点击“试听当前行”。',
    },
    'manual': {
        '添加台词': '把填写的内容作为一条已人工核对的台词加入当前项目。\n正文中的换行保留在同一条；仅有标点的台词无需生成配音。',
        '取消': '关闭输入窗口，放弃这次尚未添加的台词。',
    },
    'export': {
        '选择位置并导出…': '选择文件名和保存位置，然后按上方范围、格式导出。\nZIP 只复制已有有效配音；左侧没有勾选项时可切换为“全部台词”。',
        '取消导出': '停止本次导出，保留原项目和已有文件。\n取消或失败时，不会用未完成的内容替换已有同名导出文件。',
        '打开导出文件夹': '导出成功后打开结果所在的文件夹，便于查看、复制或分享。',
        '关闭': '返回台词列表；若正在导出，会先取消并等待任务结束。',
    },
    'ocr': {
        '刷新游戏窗口': '重新查找当前运行的游戏窗口。\n游戏启动后或切换窗口后点击，再从列表中选择要识别的窗口。',
        '框选台词区域': '获取当前游戏画面，用鼠标拖框选中台词正文区域。\n尽量只包含字幕，避开角色名和其他界面文字；区域会保存供后续使用。',
        '框选角色名区域': '在游戏画面中单独框选说话角色的名字。\n与正文区域分开，有助于识别角色并匹配对应声线。',
        '不识别角色名': '清除角色名识别区域，之后只识别台词正文。\n角色可回到台词列表中手动填写，不会自动沿用上一句的角色。',
        '开始采集到当前项目': '持续识别所选游戏窗口，把台词和核对结果加入当前项目。\n请先选好字幕区域；采集时不生成或播放配音。',
        '停止采集': '停止本次字幕采集，保留已经加入项目的台词和识别记录。\n待任务结束后可返回列表核对、生成，或再次开始采集。',
        '返回台词列表': '关闭采集窗口并返回当前项目。\n如果仍在识别，会先停止并等待收尾，已采集内容保留。',
    },
    'playback': {
        '开始识别并播放': '持续识别游戏字幕，匹配后播放本项目已完成且仍有效的配音。\n请先生成音频并选择游戏窗口；字幕切换时会停止上一句。',
        '停止识别和播放': '同时停止字幕识别和配音播放，等待任务结束。\n项目中的台词、音频及本次识别记录保留。',
        '返回台词列表': '停止识别和播放，待任务结束后返回当前项目。\n需要补充配音时可在列表中核对并生成，再打开此窗口。',
        '查看延迟统计…': '查看本次识别、匹配、排队和播放器启动的耗时，以及较慢的句子。\n统计截至播放器启动，不代表扬声器实际出声延迟；手动重播单独计数。',
        '暂停自动播放': '暂停配音播放，字幕识别继续运行。\n再次点击“继续自动播放”后，从当前字幕对应音频的开头播放。',
        '继续自动播放': '恢复自动配音，从当前字幕对应音频的开头播放。\n暂停期间识别继续运行，不会从上一句暂停的位置续播。',
        '跳过当前句': '停止并跳过当前字幕的配音。\n同一句持续显示时不会再次自动播放，等字幕切换后恢复匹配。',
        '重播当前句': '从头播放当前字幕对应的已有有效配音。\n没有匹配音频时无法重播；此操作记为手动播放。',
    },
    'metrics': {
        '打开统计文件夹': '打开本次播放的逐句耗时记录和汇总所在文件夹。\n可用来查找慢句和比较不同识别设置。',
        '关闭': '关闭统计窗口，返回自动播放窗口。',
    },
    'add_voice': {
        '选择音频 / 录屏…': '选择包含目标角色说话的音频或视频文件。\n再设置起点和 3–30 秒的时长，建议选完整一句且背景音乐较少的片段。',
        '试听所选片段': '按填写的起点和时长播放参考片段，检查是否完整、清晰且只有目标角色。\n“片段中的台词”需逐字对应实际听到的内容。',
        '停止试听 / 添加': '停止当前的片段试听或声线添加任务。\n等待操作结束后可调整设置再试。',
        '保存新角色声线': '使用当前参考片段、角色名和已核对文字建立声线。\n保存成功后可在角色声线模式使用；同名角色或别名冲突会提示处理。',
        '关闭': '返回台词列表；正在试听或添加时会先停止并等待结束。\n已成功保存的声线保留，尚未保存的填写内容不会成为声线。',
    },
    'video': {
        '选择视频…': '选择含有目标角色声音的视频或录屏。\n再填写该片段的角色名、开始秒数和截取时长。',
        '打开原视频': '用电脑的默认播放器打开所选视频，方便查找说话片段的时间。\n在这里填写开始秒数，例如 1 分 20 秒填 80。',
        '准备片段草稿': '按当前视频范围提取参考片段，交给批量窗口准备转写草稿。\n这一步还不会保存可用声线；返回后需试听并核对文字。',
        '取消': '关闭视频选择窗口，不应用本次填写的片段范围。',
    },
    'library': {
        '选择文件夹…': '选择存放角色素材的根文件夹，按每个角色一个子文件夹整理。\n选择后点击“扫描新素材”；单个视频也可用“从视频添加”。',
        '扫描新素材': '扫描所选文件夹，按角色建立新批次并准备参考片段和转写草稿。\n草稿需试听、核对后才能保存为声线；已有草稿可从“打开草稿”恢复。',
        '从视频添加…': '选择视频并填写角色名、开始时间和 3–30 秒的片段范围。\n片段会先加入草稿，试听、核对后再保存为角色声线。',
        '继续准备草稿': '继续处理当前草稿中勾选且尚未完成的角色，重试片段准备或转写。\n保留已经完成的内容；转写失败也可试听后手动填写文字。',
        '打开草稿…': '打开之前保存的声线草稿，继续试听、修正和保存声线。\n草稿自动保存；素材原文件仍需保留在可访问的位置。',
        '停止当前操作': '请求停止当前准备、转写、保存或试听操作。\n已完成的草稿和已成功保存的声线保留，可稍后继续。',
        '试听片段': '播放当前角色所选的参考片段，检查说话人、内容和起止范围。\n先试听，再逐字核对下方文字，避免把多个人的声音混进同一声线。',
        '转写此片段': '重新识别当前所选参考片段中的话，填入转写草稿。\n可能覆盖当前草稿文字，完成后需再次试听、核对；不会自动保存为声线。',
        '选取视频片段…': '为当前角色重新选择视频或调整截取范围。\n准备新片段后需重新试听、核对角色与文字。',
        '保存已核对声线': '保存左侧勾选且已经试听、核对确认的角色声线。\n未确认的草稿不会保存；与已有声线同名或别名冲突的角色会跳过。',
        '关闭': '保存当前草稿并返回台词列表；正在操作时先停止并等待。\n已完成的草稿和声线保留，下次可继续处理。',
    },
    'review': {
        '保存已选声线': '把选择的声线应用到对应的一组台词。\n“暂不处理”的条目保持原状；此操作只核对声线，不确认文字。',
        '取消': '关闭核对窗口，放弃本次尚未保存的声线选择。',
    },
    'system_voice': {
        '添加角色': '在表格中新增一行，填写角色名并选择其系统声音。\n点击“保存”后生效，仅用于系统中文声模式。',
        '删除所选角色设置': '移除表格中所选角色的系统声音绑定，保存后生效。\n该角色之后按默认设置选择系统声音，不删除参考录音或角色声线。',
        '试听所选声音': '使用旁边选定的系统声音朗读上方试听文字。\n可先比较效果再保存设置；开始后此按钮可停止试听。',
        '停止试听': '停止当前系统声音的试听，等待播放结束后可重新选择声音。',
        '保存': '保存默认系统声音和表格中的角色声音绑定。\n之后系统中文声模式下，按角色自动选择的台词会使用这些设置。',
        '取消': '关闭设置窗口，放弃尚未保存的声音绑定修改。\n正在试听时会先停止播放。',
    },
}
