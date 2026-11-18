"""Artwork and controls for the dialogue stage, in logical Qt pixels."""
import json
from math import sin, tau
from pathlib import Path

import cv2
import numpy as np
from PySide6 import QtCore as C, QtGui as G, QtWidgets as W

from dialogue_generation import atomic_text

COAST = Path(__file__).resolve().parents[1] / 'assets/dialogue-coast.jpg'
DEFAULT = dict(source='coast', path='', mask=0.75, blur=0)


def load_art(source, path):
    if source == 'signal':
        image = G.QImage(1440, 900, G.QImage.Format_RGB32)
        with G.QPainter(image) as painter:
            grad = G.QLinearGradient(0, 900, 1280, 0)
            grad.setColorAt(0, G.QColor('#111E27'))
            grad.setColorAt(1, G.QColor('#31545A'))
            painter.fillRect(image.rect(), grad)
            painter.setRenderHint(G.QPainter.Antialiasing)
            painter.setPen(G.QPen(G.QColor(145, 195, 183, 58), 1.4))
            for band in range(36):
                curve = G.QPainterPath()
                for x in range(-10, 1461, 12):
                    phase = x / 1480 * tau
                    y = 260 + band * 11 + 82 * sin(phase + band / 32) + 55 * sin(2 * phase - band / 42)
                    if x == -10:
                        curve.moveTo(x, y)
                    else:
                        curve.lineTo(x, y)
                painter.drawPath(curve)
        return G.QPixmap.fromImage(image)
    reader = G.QImageReader(str(path if source == 'file' else COAST))
    reader.setAutoTransform(True)
    size = reader.size()
    if size.isValid() and max(size.width(), size.height()) > 3840:
        reader.setScaledSize(size.scaled(3840, 3840, C.Qt.KeepAspectRatio))
    image = reader.read()
    if image.isNull():
        raise ValueError('图片无法读取：' + reader.errorString())
    return G.QPixmap.fromImage(image)


