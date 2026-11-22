"""Data-only community recordings. A publisher can sign audio, never application updates."""
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import re
import tempfile
import wave
from zipfile import BadZipFile, ZIP_STORED, ZipFile, ZipInfo

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from dialogue_generation import digest as game_digest
from wuwa_catalog import resolve_text
from wuwa_update import HASH, atomic, canonical, digest, locked

MAX_AUDIO = 32 * 1024**2
MAX_PACK = 256 * 1024**2
MAX_MANIFEST = 4 * 1024**2
MAX_LINES = 4096
FORMAT = 'asgsv-community-audio-v1'
BRANCH = 'community-packs'
FIELDS = ('id', 'state_id', 'line_id', 'branch', 'text_id', 'speaker_id', 'speaker', 'raw_text', 'plot_key')


def fingerprints():
    import gzip
    with gzip.open(Path(__file__).parent / 'assets/daily-quest-fingerprints.json.gz', 'rt', encoding='utf8') as f:
        return json.load(f)


def hexdigest(data):
    return hashlib.sha256(data).hexdigest()


def official_text(raw):
    text, unresolved = resolve_text(raw, '漂泊者')
    if not text or unresolved:
        raise ValueError('这句含有尚未解析的游戏占位符')
    return text


def clean_wav(data):
    """Preserve PCM samples exactly, discard source metadata, reject incomplete files."""
    if len(data) > MAX_AUDIO:
        raise ValueError('单句录音超过 32 MiB')
    try:
        with wave.open(io.BytesIO(data), 'rb') as source:
            channels, width, rate, frames, compression, _ = source.getparams()
            if (channels not in (1, 2) or width not in (1, 2, 3, 4) or not 8000 <= rate <= 96000
                    or not 0 < frames <= 180 * rate or compression != 'NONE'
                    or channels * width * frames > MAX_AUDIO):
                raise ValueError('录音必须是时长不超过三分钟的单声道或双声道 PCM WAV')
            pcm = source.readframes(frames)
    except (wave.Error, EOFError) as exc:
        raise ValueError('不是完整的 PCM WAV 录音') from exc
    if len(pcm) != frames * width * channels or pcm.count(b'\x80' if width == 1 else b'\0') == len(pcm):
        raise ValueError('录音截断或完全静音')
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as out:
        out.setparams((channels, width, rate, frames, 'NONE', 'not compressed'))
        out.writeframes(pcm)
    return buf.getvalue()


def public_line(line, scope, name, reference):
    if not isinstance(line, dict) or set(line) != {*FIELDS, 'sha256'}:
        raise ValueError('投稿台词字段不符合配音包格式')
    if any(not isinstance(v, str) or len(v) > 16384 for v in line.values()):
        raise ValueError('台词字段过长或类型错误')
    ident = game_digest(['wuwa', line['state_id'], line['branch'], line['line_id'], line['text_id']])
    fp = game_digest([line['raw_text'], line['speaker_id'], line['speaker'], line['plot_key']])
    if (ident != line['id'] or reference.get(ident) != fp or not HASH.fullmatch(line['sha256'])):
        raise ValueError('角色、台词或换句编号与已核实的游戏索引不一致')
    return dict(id=ident, state=line['state_id'], number=line['line_id'], speaker=line['speaker'],
                speaker_id=line['speaker_id'], quest=scope, quest_name=name, fingerprint=fp,
                text=official_text(line['raw_text']), sha256=line['sha256'])


def member(name):
    info = ZipInfo(name)
    info.compress_type = ZIP_STORED
    info.external_attr = 0o600 << 16
    return info


