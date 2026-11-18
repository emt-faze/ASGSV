"""Application-wide Qt icon and Windows taskbar identity for ASGSV entry points."""
import ctypes
from functools import lru_cache
from pathlib import Path
import sys

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication


APP_ID = "ASGSV.WuWa.DubbingStudio"
ASSETS = Path(__file__).resolve().parents[1] / "assets"


@lru_cache(maxsize=1)
def application_icon():
    # Add the ICO first so Qt preserves the hand-sized Windows frames.
    icon = QIcon(str(ASSETS / "app_icon.ico"))
    icon.addFile(str(ASSETS / "app_icon.png"))
    return icon


def create_application(arguments):
    app = QApplication.instance()
    if app is None:
        if sys.platform == "win32":
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
        app = QApplication(arguments)
    from combo_popup import install_combobox_style
    install_combobox_style(app)
    app.setWindowIcon(application_icon())
    return app
