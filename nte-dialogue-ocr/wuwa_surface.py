"""Painted controls and motion for the live workbench (logical Qt pixels)."""
from math import cos, pi, sin, tau
from time import monotonic

from PySide6 import QtCore as C, QtGui as G, QtWidgets as W
from ui.icons import bind_button_icon


def transition(widget, duration):
    anim = C.QVariantAnimation(widget)
    anim.setDuration(duration)
    anim.setEasingCurve(C.QEasingCurve.OutCubic)
    anim.valueChanged.connect(lambda _: widget.update())
    anim.setStartValue(0.0)
    anim.setEndValue(1.0)
    return anim


def animate_to(anim, end):
    current = anim.currentValue()
    anim.stop()
    anim.setStartValue(current)
    anim.setEndValue(float(end))
    anim.start()


class ToggleSwitch(W.QCheckBox):
    def __init__(self, text):
        super().__init__(text)
        self.setCursor(C.Qt.PointingHandCursor)
        self.motion = transition(self, 170)
        self.toggled.connect(lambda checked: animate_to(self.motion, checked))

    def sizeHint(self):
        return C.QSize(46 + self.fontMetrics().horizontalAdvance(self.text()), max(28, self.fontMetrics().height()+4))

    def minimumSizeHint(self):
        return self.sizeHint()

    def hitButton(self, point):
        return self.rect().contains(point)

    def paintEvent(self, event):
        p = G.QPainter(self)
        p.setRenderHint(G.QPainter.Antialiasing)
        fraction = self.motion.currentValue()
        y = self.height()/2
        track = C.QRectF(2, y-9, 33, 18)
        color = G.QColor.fromRgbF(*[(a+(b-a)*fraction)/255 for a,b in zip((52,68,75),(216,180,103))])
        p.setBrush(color)
        p.setPen(G.QPen(G.QColor('#F1D797') if self.hasFocus() else color, 1))
        p.drawRoundedRect(track, 9, 9)
        p.setPen(C.Qt.NoPen)
        p.setBrush(G.QColor('#FAFCF8'))
        p.drawEllipse(C.QPointF(11+fraction*15, y), 6.5, 6.5)
        p.setPen(self.palette().windowText().color())
        p.drawText(self.rect().adjusted(44,0,0,0), C.Qt.AlignVCenter | C.Qt.AlignLeft, self.text())


class GameSelector(W.QComboBox):
    def __init__(self):
        super().__init__()
        self.setObjectName('gameSelector')

    def paintEvent(self, event):
        super().paintEvent(event)
        p = G.QPainter(self)
        p.setRenderHint(G.QPainter.Antialiasing)
        p.setPen(G.QPen(G.QColor('#D8B467' if self.underMouse() else '#A1B6BA'), 1.5,
                        C.Qt.SolidLine, C.Qt.RoundCap, C.Qt.RoundJoin))
        x, y = self.width()-15, self.height()/2
        p.drawPolyline(G.QPolygonF([C.QPointF(x-4,y-1.5), C.QPointF(x,y+2.5), C.QPointF(x+4,y-1.5)]))


class AccentButton(W.QPushButton):
    def __init__(self, text, glyph=None):
        super().__init__(text)
        self.setProperty('accentButton', True)
        self.setCursor(C.Qt.PointingHandCursor)
        self.hover = transition(self, 180)
        self.setIconSize(C.QSize(15, 15))
        if glyph:
            self.set_glyph(glyph)

    def set_glyph(self, name):
        bind_button_icon(self, name, normal_color="#BDCCD0", disabled_color="#869AA2")

    def enterEvent(self, event):
        super().enterEvent(event)
        animate_to(self.hover, 1)

    def leaveEvent(self, event):
        super().leaveEvent(event)
        animate_to(self.hover, 0)

    def paintEvent(self, event):
        super().paintEvent(event)
        amount = self.hover.currentValue()
        if amount < .01 or not self.isEnabled():
            return
        p = G.QPainter(self)
        p.setRenderHint(G.QPainter.Antialiasing)
        color = G.QColor('#F67474' if self.objectName() == 'danger' else '#D8B467')
        p.setBrush(C.Qt.NoBrush)
        for inset, opacity in ((.6, .9), (1.7, .16)):
            color.setAlphaF(opacity*amount)
            p.setPen(G.QPen(color, 1))
            radius = 8 if self.parentWidget() and self.parentWidget().objectName() == 'studioHeader' else 6
            p.drawRoundedRect(C.QRectF(self.rect()).adjusted(inset,inset,-inset,-inset), radius, radius)


