"""Vector artwork for the ASGSV desktop header, drawn at the window's pixel density."""
from math import ceil
from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets
from ui.stage_background import DialogueStage
from generator_ui_support import studio_font, apply_button_help


HOME_SANS = ('HarmonyOS Sans SC', 'MiSans', 'Bahnschrift', 'Microsoft YaHei UI', 'sans-serif')

HOME_STYLE = """
    QWidget, QLabel, QPushButton {
        font-family: "HarmonyOS Sans SC", "MiSans", "Bahnschrift", "Microsoft YaHei UI", sans-serif;
        color: #F0F6F6;
    }
    QDialog, QWidget#livePage, QTabWidget::pane {
        background: #0D1316;
    }
    QTabWidget::pane { border: 0; border-top: 1px solid #23333A; }
    QTabBar { background: #0D1316; }
    QTabBar::tab {
        background: #0D1316; color: #8FA3AA; padding: 8px 18px;
        border-bottom: 2px solid transparent;
    }
    QTabBar::tab:hover { color: #F0F6F6; background: #182429; }
    QTabBar::tab:selected { color: #E6C57A; border-bottom-color: #D8B467; font-weight: 600; }
    QScrollArea#liveScroll { border: 0; background: #0D1316; }
    QFrame#liveCard, QFrame#liveMonitorCard, QFrame#homeFooter {
        background: qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #162025,stop:1 #11181C);
        border: 1px solid #23333A; border-top-color: #374950; border-radius: 10px;
    }
    QFrame#liveMonitorCard, QFrame#homeFooter {
        background: qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #1B2A30,stop:1 #152126);
    }
    QLabel#hudCaption { font-size: 10px; color: #56707A; }
    QLabel[liveStat="true"] {
        font-family: "Consolas", "Bahnschrift", "HarmonyOS Sans SC", "MiSans", "Microsoft YaHei UI", sans-serif;
    }
    QFrame#liveDivider { background: #23333A; border: 0; }
    QFrame#liveAccent { background: #D8B467; border: 0; }
    QLabel#studioTitle { color: #F0F6F6; }
    QLabel#studioBrand { color: #E6C57A; }
    QLabel#liveCaption { color: #8FA3AA; font-size: 13px; font-weight: 600; }
    QLabel#liveHint, QLabel#subtle { color: #8FA3AA; }
    QLabel#liveHint { font-size: 12px; }
    QLabel#liveValue { font-size: 14px; color: #D4E2E2; }
    QLabel#liveState { font-size: 18px; font-weight: 600; }
    QWidget:disabled, QLabel:disabled { color: #566970; }
    QPushButton, QToolButton, QComboBox {
        background: #1A262B; border: 1px solid #2F444D; border-radius: 6px;
        color: #D4E2E2; padding: 5px 11px;
    }
    QPushButton:hover, QToolButton:hover, QComboBox:hover,
    QPushButton:focus, QToolButton:focus, QComboBox:focus { border-color: #D8B467; }
    QPushButton:pressed, QPushButton:checked, QToolButton:checked { background: #2E424A; }
    QPushButton#primary {
        background: qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #E5C375,stop:0.55 #DBB861,stop:1 #C49A42);
        color: #0D1316; border-color: #E9CC89; font-weight: 700; padding: 6px 18px;
    }
    QPushButton#primary:hover { background: #E6C57A; }
    QPushButton#primary:pressed { background: #C9A457; }
    QPushButton#primary:focus { border-color: #F0F6F6; }
    QPushButton#danger:hover { background: rgba(220,68,68,0.18); border-color: #D94848; color: #FF8585; }
    #studioHeader QPushButton, #studioHeader QComboBox {
        background: rgba(20,29,33,0.85); border-color: #2E424A; color: #E6EFEF;
    }
    #studioHeader QPushButton:hover, #studioHeader QComboBox:hover,
    #studioHeader QPushButton:focus, #studioHeader QComboBox:focus { border-color: #D8B467; }
    QToolButton:disabled, QPushButton:disabled, QPushButton#primary:disabled,
    QPushButton#danger:disabled, #studioHeader QPushButton:disabled, QComboBox:disabled,
    #studioHeader QComboBox:disabled { background: #182126; color: #566970; border-color: #23333A; }
    QPushButton#primary:disabled, #workbenchHeader QPushButton#primary:disabled {
        color: #A6B8BE; background: #223036; border-color: #43565E;
    }
    QPushButton[accentButton="true"]:hover:enabled { border-color: #2F444D; }

    QPushButton[accentButton="true"]:focus { border-color: #D8B467; }
    #studioHeader QComboBox#gameSelector {
        border: 1px solid #45616A; border-radius: 8px; padding: 6px 28px 6px 12px;
        background: qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 rgba(38,59,66,238),stop:1 rgba(18,30,35,222));
    }
    #studioHeader QComboBox#gameSelector:hover, #studioHeader QComboBox#gameSelector:focus {
        border-color: #D8B467;
    }
    QComboBox#gameSelector::drop-down { border: none; background: transparent; width: 22px; }
    QComboBox#gameSelector::down-arrow { image: none; width: 0; height: 0; }
    QLineEdit, QPlainTextEdit, QTextEdit, QAbstractItemView, QMenu {
        color: #F0F6F6; background: #141D21; border: 1px solid #2F444D;
        selection-color: #F0F6F6; selection-background-color: #36515B;
    }
    QLineEdit, QPlainTextEdit { border-radius: 5px; padding: 4px 6px; }
    QTextEdit#liveSubtitle { background: transparent; border: 0; padding: 0; }
    QComboBox { padding-right: 22px; }
    QScrollBar:vertical { background: transparent; border: 0; width: 6px; margin: 0; }
    QScrollBar:horizontal { background: transparent; border: 0; height: 6px; margin: 0; }
    QScrollBar::handle { background: #2A3B42; border-radius: 3px; }
    QScrollBar::handle:vertical { min-height: 24px; }
    QScrollBar::handle:horizontal { min-width: 24px; }
    QScrollBar::handle:hover { background: #D8B467; }
    QAbstractScrollArea::corner { background: transparent; }
    QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
    QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
    QTableView { alternate-background-color: #182429; gridline-color: #23333A; }
    QHeaderView::section, QTableCornerButton::section {
        background: #1A262B; color: #D4E2E2; border: 0; padding: 6px;
        border-bottom: 1px solid #2F444D;
    }
    QSplitter::handle { background: #23333A; }
    QPushButton#liveSpeaker, QLabel#hudSpeaker {
        color: #ACC5CD; background: #203237; border: 1px solid #446168;
        border-radius: 13px; padding: 2px 10px; font-weight: 600;
    }
    QPushButton#liveSpeaker[named="true"], QLabel#hudSpeaker[named="true"] {
        color: #E9CA83; background: #302A1E; border-color: #D8B467;
    }
    QProgressBar#liveProgress {
        background: #23333A; border: 0; border-radius: 3px;
    }
    QProgressBar#liveProgress::chunk { background: #38BDF8; border-radius: 3px; }
    QSlider#liveVolume::groove:horizontal { height: 3px; background: #30444C; }
    QSlider#liveVolume::sub-page:horizontal { background: #D8B467; }
    QSlider#liveVolume::handle:horizontal {
        background: #E6C57A; border-radius: 5px; width: 10px; margin: -4px 0;
    }
    QListWidget#liveHistory { background: transparent; border: 0; outline: 0; }
    QListWidget#liveHistory::item { padding: 4px 5px; border-radius: 4px; }
    QListWidget#liveHistory::item:hover, QListWidget#liveHistory::item:selected { background: #243A42; }
    QFrame#hudCard {
        background-color: rgba(13, 19, 22, 0.92); border: 1px solid #2E424A; border-radius: 10px;
    }
    QFrame#hudCard QPushButton { padding: 1px 9px; font-size: 12px; }
    QLabel#hudSpeaker { font-size: 13px; border-radius: 10px; }
    QLabel#hudLine { font-size: 16px; }
    QMenu::item:selected { background: #36515B; }
    QFrame#workbenchHeader { background: #0D1316; border: 0; border-bottom: 1px solid #1E2B31; }
    QLabel#workbenchTitle { color: #EDF4F3; font-size: 17px; font-weight: 600; }
    QLabel#versionBadge, QLabel#countBadge {
        color: #B4C6CA; background: #1C272C; border: 1px solid #314047;
        border-radius: 8px; padding: 1px 6px; font: 11px "Consolas";
    }
    QToolButton#headerTool { background: #192226; border: 1px solid #2D3B40; border-radius: 7px; padding: 0; }
    QToolButton#headerTool:hover, QToolButton#headerTool:focus { border-color: #D8B467; }
    #workbenchHeader QPushButton#primary {
        color: #101719; background: #D8B467; border: 1px solid #E5C579; border-radius: 7px; font-weight: 700;
    }
    #workbenchHeader QPushButton#primary:hover { background: #E6C57A; }
    #workbenchHeader QLabel#transportKey:disabled { color: #A6B8BE; background: #192328; border-color: #43565E; }
    #workbenchHeader QLabel#transportKey { color: #302D23; background: rgba(30,35,32,0.13); border-color: rgba(48,45,35,0.32); }
    QFrame#workbenchSidebar { background: #101619; border: 0; border-right: 1px solid #223037; }
    QLabel#navCaption { padding: 4px 9px; color: #819396; font-size: 11px; }
    QToolButton#navItem {
        background: transparent; color: #A9BBBE; border: 0; border-left: 3px solid transparent;
        border-radius: 3px; padding: 0 6px; font-size: 13px;
    }
    QToolButton#navItem:hover, QToolButton#navItem:focus { background: #1C272A; color: #ECF4F3; }
    QToolButton#navItem:checked { background: #242C29; border-left-color: #D8B467; color: #F0D8A1; }
    QLabel#sectionTitle { color: #EDF4F3; font-size: 19px; font-weight: 600; }
    QFrame#sideCard { background: #131B1F; border: 1px solid #223037; border-radius: 10px; }
    QFrame#liveToolbar, QFrame#homeFooter { background: transparent; border: 0; }
    QFrame#liveToolbar { border-bottom: 1px solid #263436; }
    QFrame#railRule { background: #2A393B; border: 0; }
    QLabel#stageCaption { color: #E3D4AC; font-size: 13px; font-weight: 600; }
    QLabel#liveState { font-size: 14px; font-weight: 500; color: #B6C9C9; }
    QPushButton#liveSpeaker[named="true"] {
        color: #F0D69A; background: rgba(25,32,30,200); border: 1px solid #9C885D;
    }
    #studioHeader QPushButton {
        background: rgba(255, 255, 255, 0.04); border: 1px solid rgba(255, 255, 255, 0.08);
        border-radius: 8px; padding: 6px 12px;
    }
    #studioHeader QPushButton:hover:enabled, #studioHeader QPushButton:focus {
        border-color: #D8B467;
    }
    QPushButton[transport="true"]:disabled {
        color: #83969D; background: #162024; border: 1px solid #6E828A;
    }
    QLabel#transportKey {
        font-family: "Consolas", "Bahnschrift", monospace; font-size: 10px;
        color: #C1D0D3; background: #202D32; border: 1px solid #42565D; border-bottom-width: 2px; border-radius: 4px;
    }
    QLabel#transportKey:disabled { color: #A0B2B8; background: #1C272C; border-color: #384B52; }
    QFrame#telemetryBadge { background: #162025; border: 1px solid #26363E; border-radius: 11px; }
    QLabel#telemetryLabel {
        color: #ADBDC2; font-family: "Consolas", "Microsoft YaHei UI", monospace; font-size: 11px;
    }
    QFrame#stateCapsule {
        background: #171F22; border: 1px solid #384449; border-radius: 12px;
    }
    QFrame#stateCapsule[connected="true"] {
        background: #142A2E; border-color: #355C62;
    }
    QFrame#stateCapsule QLabel#liveState { font-size: 12px; color: #A9B9BE; }
    QFrame#stateCapsule[connected="true"] QLabel#liveState { color: #BFDFE2; }
    QPushButton#libraryLink {
        background: transparent; border: 0; border-radius: 0; text-align: left;
        padding: 3px 0; color: #A5C6CB; text-decoration: underline; font-family: "Consolas", "Bahnschrift", "Microsoft YaHei UI", sans-serif;
    }
    QPushButton#libraryLink:hover, QPushButton#libraryLink:focus {
        color: #E4C684; background: transparent;
    }
    QListWidget#liveHistory { color: #BACACC; }
    QListWidget#liveHistory::item { padding: 6px 2px; }
    QToolTip { color: #E6EFEF; background: #182328; border: 1px solid #2E424A; border-radius: 5px; padding: 5px 8px; }
    QFrame#stagePopup { background: #162025; border: 1px solid #364B52; border-radius: 9px; }
    QFrame#stagePopup QSlider::groove:horizontal { height: 4px; border-radius: 2px; background: #304249; }
    QFrame#stagePopup QSlider::sub-page:horizontal { border-radius: 2px; background: #C3AD78; }
    QFrame#stagePopup QSlider::handle:horizontal { width: 10px; margin: -3px 0; border-radius: 5px; background: #E2CE9E; }
    QPushButton#liveSpeaker:enabled { padding-right: 23px; }
    QPushButton#liveSpeaker:enabled:hover, QPushButton#liveSpeaker:focus { border-color: #F1D18B; background: #343325; }
    QToolButton#backgroundSettings { border: 1px solid #405259; border-radius: 6px; background: rgba(13,22,25,180); padding: 1px; }
    QToolButton#backgroundSettings:hover { border-color: #D8B467; }
    QPushButton#stagePreview { color: #B4C7C9; background: rgba(13, 22, 25, 180); border: 1px solid #486065; border-radius: 6px; padding: 3px 8px; font-size: 11px; }
    QPushButton#stagePreview:hover, QPushButton#stagePreview:focus { color: #F1D79B; border-color: #C9B079; }
    QPushButton#stagePreview:disabled { color: #72858C; border-color: #34464C; }
    QLabel#historyEmptyText { color: #8FA7AE; font-size: 12px; }
"""


