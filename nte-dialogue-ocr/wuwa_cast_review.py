"""User-confirmed principal voice genders, scoped to a WuWa quest."""
import json


def reviewed_roles(catalog, quest):
    record = catalog.db.execute('SELECT roles FROM quest_cast_reviews WHERE quest_id=?',(quest,)).fetchone()
    return json.loads(record[0]) if record else None


def original_problem(catalog, speaker_id, voice_id):
    source = catalog.db.execute('SELECT cloud_voice_id FROM original_voice_roles WHERE speaker_id=?',(speaker_id,)).fetchone()
    if source is not None and (not source[0] or source[0] != voice_id):
        local = catalog.db.execute('SELECT profile FROM original_reference_cast WHERE speaker_id=?',(speaker_id,)).fetchone()
        if local and voice_id == 'character:'+local[0]:
            return ''
        return '该角色使用游戏原声，等待核实可用的原声音色'
    return ''


def review_problem(catalog, quest, speaker_id=None, voice_id=None):
    if catalog.meta('require_quest_cast_review') != '1':
        return ''
    roles = reviewed_roles(catalog, quest)
    if roles is None:
        return '请在任务库确认本任务的主要人物声线'
    if speaker_id is None:
        return ''
    original = original_problem(catalog, speaker_id, voice_id)
    if original:
        return original
    for sid, gender in roles.items():
        if speaker_id is not None and sid != speaker_id:
            continue
        if gender in ('any','original'):
            continue
        if gender == 'pending':
            return '该角色的声线还没有确认'
        if voice_id is None:
            from wuwa_cast_context import for_speaker
            speaker = catalog.db.execute('SELECT speaker FROM lines WHERE speaker_id=? LIMIT 1',(sid,)).fetchone()[0]
            _, age, _ = for_speaker(catalog,sid,speaker,quest)
            preset = catalog.db.execute("""SELECT p.gender FROM breeze_cast c JOIN breeze_presets p ON p.id=c.voice_id
                                           WHERE c.speaker_id=?""",(sid,)).fetchone()
        else:
            from wuwa_cast_context import for_speaker
            speaker = catalog.db.execute('SELECT speaker FROM lines WHERE speaker_id=? LIMIT 1',(sid,)).fetchone()[0]
            _, age, _ = for_speaker(catalog,sid,speaker,quest)
            preset = catalog.db.execute('SELECT gender FROM breeze_presets WHERE id=?',(voice_id,)).fetchone()
        if not preset or preset[0] != gender:
            return '当前音色与已确认的主要人物声线不符，请在任务库核对'
    return ''


def line_problem(catalog, line_id, speaker_id, voice_id):
    if catalog.meta('require_quest_cast_review') != '1':
        return ''
    original = original_problem(catalog,speaker_id,voice_id)
    if original:
        return original
    for (quest,) in catalog.db.execute("SELECT scope_id FROM scope_lines WHERE line_id=? AND scope_id LIKE 'quest:%'", (line_id,)):
        problem = review_problem(catalog, quest, speaker_id, voice_id)
        if problem:
            return problem
    return ''


def save_review(catalog, quest, roles):
    members = {r['speaker_id'] for r in catalog.lines([quest])}
    if not members or not roles.keys() <= members or any(g not in ('male','female','any','original','pending') for g in roles.values()):
        raise ValueError('角色或声线类别与该任务不符')
    with catalog.db:
        for sid, gender in roles.items():
            if gender == 'original':
                catalog.db.execute('INSERT OR IGNORE INTO original_voice_roles VALUES(?,NULL)',(sid,))
                continue
            if gender in ('any','pending'):
                continue
            current = catalog.db.execute("""SELECT p.gender,p.age FROM breeze_cast c JOIN breeze_presets p
                                            ON p.id=c.voice_id WHERE c.speaker_id=?""",(sid,)).fetchone()
            if current and current['gender'] == gender:
                continue
            from wuwa_cast_context import for_speaker
            speaker = catalog.db.execute('SELECT speaker FROM lines WHERE speaker_id=? LIMIT 1',(sid,)).fetchone()[0]
            _, age, _ = for_speaker(catalog,sid,speaker,quest)
            preset = catalog.db.execute("""SELECT id FROM breeze_presets WHERE gender=?
                                           ORDER BY CASE WHEN age=? THEN 0 ELSE 1 END, name,id LIMIT 1""",
                                        (gender,age or (current['age'] if current else 'young'))).fetchone()
            if not preset:
                raise ValueError('云端音色库缺少所选类别，请先同步音色')
            catalog.db.execute('INSERT OR REPLACE INTO breeze_cast VALUES(?,?)',(sid,preset[0]))
        catalog.db.execute('INSERT OR REPLACE INTO quest_cast_reviews VALUES(?,?)',
                           (quest,json.dumps(roles,sort_keys=True)))
