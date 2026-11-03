"""Consistent light colors and contextual help for the generator dialogs."""
from html import escape

from PySide6 import QtCore, QtGui, QtWidgets


def light_palette():
    # Fusion still inherits the OS palette. Define every surface/foreground pair,
    # including inactive and disabled controls, so Windows dark mode cannot mix in.
    palette = QtGui.QPalette()
    colors = {
        'Window': '#f5f7fa', 'WindowText': '#172c43',
        'Base': '#ffffff', 'AlternateBase': '#f0f4f8', 'Text': '#172c43',
        'Button': '#eef2f6', 'ButtonText': '#172c43',
        'Highlight': '#167d77', 'HighlightedText': '#ffffff',
        'ToolTipBase': '#fffbe8', 'ToolTipText': '#172c43',
        'PlaceholderText': '#63758a', 'Link': '#126d66', 'LinkVisited': '#615080',
        'Light': '#ffffff', 'Midlight': '#e9eef4', 'Mid': '#b8c4d1',
        'Dark': '#8898aa', 'Shadow': '#52647a', 'BrightText': '#ffffff',
    }
    if hasattr(QtGui.QPalette.ColorRole, 'Accent'):
        colors['Accent'] = '#167d77'
    for group in (QtGui.QPalette.Active, QtGui.QPalette.Inactive, QtGui.QPalette.Disabled):
        for role, color in colors.items():
            palette.setColor(group, getattr(QtGui.QPalette.ColorRole, role), QtGui.QColor(color))
    for role in ('WindowText', 'Text', 'ButtonText', 'PlaceholderText'):
        palette.setColor(QtGui.QPalette.Disabled, getattr(QtGui.QPalette.ColorRole, role),
                         QtGui.QColor('#59697b'))
    for role in ('Base', 'Button'):
        palette.setColor(QtGui.QPalette.Disabled, getattr(QtGui.QPalette.ColorRole, role),
                         QtGui.QColor('#edf0f4'))
    palette.setColor(QtGui.QPalette.Disabled, QtGui.QPalette.Highlight, QtGui.QColor('#d4e6e4'))
    palette.setColor(QtGui.QPalette.Disabled, QtGui.QPalette.HighlightedText, QtGui.QColor('#172c43'))
    return palette


DIALOG_STYLE = """
    QDialog { background:#f5f7fa; }
    QWidget { color:#172c43; }
    QWidget:disabled { color:#59697b; }
    QPushButton {
        color:#172c43; background:#eef2f6; border:1px solid #aebac8;
        border-radius:3px; padding:6px 10px;
    }
    QPushButton:hover { background:#e3eeed; border-color:#167d77; }
    QPushButton:pressed { background:#d4e6e4; }
    QPushButton:focus { border-color:#167d77; }
    QPushButton:disabled { color:#59697b; background:#edf0f4; border-color:#c4ccd6; }
    QLineEdit, QPlainTextEdit, QComboBox, QAbstractSpinBox {
        padding:4px; color:#172c43; background:white;
        selection-color:white; selection-background-color:#167d77;
    }
    QLineEdit:disabled, QPlainTextEdit:disabled, QComboBox:disabled, QAbstractSpinBox:disabled {
        color:#59697b; background:#edf0f4;
    }
    QTableWidget {
        background:white; alternate-background-color:#f0f4f8; gridline-color:#e6eaf0;
        selection-color:white; selection-background-color:#167d77;
    }
    QHeaderView::section { background:#e9eef4; color:#172c43; padding:7px; border:0; }
    QPushButton#primary { background:#167d77; color:white; font-weight:600; border-radius:4px; }
    QPushButton#primary:hover { background:#126d66; }
    QPushButton#primary:pressed { background:#105e58; }
    QPushButton#primary:disabled { background:#d4e6e4; color:#52647a; }
    QToolTip { background:#fffbe8; color:#172c43; border:1px solid #b8c4d1; padding:7px; }
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
    'generator': {
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