def build_submission(path, scope, rows, clips, key, reference, author, progress=lambda _: None):
    required = reference['quests'].get(scope)
    if not required or len(required) > MAX_LINES:
        raise ValueError('此任务尚无可用于公开分享的完整台词指纹')
    missing = set(required) - clips.keys()
    if missing:
        raise ValueError(f'还缺 {len(missing)} 句本人生成的有效录音，配齐后即可分享')
    with tempfile.TemporaryDirectory(dir=path.parent) as tmp:
        temp = Path(tmp) / 'bundle.zip'
        lines, seen = [], set()
        with ZipFile(temp, 'w') as z:
            for count, ident in enumerate(sorted(required), 1):
                clip = clips[ident]
                row = rows.get(ident)
                if row is None or row['voice_status'] == 'voiced' or row['fingerprint'] != required[ident]:
                    raise ValueError(f'第 {count} 句的索引或原声状态已改变')
                if clip.text != official_text(row['raw_text']):
                    raise ValueError(f'第 {count} 句含个性化称呼或改词。请先用「漂泊者」补录这一句')
                if clip.path.stat().st_size > MAX_AUDIO:
                    raise ValueError(f'第 {count} 句文件过大')
                source = clip.path.read_bytes()
                if hexdigest(source) != clip.sha256:
                    raise ValueError(f'第 {count} 句的本机录音校验失败')
                data = clean_wav(source)
                sha = hexdigest(data)
                line = dict(((k, row[k]) for k in FIELDS), sha256=sha)
                public_line(line, scope, '', required)
                lines.append(line)
                if sha not in seen:
                    if z.fp.tell() + len(data) > MAX_PACK:
                        raise ValueError('任务包超过 256 MiB')
                    z.writestr(member('audio/' + sha + '.wav'), data)
                    seen.add(sha)
                if count == 1 or count % 16 == 0 or count == len(required):
                    progress(f'核对本机录音 {count}/{len(required)}')
            body = dict(format=FORMAT, scope=scope, author=author, lines=lines)
            envelope = dict(body=body, public_key=key.public_key().public_bytes_raw().hex(),
                            signature=base64.b64encode(key.sign(canonical(body))).decode('ascii'))
            manifest = canonical(envelope)
            if len(manifest) > MAX_MANIFEST:
                raise ValueError('任务包元数据过大')
            z.writestr(member('submission.json'), manifest)
        if temp.stat().st_size > MAX_PACK:
            raise ValueError('任务包超过 256 MiB')
        os.replace(temp, path)
    return hexdigest(canonical([scope, lines]))


def export_local(root, scope, path, key, author, progress):
    # Only recordings owned by generation projects can enter a submission.
    from wuwa_catalog import Catalog
    from wuwa_log_generation import existing_audio
    catalog = Catalog(root)
    try:
        cfg = json.loads((root / 'config.json').read_bytes())
        audio, _, _ = existing_audio(catalog, cfg)
        rows = {r['id']: r for r in catalog.lines([scope])}
        return build_submission(path, scope, rows, {c.catalog_id: c for c in audio.values()},
                                key, fingerprints(), author, progress)
    finally:
        catalog.close()