def configure_home(home):
    from combo_popup import install_combobox_style
    install_combobox_style(QtWidgets.QApplication.instance())
    # Fusion uses palette roles for popup viewports, arrows and native indicators.
    palette = QtGui.QPalette(home.palette())
    roles = QtGui.QPalette.ColorRole
    for color, names in (
        ('#0D1316', ('Window', 'Shadow')), ('#141D21', ('Base',)),
        ('#182429', ('AlternateBase', 'ToolTipBase')), ('#1A262B', ('Button',)),
        ('#F0F6F6', ('WindowText', 'Text', 'HighlightedText', 'ToolTipText', 'BrightText')),
        ('#D4E2E2', ('ButtonText',)), ('#8FA3AA', ('PlaceholderText',)),
        ('#36515B', ('Highlight',)), ('#D8B467', ('Link',)),
        ('#2F444D', ('Light', 'Midlight')), ('#23333A', ('Mid', 'Dark')),
    ):
        for name in names:
            palette.setColor(getattr(roles, name), QtGui.QColor(color))
    for role in (roles.WindowText, roles.Text, roles.ButtonText):
        palette.setColor(QtGui.QPalette.Disabled, role, QtGui.QColor('#566970'))
    home.setPalette(palette)
    for widget in home.findChildren(QtWidgets.QWidget):
        widget.setPalette(palette)
    home.setAttribute(QtCore.Qt.WA_AlwaysShowToolTips, True)
    home.setStyleSheet(HOME_STYLE)
    apply_button_help(home, 'generator')