class DialogueStage(W.QFrame):
    def __init__(self, root=None):
        super().__init__()
        self.setObjectName('dialogueStage')
        self.config_file = Path(root) / 'stage-background.json' if root else None
        self.options = DEFAULT.copy()
        self.art = load_art('coast', '')
        self.notice = ''
        if self.config_file and self.config_file.is_file():
            try:
                data = json.loads(self.config_file.read_text(encoding='utf-8'))
                source = data['source']
                if source not in ('coast', 'signal', 'file'):
                    raise ValueError('未知背景预设')
                mask = max(.3, min(.9, float(data['mask'])))
                blur = max(0, min(20, int(data['blur'])))
                self.art = load_art(source, data['path'])
                self.options.update(source=source, path=data['path'], mask=mask, blur=blur)
            except (OSError, ValueError, TypeError, KeyError) as exc:
                self.notice = f'已显示默认海岸背景。{exc}'
        self.cache_key = None
        self.cached = G.QPixmap()
        self.previous = G.QPixmap()
        self._mix = 1.
        self.fade = C.QPropertyAnimation(self, b'mix', self)
        self.fade.setDuration(300)
        self.fade.setEasingCurve(C.QEasingCurve.InOutCubic)
        self.fade.finished.connect(self.end_fade)

    def set_mix(self, value):
        self._mix = value
        self.update()

    mix = C.Property(float, lambda self: self._mix, set_mix)

    def end_fade(self):
        self.previous = G.QPixmap()

    def layer(self):
        ratio = self.devicePixelRatioF()
        width, height = max(1, round(self.width()*ratio)), max(1, round(self.height()*ratio))
        blur = self.options['blur']
        key = (width, height, self.art.cacheKey(), blur)
        if key != self.cache_key:
            # Crop the source first so even a panorama needs only a viewport-sized intermediate.
            aw, ah = self.art.width(), self.art.height()
            cw, ch = max(1,min(aw,round(ah*width/height))), max(1,min(ah,round(aw*height/width)))
            crop = self.art.copy((aw-cw)//2, (ah-ch)//2, cw, ch)
            # Keep the blur kernel small, even on a scaled 4K display. Qt expands the
            # already filtered pixmap on composition; child text and controls stay sharp.
            down = max(1, round(blur * ratio / 2))
            dest_w, dest_h = max(1,round(width/down)), max(1,round(height/down))
            frame = crop.scaled(dest_w, dest_h, C.Qt.KeepAspectRatioByExpanding, C.Qt.SmoothTransformation)
            frame = frame.copy((frame.width()-dest_w)//2, (frame.height()-dest_h)//2, dest_w, dest_h)
            if frame.hasAlphaChannel():
                opaque = G.QPixmap(frame.size())
                opaque.fill(G.QColor('#131C1E'))
                with G.QPainter(opaque) as painter:
                    painter.drawPixmap(0,0,frame)
                frame = opaque
            if blur:
                img = frame.toImage().convertToFormat(G.QImage.Format_RGBA8888)
                rgba = np.frombuffer(img.constBits(), np.uint8).reshape(dest_h, img.bytesPerLine())
                rgba = rgba[:, :dest_w*4].reshape(dest_h, dest_w, 4)
                pixels = cv2.GaussianBlur(rgba, (0,0), blur*ratio/down)
                smooth = G.QImage(pixels.data,dest_w,dest_h,pixels.strides[0],G.QImage.Format_RGBA8888)
                frame = G.QPixmap.fromImage(smooth.copy())
            self.cached, self.cache_key = frame, key
        return self.cached

    def switch(self, source, path='', *, reset=False):
        art = load_art(source, path)
        old = self.layer()
        if self.fade.state() == C.QAbstractAnimation.Running:
            blend = old.copy()
            with G.QPainter(blend) as p:
                p.drawPixmap(blend.rect(), self.previous)
                p.setOpacity(self._mix)
                p.drawPixmap(blend.rect(), old)
            old = blend
        self.fade.stop()
        self.previous, self.art = old, art
        if reset:
            self.options = DEFAULT.copy()
        self.notice = ''
        self.options.update(source=source, path=path)
        self.fade.setStartValue(0.)
        self.fade.setEndValue(1.)
        self.fade.start()
        self.save()

    def save(self):
        if self.config_file:
            atomic_text(self.config_file, json.dumps(self.options, ensure_ascii=False))

    def paintEvent(self, event):
        with G.QPainter(self) as p:
            p.setRenderHints(G.QPainter.Antialiasing | G.QPainter.SmoothPixmapTransform)
            bounds = C.QRectF(self.rect())
            clip = G.QPainterPath()
            clip.addRoundedRect(bounds, 10, 10)
            p.setClipPath(clip)
            p.fillRect(bounds, G.QColor('#131C1E'))
            if not self.previous.isNull():
                p.drawPixmap(self.rect(), self.previous)
            p.setOpacity(self._mix)
            p.drawPixmap(self.rect(), self.layer())
            p.setOpacity(1.)
            mask = self.options['mask']
            shade = G.QLinearGradient(0, 0, 0, bounds.height())
            for stop, weight in ((0,.32),(.23,.23),(.45,.72),(.75,1.1),(1,1.3)):
                shade.setColorAt(stop, G.QColor(11,20,23,round(255*min(.99,max(stop**3*.70,mask*weight)))))
            p.fillRect(bounds, shade)
            p.setPen(G.QPen(G.QColor(128,155,151,65), 1))
            p.setBrush(C.Qt.NoBrush)
            p.drawRoundedRect(bounds.adjusted(.5,.5,-.5,-.5),10,10)


def show_at(popup, anchor):
    popup.adjustSize()
    bounds = anchor.screen().availableGeometry()
    pos = anchor.mapToGlobal(C.QPoint(0, anchor.height()+5))
    x = max(bounds.left(), min(pos.x(), bounds.right()-popup.width()+1))
    y = pos.y() if pos.y()+popup.height() <= bounds.bottom()+1 else max(bounds.top(), pos.y()-popup.height()-anchor.height()-10)
    popup.move(x,y)
    popup.show()


class BackgroundMenu(W.QFrame):
    def __init__(self, stage):
        super().__init__(stage, C.Qt.Popup)
        self.setObjectName('stagePopup')
        self.setAttribute(C.Qt.WA_DeleteOnClose)
        self.stage = stage
        self.save_timer = C.QTimer(self)
        self.save_timer.setSingleShot(True)
        self.save_timer.setInterval(160)
        self.save_timer.timeout.connect(stage.save)
        self.custom_path = stage.options["path"]
        self.setFixedWidth(272)
        form = W.QFormLayout(self)
        form.setContentsMargins(14, 13, 14, 13)
        form.setSpacing(9)
        self.presets = W.QComboBox()
        self.presets.addItem('海岸', 'coast')
        self.presets.addItem('共鸣曲线', 'signal')
        if stage.options['source']=='file':
            self.presets.addItem(Path(stage.options['path']).name, 'file')
        self.presets.setCurrentIndex(self.presets.findData(stage.options['source']))
        self.presets.activated.connect(self.choose_preset)
        form.addRow('舞台背景', self.presets)
        import_button = W.QPushButton('导入本地图片…')
        import_button.clicked.connect(self.import_image)
        form.addRow(import_button)
        for name, title, lo, hi in (('mask','遮罩',30,90),('blur','虚化',0,20)):
            slider = W.QSlider(C.Qt.Horizontal)
            slider.setAccessibleName(title)
            slider.setRange(lo,hi)
            slider.setValue(round(stage.options[name]*(100 if name=='mask' else 1)))
            value = W.QLabel()
            value.setFixedWidth(42)
            unit = '%' if name=='mask' else ' px'
            value.setText(f'{slider.value()}{unit}')
            def change(v, field=name, label=value, suffix=unit):
                self.stage.options[field] = v/100 if field=='mask' else v
                label.setText(f'{v}{suffix}')
                self.stage.update()
                self.save_timer.start()
            slider.valueChanged.connect(change)
            row = W.QHBoxLayout()
            row.addWidget(slider)
            row.addWidget(value)
            form.addRow(title,row)
        reset = W.QPushButton('恢复默认背景')
        reset.clicked.connect(self.restore)
        form.addRow(reset)
        if stage.notice:
            note = W.QLabel(stage.notice)
            note.setWordWrap(True)
            form.addRow(note)

    def hideEvent(self, event):
        if self.save_timer.isActive():
            self.save_timer.stop()
            self.stage.save()
        super().hideEvent(event)

    def choose_preset(self):
        source = self.presets.currentData()
        if source!='file':
            self.stage.switch(source)
        else:
            self.stage.switch(source,self.custom_path)

    def restore(self):
        self.stage.switch('coast',reset=True)
        self.close()

    def import_image(self):
        stage = self.stage
        self.close()
        dialog = W.QFileDialog(stage.window(), '选择主舞台背景')
        dialog.setFileMode(W.QFileDialog.ExistingFile)
        dialog.setNameFilter('图片 (*.png *.jpg *.jpeg *.webp)')
        dialog.setAttribute(C.Qt.WA_DeleteOnClose)
        def apply_file(path):
            try:
                stage.switch('file',path)
            except (ValueError,OSError) as exc:
                W.QMessageBox.warning(stage.window(),'图片未能载入',str(exc))
        dialog.fileSelected.connect(apply_file)
        dialog.open()
