"""Select the voices of today's affordable, verified lines before starting TTS."""
from datetime import date
from hashlib import sha256

from PySide6.QtCore import QLockFile

import wuwa_breeze as breeze
from wuwa_cast_review import original_problem, reviewed_roles
from wuwa_daily_cast import CastingServiceError, DEFAULT_BASE, DEFAULT_MODEL, voice_for


class DailyCasting:
    def __init__(self, catalog, remaining_units, progress):
        self.c = catalog
        self.day = date.today().isoformat()
        self.remaining = max(0, remaining_units)
        self.progress = progress
        self.lines = {}
        self.problems = {}
        self.planned_keys = set()
        self.speakers = {}
        self.base = catalog.meta('daily_cast_endpoint') or DEFAULT_BASE
        self.model = catalog.meta('daily_cast_model') or DEFAULT_MODEL

    def _prior(self, key):
        row = self.c.db.execute('SELECT state FROM breeze_calls WHERE cache_key=?',(key,)).fetchone()
        return row[0] if row else None

    def _voice(self, scope, row, members, bound):
        sid = row['speaker_id']
        guard = QLockFile(str(self.c.root/('daily-cast-'+sha256(sid.encode()).hexdigest()+'.lock')))
        guard.setStaleLockTime(0)
        if not guard.tryLock(45000):
            raise CastingServiceError('另一项任务正在选声，请稍后再执行')
        try:
            current = self.c.binding(sid)
            current_voice = current['voice'] if current else ''
            prior = self.c.db.execute('''SELECT voice,delivery FROM daily_voice_choices
                WHERE day=? AND speaker=? AND endpoint=? AND model=?''',
                (self.day,sid,self.base,self.model)).fetchone()
            if prior and 'breeze:'+prior['voice']==current_voice:
                return prior['voice'], prior['delivery']
            if current_voice and current_voice!='breeze:'+bound:
                raise ValueError('选声期间角色绑定已变化，请重新执行')
            self.progress('配音前选声 · '+row['speaker'])
            voice = voice_for(self.c,scope,row,members,day=self.day)
            saved = self.c.db.execute('''SELECT delivery FROM daily_voice_choices
                WHERE day=? AND speaker=? AND voice=? AND endpoint=? AND model=?''',
                (self.day,sid,voice,self.base,self.model)).fetchone()
            if not saved:
                raise CastingServiceError('选声配置已改变，请重新启动每日任务')
            return voice, saved[0]
        finally:
            guard.unlock()

    def consider(self, scope, row, members, body, spoken):
        """Called only for a missing line with up-to-date resource and original-voice evidence."""
        c = self.c
        sid, identity = row['speaker_id'], row['id']
        if (scope,sid) in self.problems:
            return
        binding = c.binding(sid)
        bound = binding['voice'].removeprefix('breeze:') if binding else ''
        reviewed = (reviewed_roles(c,scope) or {}).get(sid)
        if reviewed in ('pending','original') or original_problem(c,sid,bound) or (binding and not binding['voice'].startswith('breeze:')):
            self.problems[scope,sid] = '角色等待原声或人工选声'
            return
        if reviewed in ('male','female') and bound:
            preset = c.db.execute('SELECT gender FROM breeze_presets WHERE id=?',(bound,)).fetchone()
            if not preset or preset[0]!=reviewed:
                self.problems[scope,sid] = '已绑定音色与确认的性别不符'
                return
        units = 2 * len(spoken)
        def key_for(voice, direction):
            return breeze.recipe(body,voice,direction,spoken=spoken)
        if not 1<=len(spoken)<=1000:
            return
        if bound:
            direction = breeze.line_direction(c,identity,bound)
            key = key_for(bound,direction)
            prior = self._prior(key)
            if prior:
                if prior in ('ready','validated'):
                    self.lines[scope,identity] = (bound,direction,direction)
                else:
                    self.lines[scope,identity] = (bound,direction,None)
                return
        if units>self.remaining and sid not in self.speakers:
            return
        if (date.today().isoformat()!=self.day or c.meta('daily_cast_enabled')!='1'
                or (c.meta('daily_cast_endpoint') or DEFAULT_BASE)!=self.base
                or (c.meta('daily_cast_model') or DEFAULT_MODEL)!=self.model):
            raise CastingServiceError('日期或角色分析设置已变化，请重新执行计划')
        try:
            if sid not in self.speakers or self.speakers[sid][0]!=bound:
                self.speakers[sid] = self._voice(scope,row,members,bound)
        except CastingServiceError:
            raise
        except ValueError as exc:
            self.problems[scope,sid] = str(exc)
            return
        voice, delivery = self.speakers[sid]
        old = breeze.line_direction(c,identity,voice)
        base_key = key_for(voice,old)
        prior = self._prior(base_key)
        override = c.db.execute('SELECT 1 FROM meta WHERE key=?',
                                ('breeze_direction:'+identity+':'+voice,)).fetchone()
        direction = old if prior or override else old+' 本角色表达：'+delivery
        key = key_for(voice,direction)
        prior = prior or self._prior(key)
        if prior and prior not in ('ready','validated'):
            self.lines[scope,identity] = (voice,old,None)
            return
        if not prior and key not in self.planned_keys:
            if units>self.remaining:
                return
            self.remaining -= units
            self.planned_keys.add(key)
        self.lines[scope,identity] = (voice,old,direction)

    def activate(self, scope, row, body, spoken):
        """Apply the prepared delivery only at export, preserving overrides and reservations."""
        c = self.c
        entry = self.lines.get((scope,row['id']))
        if not entry:
            return '',self.problems.get((scope,row['speaker_id']),'等待次日预算')
        voice, old, direction = entry
        if direction is None:
            return '', '云端历史待核对，已防止重复扣费'
        with c.db:
            c.db.execute('BEGIN IMMEDIATE')
            binding = c.binding(row['speaker_id'])
            if not binding or binding['voice']!='breeze:'+voice:
                return '', '角色绑定已变化，请重新执行'
            if date.today().isoformat()!=self.day or c.meta('daily_cast_enabled')!='1':
                raise CastingServiceError('日期或角色分析设置已变化，请重新执行计划')
            current = breeze.line_direction(c,row['id'],voice)
            if current not in (old,direction):
                return '', '台词的演绎设置已变化，请重新执行'
            if current!=direction:
                prior = self._prior(breeze.recipe(body,voice,current,spoken=spoken))
                if prior:
                    if prior not in ('validated','ready'):
                        return '', '云端历史待核对，已防止重复扣费'
                    return voice,''
                c.db.execute('INSERT OR IGNORE INTO meta VALUES(?,?)',
                             ('breeze_direction:'+row['id']+':'+voice,direction))
                if breeze.line_direction(c,row['id'],voice)!=direction:
                    return '', '台词已有指定的演绎设置，请重新执行'
        return voice,''
