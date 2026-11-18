"""Compact view of the live panel. All transport actions belong to the main window."""
from PySide6 import QtCore as C, QtWidgets as W

from asgsv_identity import application_icon
from wuwa_identity import HOME_STYLE
from wuwa_surface import AccentButton, PulseLamp, ResonanceWaveWidget


class LiveHud(W.QWidget):
    restore_requested = C.Signal()
    compact_size = C.QSize(156, 36)
    full_size = C.QSize(460, 106)

    def __init__(self, panel):
        flags = C.Qt.FramelessWindowHint | C.Qt.WindowStaysOnTopHint | C.Qt.Tool
        super().__init__(None, flags)
        self.setAttribute(C.Qt.WA_TranslucentBackground)
        self.setAttribute(C.Qt.WA_ShowWithoutActivating)
        self.setWindowTitle('鸣潮 · 配音悬浮窗')
        self.setWindowIcon(application_icon())
        self.resize(self.compact_size)
        self.playing = False
        self.hovered = False
        self.grace = C.QTimer(self)
        self.grace.setSingleShot(True)
        self.grace.setInterval(2500)
        self.grace.setTimerType(C.Qt.PreciseTimer)
        self.grace.timeout.connect(self.update_extent)
        self.motion = C.QPropertyAnimation(self, b'geometry', self)
        self.motion.setDuration(280)
        self.motion.setEasingCurve(C.QEasingCurve.OutCubic)
        self.setStyleSheet(HOME_STYLE)
        self.drag_origin = None
        self.dragged = False
        self.card = W.QFrame(self)
        self.card.setObjectName('hudCard')
        self.card.setStyleSheet('QFrame#hudCard { border-radius: 17px; }')
        self.card.setGeometry(self.rect())
        self.compact = W.QWidget(self.card)
        self.compact.setFixedSize(self.compact_size)
        badge = W.QHBoxLayout(self.compact)
        badge.setContentsMargins(8, 0, 8, 0)
        badge.setSpacing(4)
        badge.addStretch()
        self.lamp = PulseLamp()
        badge.addWidget(self.lamp)
        self.status = W.QLabel('ASGSV 待机')
        badge.addWidget(self.status)
        badge.addStretch()
        self.full = W.QWidget(self.card)
        self.full.setFixedSize(self.full_size)
        self.compact_fade = W.QGraphicsOpacityEffect(self.compact)
        self.full_fade = W.QGraphicsOpacityEffect(self.full)
        self.compact_fade.setOpacity(1)
        self.full_fade.setOpacity(0)
        self.compact.setGraphicsEffect(self.compact_fade)
        self.full.setGraphicsEffect(self.full_fade)
        self.full.hide()
        layout = W.QVBoxLayout(self.full)
        layout.setContentsMargins(12, 7, 12, 7)
        layout.setSpacing(3)
        row = W.QHBoxLayout()
        self.speaker = W.QLabel()
        self.speaker.setObjectName('hudSpeaker')
        self.speaker.setTextFormat(C.Qt.PlainText)
        row.addWidget(self.speaker)
        row.addStretch()
        self.pin = AccentButton('', 'pin')
        self.pin.setCheckable(True)
        self.pin.setFixedSize(29, 23)
        self.pin.setAccessibleName('固定悬浮窗')
        self.pin.setStyleSheet('QPushButton:checked { background:#403A29; border:1px solid #D8B467; }')
        self.pin.toggled.connect(self.pin_changed)
        self.pin.setToolTip('智能收起已开启 · 点击固定展开')
        self.restore = AccentButton('还原主窗口', 'restore')
        self.restore.clicked.connect(self.restore_requested)
        row.addWidget(self.restore)
        row.addWidget(self.pin)
        layout.addLayout(row)
        self.line = W.QLabel()
        self.line.setObjectName('hudLine')
        self.line.setTextFormat(C.Qt.PlainText)
        self.line.setSizePolicy(W.QSizePolicy.Ignored, W.QSizePolicy.Preferred)
        layout.addWidget(self.line, 1)
        controls = W.QHBoxLayout()
        controls.setSpacing(6)
        self.controls = []
        for caption, source, glyph in (('暂停', panel.pause, 'pause'), ('重播', panel.replay, 'replay'),
                                       ('跳过', panel.skip, 'skip')):
            button = AccentButton(caption, glyph)
            button.clicked.connect(source.click)
            controls.addWidget(button)
            self.controls.append((button, source))
        controls.addStretch()
        self.wave = ResonanceWaveWidget()
        self.wave.setFixedSize(98, 22)
        controls.addWidget(self.wave)
        layout.addLayout(controls)
        for child in self.card.findChildren(W.QWidget) + [self.card]:
            child.installEventFilter(self)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'card'):
            self.card.setGeometry(self.rect())
            progress = (self.width() - self.compact_size.width()) / (self.full_size.width() - self.compact_size.width())
            opacity = min(1.0, max(0.0, (progress - .55) / .45))
            self.full_fade.setOpacity(opacity)
            self.compact_fade.setOpacity(1 - opacity)
            self.full.setVisible(opacity > 0)
            self.compact.setVisible(opacity < 1)
            self.compact.move((self.width() - self.compact.width()) // 2,
                              (self.height() - self.compact.height()) // 2)

    def pin_changed(self, pinned):
        self.pin.setToolTip('已固定展开 · 点击恢复智能收起' if pinned else '智能收起已开启 · 点击固定展开')
        self.update_extent()

    def set_playing(self, playing):
        if playing == self.playing:
            return
        self.playing = playing
        self.wave.set_playing(playing)
        if playing:
            self.grace.stop()
        elif self.isVisible():
            self.grace.start()
        self.update_extent()

    def update_extent(self):
        if not self.isVisible() or self.drag_origin is not None:
            return
        expand = self.playing or self.hovered or self.pin.isChecked() or self.grace.isActive()
        size = self.full_size if expand else self.compact_size
        rect = self.geometry()
        area = self.screen().availableGeometry()
        right = rect.center().x() >= area.center().x()
        bottom = rect.center().y() >= area.center().y()
        x = rect.right() - size.width() + 1 if right else rect.left()
        y = rect.bottom() - size.height() + 1 if bottom else rect.top()
        target = C.QRect(max(area.left(), min(x, area.right() + 1 - size.width())),
                         max(area.top(), min(y, area.bottom() + 1 - size.height())),
                         size.width(), size.height())
        if self.motion.state() == C.QAbstractAnimation.Running and self.motion.endValue() == target:
            return
        self.motion.stop()
        if rect != target:
            self.motion.setStartValue(rect)
            self.motion.setEndValue(target)
            self.motion.start()

    def enterEvent(self, event):
        super().enterEvent(event)
        self.hovered = True
        self.update_extent()

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self.hovered = False
        self.update_extent()

    def showEvent(self, event):
        super().showEvent(event)
        self.update_extent()

    def hideEvent(self, event):
        self.motion.stop()
        self.grace.stop()
        self.hovered = False
        super().hideEvent(event)

    def set_listening(self, connected, running):
        self.status.setText('ASGSV 监听中' if connected else ('ASGSV 等待游戏' if running else 'ASGSV 已停止'))
        self.lamp.connected = connected
        self.lamp.update()

    def sync_controls(self):
        for button, source in self.controls:
            button.setEnabled(source.isEnabled())
        pause, source = self.controls[0]
        caption = '恢复' if source.isChecked() else '暂停'
        if caption != pause.text():
            pause.setText(caption)
            pause.set_glyph('play' if source.isChecked() else 'pause')

    def set_dialogue(self, speaker, body, named):
        self.speaker.setText(self.speaker.fontMetrics().elidedText(speaker, C.Qt.ElideRight, 250))
        self.speaker.setProperty('named', named)
        self.speaker.style().unpolish(self.speaker)
        self.speaker.style().polish(self.speaker)
        self.line.setText(self.line.fontMetrics().elidedText(' '.join(body.split()), C.Qt.ElideRight, self.full_size.width() - 26))
        self.line.setToolTip(body)

    def mousePressEvent(self, event):
        if event.button() == C.Qt.LeftButton:
            self.motion.stop()
            self.drag_origin = event.globalPosition().toPoint()
            self.drag_window = self.pos()
            self.dragged = False
            event.accept()

    def mouseMoveEvent(self, event):
        if self.drag_origin is not None and event.buttons() & C.Qt.LeftButton:
            delta = event.globalPosition().toPoint() - self.drag_origin
            if self.dragged or delta.manhattanLength() >= W.QApplication.startDragDistance():
                self.dragged = True
                self.move(self.drag_window + delta)
                event.accept()

    def mouseReleaseEvent(self, event):
        self.drag_origin = None
        self.dragged = False
        self.update_extent()

    def eventFilter(self, obj, event):
        # Buttons retain normal clicks. A drag beginning on one cancels its click.
        kind = event.type()
        if kind == C.QEvent.MouseButtonPress:
            self.mousePressEvent(event)
        elif kind == C.QEvent.MouseMove:
            self.mouseMoveEvent(event)
            return self.dragged
        elif kind == C.QEvent.MouseButtonRelease:
            dragged = self.dragged
            self.mouseReleaseEvent(event)
            if dragged and isinstance(obj, W.QAbstractButton):
                obj.setDown(False)
            return dragged
        return False

    def closeEvent(self, event):
        self.restore_requested.emit()
        event.accept()
