"""Draw ASGSV's application mark and export its PNG and Windows icon family."""
from pathlib import Path
import struct

from PySide6.QtCore import QBuffer, QIODevice, QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush, QColor, QImage, QLinearGradient, QPainter, QPainterPath, QPen, QRadialGradient,
)


ASSETS = Path(__file__).resolve().parents[1] / "assets"
ICON_SIZES = (16, 24, 32, 48, 64, 128, 256)


def draw_master():
    image = QImage(1024, 1024, QImage.Format_ARGB32_Premultiplied)
    image.fill(Qt.transparent)
    with QPainter(image) as painter:
        painter.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        # Logical 256 px artboard. Continuous-curvature corners join the straight sides.
        painter.scale(4, 4)
        base = QPainterPath(QPointF(64, 4))
        base.lineTo(192, 4)
        base.cubicTo(237, 4, 252, 19, 252, 64)
        base.lineTo(252, 192)
        base.cubicTo(252, 237, 237, 252, 192, 252)
        base.lineTo(64, 252)
        base.cubicTo(19, 252, 4, 237, 4, 192)
        base.lineTo(4, 64)
        base.cubicTo(4, 19, 19, 4, 64, 4)
        base.closeSubpath()
        ground = QRadialGradient(128, 128, 155)
        ground.setColorAt(0, QColor("#18262C"))
        ground.setColorAt(1, QColor("#0A0F12"))
        painter.fillPath(base, ground)
        painter.setClipPath(base)
        # Half of this stroke falls inside the silhouette.
        painter.strokePath(base, QPen(QColor(216, 180, 103, 107), 2))

        halo = QRadialGradient(128, 128, 100)
        halo.setColorAt(0, QColor(56, 189, 248, 26))
        halo.setColorAt(1, QColor(56, 189, 248, 0))
        painter.fillRect(QRectF(28, 28, 200, 200), halo)

        resonance = QPainterPath(QPointF(128, 29))
        for x, y in ((227, 128), (128, 227), (29, 128)):
            resonance.lineTo(x, y)
        resonance.closeSubpath()
        resonance.moveTo(128, 23)
        resonance.cubicTo(137, 106, 150, 119, 233, 128)
        resonance.cubicTo(150, 137, 137, 150, 128, 233)
        resonance.cubicTo(119, 150, 106, 137, 23, 128)
        resonance.cubicTo(106, 119, 119, 106, 128, 23)
        resonance.closeSubpath()
        painter.strokePath(resonance, QPen(QColor(56, 189, 248, 71), 1.15))

        # Five rounded tracks, with distinct gaps even at the 16 px pyramid level.
        gold = QLinearGradient(122, 53, 134, 203)
        gold.setColorAt(0, QColor("#F3D88B"))
        gold.setColorAt(.52, QColor("#D8B467"))
        gold.setColorAt(1, QColor("#9C7830"))
        for x, height in zip((56, 88, 128, 168, 200), (50, 96, 150, 96, 50)):
            bar = QPainterPath()
            bar.addRoundedRect(QRectF(x - 9, 128 - height / 2, 18, height), 9, 9)
            painter.fillPath(bar, QBrush(gold))
    return image


def encode_png(image, size):
    frame = image.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    buffer = QBuffer()
    buffer.open(QIODevice.WriteOnly)
    if not frame.save(buffer, "PNG"):
        raise RuntimeError(f"Could not encode {size}px PNG")
    return bytes(buffer.data())


def main():
    ASSETS.mkdir(parents=True, exist_ok=True)
    master = draw_master()
    (ASSETS / "app_icon.png").write_bytes(encode_png(master, 512))
    frames = [encode_png(master, size) for size in ICON_SIZES]
    directory = bytearray(struct.pack("<HHH", 0, 1, len(frames)))
    offset = 6 + 16 * len(frames)
    for size, frame in zip(ICON_SIZES, frames):
        dimension = size % 256  # ICONDIR encodes 256 as zero.
        directory.extend(struct.pack("<BBBBHHII", dimension, dimension, 0, 0, 1, 32, len(frame), offset))
        offset += len(frame)
    (ASSETS / "app_icon.ico").write_bytes(directory + b"".join(frames))


if __name__ == "__main__":
    main()
