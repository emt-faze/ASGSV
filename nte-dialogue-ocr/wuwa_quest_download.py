"""The signed, independently published catalog of complete, optional quest recordings."""
import hashlib
import json
import re
from urllib.request import Request, build_opener, urlopen
from zipfile import ZipFile

import wuwa_update as update
from wuwa_voice_pack import mount_signature

CATALOG = 'catalog.json'
MAX_CATALOG = 1024 * 1024


def open_file(source, filename, *, tag=None, headers=None, timeout=10):
    if filename != CATALOG and not re.fullmatch(r'quest-[0-9]+-r[1-9][0-9]*\.zip', filename):
        raise ValueError('无效的任务包文件名')
    if source == 'cnb':
        base = f'https://cnb.cool/{update.CNB_REPOSITORY}/-/git/raw/main/quest-packs/'
        opener = build_opener(update.CnbRedirect()).open
    elif source == 'github':
        base = 'https://raw.githubusercontent.com/emt-faze/ASGSV/main/quest-packs/'
        opener = urlopen
    else:
        raise ValueError('未知配音包来源')
    return opener(Request(base + filename, headers={'User-Agent':'ASGSV-Quest-Library', **(headers or {})}), timeout=timeout)


def parse_catalog(raw):
    body = update.signed_body(raw)
    if body.get('kind') != 'quest-catalog' or not isinstance(body.get('quests'), list):
        raise ValueError('文件不是官方任务配音目录')
    result = {}
    for e in body['quests']:
        scope = e['scope']
        if (not re.fullmatch(r'quest:[0-9]+', scope) or scope in result or e['kind'] != 'quest'
                or type(e['revision']) is not int or e['revision'] < 1
                or e['asset'] != f"quest-{scope[6:]}-r{e['revision']}.zip"
                or not update.HASH.fullmatch(e['sha256']) or not 0 < e['size'] <= 2**31
                or type(e['lines']) is not int or e['lines'] < 1 or not e['name'].strip()
                or not isinstance(e['tested_builds'], list)
                or any(not update.HASH.fullmatch(h) for h in e['tested_builds'])):
            raise ValueError('配音目录的任务记录无效')
        update.semver(e['minimum'])
        result[scope] = e
    return result


def catalog_file(root):
    return root / 'updates' / 'quest-catalog.json'


def fetch_catalog(root, source):
    with open_file(source, CATALOG, headers={'Cache-Control':'no-cache'}) as response:
        raw = response.read(MAX_CATALOG + 1)
    if len(raw) > MAX_CATALOG:
        raise ValueError('配音目录超过大小限制')
    offers = parse_catalog(raw)
    update.atomic(catalog_file(root), raw)
    return offers


def build_id(root):
    inventory = root / 'sources/local-inventory.json'
    if inventory.is_file():
        signature = mount_signature(json.loads(inventory.read_bytes()))
        if signature:
            return hashlib.sha256(update.canonical(signature)).hexdigest()
    return ''


def install_offer(install, root, entry, source, stop, progress):
    if update.semver(update.portable_version(install)) < update.semver(entry['minimum']):
        raise ValueError('请先升级工作台至 v' + entry['minimum'])
    progress(update.SOURCES[source] + ' · ' + entry['name'])
    path = update.download(install, {}, entry, progress, source=source, stop=stop, open_file=open_file)
    if stop.is_set():
        raise InterruptedError('已取消下载')
    with ZipFile(path) as z:
        m = update.inspect_zip(z)
        if any(m.get(k) != entry[k] for k in ('kind','scope','revision','minimum','name')):
            raise ValueError('下载包和所选任务不一致')
    return update.install_content(install, path, progress, content_root=root, stop=stop)
