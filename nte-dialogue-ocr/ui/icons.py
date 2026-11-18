"""Scalable, tintable workbench icons. Sizes are in Qt device-independent pixels."""
from __future__ import annotations

import math
from functools import cache
from pathlib import Path
from tempfile import TemporaryDirectory

from PySide6.QtCore import QEvent, QObject, QSize, Qt
from PySide6.QtGui import QColor, QGuiApplication, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QAbstractButton

__all__ = ["get_icon", "get_pixmap", "bind_button_icon"]

# One SVG coordinate space for both transport symbols and the resonance motifs.
_PATHS = {
    "chevron_down": "M6 9l6 6 6-6",
    "landscape": "M3 5h18v14H3z M4 18l6-7 4 4 2-2 4 5 M16 8h.1",
    "stop": "M5.5 5.5h13v13h-13Z",
    "check": "M4 12l5 5L20 6",
    "scan": "M3 8V4h5m8 0h5v4m0 8v4h-5m-8 0H3v-4 M6 12h12",
    "previous": "M15 5l-7 7 7 7",
    "next": "M9 5l7 7-7 7",
    "restore": "M9 3H4v5 M4 3l7 7 M20 9V6a2 2 0 0 0-2-2h-4 M4 13v5a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-4",
    "play": "M7 4.5 19 12 7 19.5Z",
    "pause": "M8.5 5v14 M15.5 5v14",
    "replay": "M4 4.5v5.8h5.8 M4.4 9.4a8 8 0 1 1 .4 6.2",
    "skip": "M5 5.5 15 12 5 18.5Z M18.5 5.5v13",
    "folder": "M3 18.5V6a1.5 1.5 0 0 1 1.5-1.5H9l2.5 3H19a1.5 1.5 0 0 1 1.5 1.5v1 "
              "M3 18.5l3-8h15.5l-3 8a1.5 1.5 0 0 1-1.4 1H4.4a1.4 1.4 0 0 1-1.4-1Z",
    "history": "M3 5v5h5 M3.7 9a8.5 8.5 0 1 1-.1 5.7 M12 7.5V12l3.5 2",
    "settings": "M9.7 3h4.6l.5 2.2 1.9 1.1 2.1-.7 2.3 4-1.6 1.5v2l1.6 1.5-2.3 4-2.1-.7-1.9 1.1-.5 2.1H9.7"
                "l-.5-2.1-1.9-1.1-2.1.7-2.3-4 1.6-1.5v-2l-1.6-1.5 2.3-4 2.1.7 1.9-1.1Z "
                "M15 12a3 3 0 1 1-6 0 3 3 0 1 1 6 0Z",
    "pin": "m8 3 9 0-2 3v5l3 3v1H6v-1l3-3V6L8 3Zm4 12v6",
    "pip": "M5 4.5h14a2 2 0 0 1 2 2v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-11a2 2 0 0 1 2-2Z "
           "M12 11h6v5h-6Z",
    "terminal": "M5 4.5h14a2 2 0 0 1 2 2v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-11a2 2 0 0 1 2-2Z "
                "M6.5 9l3 3-3 3 M12.5 15H17",
    "keyboard": "M4.5 6h15A2 2 0 0 1 21.5 8v8a2 2 0 0 1-2 2h-15a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2Z "
                "M6.5 10h.5m2.8 0h.5m2.8 0h.5m2.8 0h.5 M7 14h.5m3 0h6",
    "volume": "M3 9.5h4L12 5v14l-5-4.5H3Z M15.5 8.5a5 5 0 0 1 0 7 M18 5.5a9 9 0 0 1 0 13",
    "sidebar_collapse": "M5 4.5h14a2 2 0 0 1 2 2v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-11a2 2 0 0 1 2-2Z "
                        "M9 5v14 M16.5 9l-3 3 3 3",
    "tacet_voice": "M12 2l2.2 7.8L20 12l-5.8 2.2L12 22l-2.2-7.8L4 12l5.8-2.2Z "
                   "M2 12h6.5l1.7-2.8 3.6 5.6 1.7-2.8H22",
    "echo_role": "M12 2.5 20 12l-8 9.5L4 12Z M12 7.5l3.7 4.5-3.7 4.5L8.3 12Z",
    "quest_wave": "M12 6.5l4 5.5-4 5.5L8 12Z M6.2 7.6a7.3 7.3 0 0 0 0 8.8 "
                  "M17.8 7.6a7.3 7.3 0 0 1 0 8.8 M3.8 5.8a10.3 10.3 0 0 0 0 12.4 "
                  "M20.2 5.8a10.3 10.3 0 0 1 0 12.4",
    "asgsv_logo": "M3 10v4 M6 7.5v9 M9 5v14 M12 2v20 M15 5v14 M18 7.5v9 M21 10v4",
}


