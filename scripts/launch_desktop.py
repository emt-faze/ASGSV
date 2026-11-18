"""Initialize settings for a freshly extracted installation and open the selected UI."""
import sys
from pathlib import Path

from configure_generator import configure_portable

ROOT = Path(__file__).resolve().parents[1]


def main():
    args = sys.argv[1:]
    ocr = bool(args and args[0] == '--ocr')
    shared = ROOT / 'wuwa-dubber/data/config.json'
    desktop = ROOT / 'nte-dialogue-ocr/config.json'
    if not shared.is_file() or not desktop.is_file():
        configure_portable(ROOT)
    if ocr:
        from app import main as run
        sys.argv[1:] = args[1:]
    else:
        from dialogue_generator_ui import main as run
    return run()


if __name__ == '__main__':
    raise SystemExit(main())
