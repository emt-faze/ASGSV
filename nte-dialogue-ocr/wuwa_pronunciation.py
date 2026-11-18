"""Literal, longest-first WuWa pronunciation hints used at synthesis boundaries."""
from functools import lru_cache
import json
from pathlib import Path
import re

from game_profiles import WUWA_ROOT

DEFAULT_TERMS = {
    '声骸': '声孩', '岁主': '穗主', '瑝珑': '皇龙',
    '今州': '金州', '鸣式': '铭式', '共鸣者': '共明者',
}


@lru_cache(maxsize=8)
def _from_file(path, mtime, size):
    config = json.loads(path.read_text(encoding='utf-8'))
    return tuple(config.get('tts_pronunciations', DEFAULT_TERMS).items())


@lru_cache(maxsize=8)
def _matcher(entries):
    return re.compile('|'.join(re.escape(term) for term in sorted(dict(entries), key=len, reverse=True)))


def tts_text(text, root=None):
    path = Path(root or WUWA_ROOT) / 'config.json'
    try:
        stat = path.stat()
    except FileNotFoundError:
        entries = tuple(DEFAULT_TERMS.items())
    else:
        entries = _from_file(path, stat.st_mtime_ns, stat.st_size)
    if not entries:
        return text
    terms = dict(entries)
    return _matcher(entries).sub(lambda match: terms[match[0]], text)