def _svg(name: str, color: str) -> bytes:
    path = _PATHS[name]
    tint = QColor(color)
    if not tint.isValid():
        raise ValueError(f"Invalid icon color: {color!r}")
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24">'
        f'<path d="{path}" fill="none" stroke="{tint.name()}" stroke-opacity="{tint.alphaF()}" '
        'stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"/></svg>'
    ).encode("ascii")


def _pixmap(svg: bytes, size: int, ratio: float) -> QPixmap:
    if size <= 0 or not math.isfinite(ratio) or ratio <= 0:
        raise ValueError("Icon size and device pixel ratio must be positive")
    pixels = max(1, math.floor(size * ratio + 0.5))
    result = QPixmap(pixels, pixels)
    result.fill(Qt.transparent)
    # Paint at the final physical resolution; attach the logical scale only afterwards.
    with QPainter(result) as painter:
        painter.setRenderHint(QPainter.Antialiasing)
        QSvgRenderer(svg).render(painter)
    result.setDevicePixelRatio(ratio)
    return result


@cache
def _icon_directory() -> TemporaryDirectory:
    return TemporaryDirectory(prefix="asgsv-icons-")


@cache
def _svg_file(name: str, color: str) -> str:
    # Qt's native scalable QIcon engine opens SVGs by filename, including on a
    # later DPI change. Keep these tiny sources alive for the application's lifetime.
    svg = _svg(name, color)
    path = Path(_icon_directory().name) / f"{name}-{QColor(color).rgba():08x}.svg"
    path.write_bytes(svg)
    return str(path)


def get_icon(name: str, color: str = "#8FA3AA", size: int = 16) -> QIcon:
    """Return a scalable SVG icon, with a pixmap for its preferred logical size."""
    pixmap = get_pixmap(name, color, size)
    icon = QIcon(_svg_file(name, color))
    icon.addPixmap(pixmap)
    return icon


def get_pixmap(name: str, color: str = "#8FA3AA", size: int = 16, *,
               device_pixel_ratio: float | None = None) -> QPixmap:
    """Render a standalone pixmap. Pass the target widget's DPR for a specific screen."""
    app = QGuiApplication.instance()
    if not isinstance(app, QGuiApplication):
        raise RuntimeError("Create a QGuiApplication before requesting a pixmap")
    if device_pixel_ratio is None:
        target = app.focusWindow() or app.primaryScreen()
        device_pixel_ratio = target.devicePixelRatio()
    return _pixmap(_svg(name, color), size, device_pixel_ratio)


class _ButtonBinding(QObject):
    """Some Qt styles request Normal even while a push button is hovered."""
    def configure(self, button, name, normal, hover, disabled, size):
        self.normal = get_icon(name, normal, size)
        self.hovered = get_icon(name, hover, size)
        for icon in (self.normal, self.hovered):
            icon.addFile(_svg_file(name, hover), QSize(), QIcon.Active)
            icon.addFile(_svg_file(name, disabled), QSize(), QIcon.Disabled)
        button.setIconSize(QSize(size, size))
        self.apply(button, button.underMouse())

    def apply(self, button, hovered):
        button.setIcon(self.hovered if hovered and button.isEnabled() else self.normal)

    def eventFilter(self, button, event):
        kind = event.type()
        if kind == QEvent.Enter:
            self.apply(button, True)
        elif kind == QEvent.Leave:
            self.apply(button, False)
        elif kind == QEvent.EnabledChange:
            self.apply(button, button.underMouse())
        return False


def bind_button_icon(button: QAbstractButton, name: str, normal_color: str = "#8FA3AA",
                     hover_color: str = "#D8B467", disabled_color: str = "#46565C",
                     size: int = 15) -> None:
    """Set the button's icon and update its tint on hover, leave, enable and disable."""
    binding = button.findChild(_ButtonBinding, options=Qt.FindDirectChildrenOnly)
    if binding is None:
        binding = _ButtonBinding(button)
    binding.configure(button, name, normal_color, hover_color, disabled_color, size)
    button.installEventFilter(binding)
