"""Shared drop-down list styling for workbench and dynamically created dialogs."""
from PySide6 import QtCore as C, QtGui as G, QtWidgets as W


LIST_STYLE = """
    QComboBox QAbstractItemView, QListView#comboChoices {
        font-size: 13px; color: #E6EFEF; background-color: #141D22;
        border: 1px solid #2E424A; border-radius: 8px; padding: 4px; outline: none;
        selection-background-color: #213038; selection-color: #D8B467;
    }
    QListView#comboChoices::item {
        min-height: 30px; padding: 4px 10px; border-radius: 5px;
    }
    QListView#comboChoices::item:hover, QListView#comboChoices::item:selected {
        background: #213038; color: #D8B467;
    }
"""


class _PopupPlacement(C.QObject):
    def __init__(self, combo, popup):
        super().__init__(popup)
        self.combo = combo

    def eventFilter(self, popup, event):
        if event.type() == C.QEvent.Show:
            self.position(popup)
            view = self.combo.view()
            index = self.combo.model().index(self.combo.currentIndex(), self.combo.modelColumn(),
                                             self.combo.rootModelIndex())
            if index.isValid():
                view.selectionModel().setCurrentIndex(index, C.QItemSelectionModel.ClearAndSelect)
        return False

    def position(self, popup):
        combo = self.combo
        area = combo.screen().availableGeometry()
        origin = combo.mapToGlobal(C.QPoint(0, 0))
        below = origin.y() + combo.height() + 4
        room = area.bottom() + 1 - below
        above = origin.y() - 4 - area.top()
        height = popup.height()
        if height > room and above > room:
            height = min(height, above)
            y = origin.y() - 4 - height
        else:
            height = min(height, max(1, room))
            y = below
        popup.setMinimumWidth(min(combo.width(), area.width()))
        width = min(max(popup.width(), combo.width()), area.width())
        x = max(area.left(), min(origin.x(), area.right() + 1 - width))
        popup.setGeometry(x, y, width, height)


def polish_combobox(combo: W.QComboBox):
    if combo.property('asgsvComboPolished'):
        return
    combo.setProperty('asgsvComboPolished', True)
    combo.setStyleSheet(combo.styleSheet() + '\nQComboBox { combobox-popup: 0; }')
    view = W.QListView()
    view.setObjectName('comboChoices')
    view.setMouseTracking(True)
    combo.setView(view)
    combo.setItemDelegate(W.QStyledItemDelegate(view))
    popup = view.window()
    popup.setWindowFlags(C.Qt.Popup | C.Qt.FramelessWindowHint | C.Qt.NoDropShadowWindowHint)
    popup.setAttribute(C.Qt.WA_TranslucentBackground, True)
    popup.setFrameShape(W.QFrame.NoFrame)
    popup.setObjectName('comboPopup')
    popup.setStyleSheet('QFrame#comboPopup { background-color: #141D22; border: none; border-radius: 8px; padding: 0; }')
    palette = view.palette()
    for role in (G.QPalette.Window, G.QPalette.Base):
        palette.setColor(role, G.QColor('#141D22'))
    for widget in (popup, view, view.viewport()):
        widget.setPalette(palette)
    view.setStyleSheet(LIST_STYLE)
    popup.installEventFilter(_PopupPlacement(combo, popup))


class _ComboPolishFilter(C.QObject):
    def eventFilter(self, obj, event):
        if event.type() == C.QEvent.Polish and isinstance(obj, W.QComboBox):
            polish_combobox(obj)
        return False


def install_combobox_style(app):
    if not app.property('asgsvComboStyle'):
        app.setEffectEnabled(C.Qt.UI_AnimateCombo, False)
        app.installEventFilter(_ComboPolishFilter(app))
        app.setProperty('asgsvComboStyle', True)