class GameWordmark(QtWidgets.QLabel):
    """Typeset the active game's name as live, DPI-independent outlined lettering."""

    def __init__(self, text):
        super().__init__(text)
        self.setFont(studio_font(HOME_SANS, 39, QtGui.QFont.Weight.Black, tracking=1.6))
        self.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)

    def ink(self):
        path = QtGui.QPainterPath()
        path.addText(0, 0, self.font(), self.text())
        return QtGui.QTransform().shear(-.11, 0).map(path)

    def sizeHint(self):
        box = self.ink().boundingRect()
        return QtCore.QSize(ceil(box.width()) + 6, ceil(box.height()) + 6)

    def minimumSizeHint(self):
        return self.sizeHint()

    def paintEvent(self, event):
        path = self.ink()
        box = path.boundingRect()
        if box.isEmpty():
            return
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        p.translate(3 - box.left(), (self.height() - box.height()) / 2 - box.top())
        finish = QtGui.QLinearGradient(box.topLeft(), box.bottomLeft())
        finish.setColorAt(0, QtGui.QColor('#fffdf3'))
        finish.setColorAt(.60, QtGui.QColor('#f6f0db'))
        finish.setColorAt(1, QtGui.QColor('#ddc189'))
        p.fillPath(path, finish)
        p.end()