class CapsuleText(W.QLabel):
    def __init__(self, text):
        super().__init__(text)
        self.setObjectName('liveState')
        self.setTextFormat(C.Qt.PlainText)

    def minimumSizeHint(self):
        return C.QSize(18, super().minimumSizeHint().height())

    def setText(self, text):
        super().setText(text)
        self.setToolTip(text)

    def paintEvent(self, event):
        if self.fontMetrics().horizontalAdvance(self.text()) <= self.width():
            super().paintEvent(event)
            return
        p = G.QPainter(self)
        p.setPen(self.palette().windowText().color())
        p.drawText(self.rect(), C.Qt.AlignLeft | C.Qt.AlignVCenter,
                   self.fontMetrics().elidedText(self.text(), C.Qt.ElideRight, self.width()))


class LibraryLink(W.QPushButton):
    def paintEvent(self, event):
        super().paintEvent(event)
        if self.underMouse() or self.hasFocus():
            p = G.QPainter(self)
            p.setPen(G.QColor('#E4C684'))
            y = (self.height() + self.fontMetrics().height()) // 2
            p.drawLine(0, y, min(self.width()-1, self.fontMetrics().horizontalAdvance(self.text())), y)


class TransportButton(AccentButton):
    def __init__(self, text, glyph):
        super().__init__(text, glyph)
        self.setProperty('transport', True)
        self.key_badge = W.QLabel(self)
        self.key_badge.setObjectName('transportKey')
        self.key_badge.setAlignment(C.Qt.AlignCenter)
        self.key_badge.setAttribute(C.Qt.WA_TransparentForMouseEvents)
        self.key_badge.setFixedHeight(17)

    def set_key(self, key):
        self.key_badge.setText(key)
        self.key_badge.ensurePolished()
        width = self.key_badge.fontMetrics().horizontalAdvance(key) + 10
        self.key_badge.setFixedWidth(width)
        # QStyle lays out the original icon and translated text in the remaining space.
        self.setStyleSheet(f'QPushButton {{padding-left: 7px; padding-right: {width + 10}px;}}')
        self.setAccessibleDescription('游戏内快捷键 ' + key)
        self.position_key()

    def position_key(self):
        self.key_badge.move(self.width()-self.key_badge.width()-5,
                            (self.height()-self.key_badge.height())//2)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.position_key()


class TelemetryBadge(W.QFrame):
    colors = {'off': '#56656B', 'ready': '#58C9AC', 'busy': '#65C7E5', 'warning': '#D8B467'}

    def __init__(self, title):
        super().__init__()
        self.setObjectName('telemetryBadge')
        self.setFixedHeight(23)
        layout = W.QHBoxLayout(self)
        layout.setContentsMargins(8, 0, 8, 0)
        layout.setSpacing(6)
        self.dot = W.QFrame()
        self.dot.setFixedSize(6, 6)
        self.glow = W.QGraphicsDropShadowEffect(self.dot)
        self.glow.setOffset(0, 0)
        self.glow.setBlurRadius(6)
        self.dot.setGraphicsEffect(self.glow)
        layout.addWidget(self.dot)
        self.label = W.QLabel(title)
        self.label.setObjectName('telemetryLabel')
        layout.addWidget(self.label)
        self.setAccessibleName(title)
        self.status = None

    def display(self, state, detail):
        if self.status == (state, detail):
            return
        self.status = state, detail
        color = self.colors[state]
        self.dot.setStyleSheet(f'background: {color}; border: none; border-radius: 3px;')
        self.glow.setColor(G.QColor(color))
        self.glow.setEnabled(state != 'off')
        self.setToolTip(detail)
        self.setAccessibleDescription(detail)


class PulseLamp(W.QWidget):
    def __init__(self):
        super().__init__()
        self.connected = False
        self.setFixedSize(20,20)

    def paintEvent(self, event):
        p = G.QPainter(self)
        p.setRenderHint(G.QPainter.Antialiasing)
        pulse = .5 + .5*sin(monotonic()*2.3)
        center = C.QPointF(10,10)
        color = G.QColor('#72D7E1' if self.connected else '#78868B')
        halo = G.QRadialGradient(center, 6+3*pulse)
        halo.setColorAt(0, color)
        edge = G.QColor(color)
        edge.setAlpha(0)
        halo.setColorAt(1, edge)
        p.setPen(C.Qt.NoPen)
        if self.connected:
            p.setBrush(halo)
            p.drawEllipse(center, 9, 9)
        p.setBrush(color)
        p.drawEllipse(center, 3, 3)


class ResonanceWaveWidget(W.QWidget):
    """A status-driven resonance display. The progress track below owns time position."""
    def __init__(self):
        super().__init__()
        self.playing = False
        self.setMinimumHeight(32)
        self.setMaximumHeight(205)
        self.setSizePolicy(W.QSizePolicy.Expanding, W.QSizePolicy.Expanding)
        self.setAttribute(C.Qt.WA_TransparentForMouseEvents)
        self.clock = C.QTimer(self)
        self.clock.timeout.connect(self.update)
        self.clock.setInterval(80)

    def set_playing(self, playing):
        if self.playing != playing:
            self.playing = playing
            self.clock.setInterval(40 if playing else 80)
            self.update()

    def showEvent(self, event):
        super().showEvent(event)
        self.clock.start()

    def hideEvent(self, event):
        self.clock.stop()
        super().hideEvent(event)

    def paintEvent(self, event):
        p = G.QPainter(self)
        p.setRenderHint(G.QPainter.Antialiasing)
        w, h = self.width(), self.height()
        if w < 2:
            return
        mid = h * .53
        t = monotonic() % 1200
        speed = 2.8 if self.playing else .60
        intensity = .41 if self.playing else .18
        # The faint vertical divisions reveal amplitude as the signal rises.
        for x in range(24, w, 36):
            u = x/w
            length = h * .33 * sin(pi*u)
            fade = G.QLinearGradient(0, mid-length, 0, mid+length)
            for stop, alpha in ((0,0),(.5,28),(1,0)):
                fade.setColorAt(stop, G.QColor(110, 160, 170, alpha))
            p.setPen(G.QPen(G.QBrush(fade), 1, C.Qt.DotLine))
            p.drawLine(C.QPointF(x,mid-length), C.QPointF(x,mid+length))
        # Instrument ticks establish the zero line without competing with the dialogue.
        p.setPen(G.QPen(G.QColor(117, 162, 172, 52), 1))
        for x in range(4, w, 12):
            tick = 3 if (x-4) % 60 == 0 else 1
            p.drawLine(C.QPointF(x,mid-tick), C.QPointF(x,mid+tick))
        for index, (rgb, cycles) in enumerate((((61,165,187),3.3),((216,180,103),4.1),
                                               ((56,189,248),4.8),((129,219,223),3.8))):
            path = G.QPainterPath()
            for x in range(0, w+3, 3):
                u = min(x/w, 1)
                env = sin(pi*u)**1.45
                carrier = sin(tau*cycles*u - t*speed + index*.8)
                side = cos(tau*2.3*u + .6*t*speed + index*.4)
                y = mid + h*intensity*env*(.68*carrier+.32*side)
                if x:
                    path.lineTo(x,y)
                else:
                    path.moveTo(x,y)
            ink = G.QLinearGradient(0,0,w,0)
            for stop, alpha in ((0,0),(.16,65),(.48,195),(.75,125),(1,0)):
                ink.setColorAt(stop,G.QColor(*rgb,alpha))
            if index == 2:
                fill = G.QPainterPath(path)
                fill.lineTo(w, mid)
                fill.lineTo(0, mid)
                fill.closeSubpath()
                p.fillPath(fill, G.QColor(56,189,248,22 if self.playing else 12))
            p.setPen(G.QPen(G.QBrush(ink), 1.4 if index==2 else 1))
            p.drawPath(path)
