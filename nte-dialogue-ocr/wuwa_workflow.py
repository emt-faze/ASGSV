"""Non-spoiler task labels; never infer missing audio is unvoiced."""

import hashlib
import json


def voice_label(binding):
    if not binding:
        return '\u7f3a\u58f0\u7ebf'
    kind, _, name = binding['voice'].partition(':')
    if kind == 'breeze':
        return 'BreezeBlue · ' + binding['label']
    label = {'npc': 'NPC \u5b9a\u5236\u58f0', 'system': '\u901a\u7528\u7cfb\u7edf\u58f0'}.get(kind, name)
    return label + ('' if binding['transcript_reviewed'] else ' \u00b7 \u5f85\u68c0\u67e5')


def project_quality(project):
    """ASR checks are tied to exact audio bytes, never a naturalness approval."""
    states = {row.id: 'unchecked' for row in project.rows}
    try:
        report = json.loads((project.directory / 'quality-report.json').read_text(encoding='utf-8'))
        if report.get('game_id') != project.game_id or not isinstance(report.get('items'), list):
            return states
        items = {}
        for item in report['items']:
            identity = item['id']
            if identity in items:
                return states
            items[identity] = item
        for row in project.rows:
            item = items.get(row.id, {})
            path = project.audio_path(row)
            if (path and item.get('expected') == row.text and item.get('sha256')
                    and hashlib.sha256(path.read_bytes()).hexdigest() == item['sha256']
                    and item.get('content_check') in ('pass', 'review')):
                states[row.id] = item['content_check']
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return {row.id: 'unchecked' for row in project.rows}
    return states


def quality_label(states):
    review = sum(state == 'review' for state in states.values())
    unchecked = sum(state == 'unchecked' for state in states.values())
    if review or unchecked:
        return f'\u8bfb\u97f3\u5f85\u6838\u5bf9 {review} \u53e5 \u00b7 \u672a\u68c0\u67e5 {unchecked} \u53e5'
    return '\u81ea\u52a8\u68c0\u67e5\u901a\u8fc7 \u00b7 \u81ea\u7136\u5ea6\u5f85\u8bd5\u542c'


def scope_voice_label(row):
    if row['total'] and row['voiced'] == row['total']:
        return '\u5df2\u6709\u539f\u58f0'
    if row['total'] and row['ready'] == row['total']:
        return '\u65e0\u539f\u914d\u97f3'
    if row['unknown']:
        return '\u5f85\u6838\u5b9e'
    if row['ready']:
        return '\u90e8\u5206\u65e0\u539f\u58f0'
    return '\u5f85\u6838\u5b9e'