def resonance_mark(painter, bounds):
    painter.save()
    painter.translate(bounds.topLeft())
    painter.scale(bounds.width() / 60, bounds.height() / 60)
    painter.setPen(QtCore.Qt.NoPen)
    painter.setBrush(QtGui.QColor('#e4cc94'))
    for x, top, bottom in ((8, 28, 34), (16, 19, 43), (25, 11, 50),
                           (34, 4, 54), (43, 16, 42), (51, 25, 33)):
        blade = QtGui.QPolygonF([QtCore.QPointF(x + 3, top), QtCore.QPointF(x + 2, 30),
                                 QtCore.QPointF(x - 2, bottom), QtCore.QPointF(x - 3, 30)])
        painter.drawPolygon(blade)
    painter.restore()


def studio_icon():
    icon = QtGui.QIcon()
    for size in (16, 32, 48, 64, 128):
        pix = QtGui.QPixmap(size, size)
        pix.fill(QtCore.Qt.transparent)
        painter = QtGui.QPainter(pix)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(QtGui.QColor('#122f31'))
        painter.drawRoundedRect(QtCore.QRectF(0, 0, size, size), size * .2, size * .2)
        inset = size * .07
        resonance_mark(painter, QtCore.QRectF(inset, inset, size - 2 * inset, size - 2 * inset))
        painter.end()
        icon.addPixmap(pix)
    return icon


class StudioHeader(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('studioHeader')
        self.setMinimumHeight(63)
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)

    def paintEvent(self, event):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        resonance_mark(p, QtCore.QRectF(2, (self.height()-39)/2, 39, 39))