def inspect_bundle(path, reference, titles, *, output=None):
    """Validate every byte before making a catalog entry or activating a snapshot."""
    if path.stat().st_size > MAX_PACK:
        raise ValueError('投稿超过 256 MiB')
    try:
        with ZipFile(path) as z:
            entries = z.infolist()
            names = {i.filename for i in entries}
            if (len(entries) > MAX_LINES + 1 or len(names) != len(entries)
                    or 'submission.json' not in names or sum(i.file_size for i in entries) > MAX_PACK
                    or any(i.compress_type != ZIP_STORED or i.flag_bits & 1 for i in entries)):
                raise ValueError('投稿文件数量、封装格式或大小无效')
            if z.getinfo('submission.json').file_size > MAX_MANIFEST:
                raise ValueError('投稿台词清单过大')
            raw = z.read('submission.json')
            envelope = json.loads(raw)
            body = envelope['body']
            if set(envelope) != {'public_key', 'signature', 'body'} or canonical(envelope) != raw:
                raise ValueError('投稿清单有多余字段或编码无效')
            pub = envelope['public_key']
            Ed25519PublicKey.from_public_bytes(bytes.fromhex(pub)).verify(
                base64.b64decode(envelope['signature'], validate=True), canonical(body))
            if (set(body) != {'format', 'scope', 'author', 'lines'} or body['format'] != FORMAT
                    or not re.fullmatch(r'[A-Za-z0-9_.-]{1,80}', body['author'])):
                raise ValueError('文件不是 ASGSV 社区配音包')
            scope, inputs = body['scope'], body['lines']
            expected = reference['quests'].get(scope)
            if not expected or scope not in titles or not isinstance(inputs, list) or not 0 < len(inputs) <= MAX_LINES:
                raise ValueError('任务不在当前公开支线索引中')
            rows = [public_line(i, scope, titles[scope], expected) for i in inputs]
            ids = [i['id'] for i in rows]
            if ids != sorted(expected) or len({(r['state'], r['number']) for r in rows}) != len(rows):
                raise ValueError('台词缺失、重复或换句编号有歧义')
            hashes = {r['sha256'] for r in rows}
            if names != {'submission.json', *('audio/' + sha + '.wav' for sha in hashes)}:
                raise ValueError('投稿含额外文件或缺少录音')
            if output:
                (output / 'audio').mkdir(parents=True, exist_ok=True)
            for sha in sorted(hashes):
                name = 'audio/' + sha + '.wav'
                if z.getinfo(name).file_size > MAX_AUDIO:
                    raise ValueError('单句音频过大')
                audio = z.read(name)
                if hexdigest(audio) != sha or clean_wav(audio) != audio:
                    raise ValueError('录音校验失败或含有额外的文件元数据')
                if output:
                    (output / name).write_bytes(audio)
            return dict(scope=scope, name=titles[scope], rows=rows, public_key=pub, author=body['author'],
                        content=hexdigest(canonical([scope, inputs])), sample=rows[0]['sha256'])
    except (BadZipFile, EOFError, KeyError, TypeError, InvalidSignature, UnicodeError) as exc:
        raise ValueError('投稿签名、清单或 ZIP 文件无效') from exc


def select_version(root, scope, entry=None):
    directory = root / 'updates'
    path = directory / 'community-selected.json'
    with locked(directory / 'content.lock'):
        selected = selections(root)
        if entry:
            selected[scope] = entry
        else:
            selected.pop(scope, None)
        atomic(path, canonical(selected))


def selections(root):
    path = root / 'updates/community-selected.json'
    return json.loads(path.read_bytes()) if path.exists() else {}


def install_bundle(root, path, offer):
    sha = offer['sha256']
    if path.stat().st_size != offer['size'] or digest(path) != sha or not HASH.fullmatch(sha):
        raise ValueError('社区配音包与公开目录的校验值不一致')
    store = root / 'updates'
    store.mkdir(parents=True, exist_ok=True)
    reference = fingerprints()
    with tempfile.TemporaryDirectory(dir=store) as tmp:
        stage = Path(tmp) / 'payload'
        details = inspect_bundle(path, reference, {offer['scope']: offer['name']}, output=stage)
        if (any(offer[k] != details[k] for k in ('scope','author','content','public_key','sample'))
                or offer['lines'] != len(details['rows'])):
            raise ValueError('下载到的并非所选作者的版本')
        for row in details['rows']:
            row.update(label='社区配音 · ' + offer['author'], voice='community:' + str(offer['issue']))
        (stage / 'pack.json').write_bytes(canonical(dict(format=1, mounts=reference['mounts'],
            additional_mounts=[s for s in offer['mounts'] if s != reference['mounts']], lines=details['rows'])))
        with locked(store / 'content.lock'):
            dst = store / sha
            if not dst.exists():
                os.replace(stage, dst)
            elif ((dst/'pack.json').read_bytes() != (stage/'pack.json').read_bytes()
                  or any(not (dst/'audio'/f.name).is_file() or digest(dst/'audio'/f.name) != f.stem for f in (stage/'audio').iterdir())):
                raise ValueError('本机已安装的社区配音快照受损')
            selected = selections(root)
            selected[offer['scope']] = dict(folder=sha, revision=0, community=offer['issue'], author=offer['author'])
            atomic(store/'community-selected.json', canonical(selected))
    return f'已启用 {offer["author"]} 的配音 · {offer["lines"]} 句'

