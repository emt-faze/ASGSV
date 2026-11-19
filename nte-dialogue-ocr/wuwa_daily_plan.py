"""Standalone daily plan. The CLI and desktop dialog share the same SQLite ledger."""
import argparse
from collections import Counter
from datetime import datetime, date, timedelta
import json
from pathlib import Path
from threading import Event, Lock
from time import monotonic

from PySide6.QtCore import QLockFile
from dialogue_generation import file_digest
from wuwa_catalog import Catalog, resolve_text
from wuwa_daily_scan import scan, matched_lines
from wuwa_daily_cast import voice_for
from wuwa_resources import inventory
from wuwa_log_generation import export_cloud
import wuwa_breeze as breeze


def initialize(c):
    c.db.executescript('''
        CREATE TABLE IF NOT EXISTS daily_plan(
            account TEXT NOT NULL, scope TEXT NOT NULL, name TEXT NOT NULL, status TEXT NOT NULL,
            PRIMARY KEY(account,scope));
        CREATE TABLE IF NOT EXISTS daily_discovered(
            account TEXT NOT NULL, scope TEXT NOT NULL, name TEXT NOT NULL, evidence TEXT NOT NULL,
            PRIMARY KEY(account,scope));
        CREATE TABLE IF NOT EXISTS daily_profiles(speaker TEXT PRIMARY KEY,signature TEXT,profile TEXT);
    ''')


def save_meta(c, **values):
    with c.db:
        c.db.executemany('INSERT OR REPLACE INTO meta VALUES(?,?)', values.items())


def config(c):
    path = c.root/'config.json'
    if not path.is_file():
        raise ValueError('请先在实时配音页运行「一键配置并开始」，保存鸣潮安装路径。')
    return json.loads(path.read_text(encoding='utf-8-sig'))


def record_scan(c, account, items):
    initialize(c)
    with c.db:
        c.db.execute('DELETE FROM daily_discovered WHERE account=?',(account,))
        c.db.executemany('INSERT INTO daily_discovered VALUES(?,?,?,?)',
                         [(account,scope,name,reason) for scope,name,reason in items])
        c.db.execute("INSERT OR REPLACE INTO meta VALUES('daily_scan_revision',?)",(datetime.now().isoformat(timespec='microseconds'),))
        c.db.execute("INSERT OR REPLACE INTO meta VALUES('daily_scan_account',?)",(account,))
        c.db.execute("INSERT OR REPLACE INTO meta VALUES('daily_account',?)",(account,))
        c.db.execute('UPDATE daily_plan SET name=coalesce((SELECT name FROM scopes WHERE id=daily_plan.scope),name) WHERE account=?',(account,))


def enqueue(c, account, items):
    with c.db:
        added = c.db.executemany("INSERT OR IGNORE INTO daily_plan VALUES(?,?,?,'等待执行')",
                         [(account, scope, name) for scope,name in items])
        if added.rowcount:
            c.db.execute("DELETE FROM meta WHERE key='daily_finished_slot'")
    return added.rowcount


def today_units(c):
    return c.db.execute('SELECT coalesce(sum(units),0) FROM breeze_calls WHERE day=?',
                        (date.today().isoformat(),)).fetchone()[0]


def run(c, *, scheduled=False, dry=False):
    initialize(c)
    guard = QLockFile(str(c.root/'daily-plan.lock'))
    guard.setStaleLockTime(0)
    if not guard.tryLock(0):
        return '每日计划正在另一进程执行'
    try:
        return _run(c, scheduled, dry)
    except Exception as exc:
        if not dry:
            save_meta(c,daily_result='暂停 · '+str(exc),daily_updated=datetime.now().isoformat(timespec='seconds'))
        raise
    finally:
        guard.unlock()


def due_slot(now, at, since, finished):
    slot = datetime.combine(now.date(), datetime.strptime(at,'%H:%M').time())
    if slot > now:
        slot -= timedelta(days=1)
    if (since and slot < datetime.fromisoformat(since)) or slot.isoformat()==finished:
        return None
    return slot.isoformat()


def _run(c, scheduled, dry):
    now = datetime.now()
    today = now.date().isoformat()
    if scheduled:
        if c.meta('daily_enabled') != '1':
            return '计划已关闭'
        slot = due_slot(now,c.meta('daily_time','09:00'),c.meta('daily_since'),c.meta('daily_finished_slot'))
        if not slot:
            return '等待下次计划'
    cfg = config(c)
    account, candidates = scan(c,cfg)
    if not dry:
        record_scan(c,account,candidates)
    active = {scope for scope,_,_ in candidates}
    queued = c.db.execute('SELECT scope,name FROM daily_plan WHERE account=? ORDER BY rowid',(account,)).fetchall()
    if dry:
        return f'扫描确认 {len(active)} 项 · 当前账号计划 {len(queued)} 项 · 今日账本 {today_units(c)/10:g} 积分'
    def checkpoint(message):
        save_meta(c,daily_result=message,daily_updated=datetime.now().isoformat(timespec='seconds'))
    if not queued:
        checkpoint('当前账号的计划为空')
        return '当前账号的计划为空'
    inv = inventory(cfg['install_root'], c.root)
    verified = matched_lines(inv)
    if not verified:
        raise ValueError('当前游戏挂载版本尚无匹配的支线指纹，更新工作台的任务索引包后重试。')
    if not c.cloud_only():
        raise ValueError('请在每日计划的「配音与预算」中将配音引擎切换为 BreezeBlue。')
    if not c.branch_rule_enabled():
        raise ValueError('请在每日计划的「配音与预算」中启用普通支线配音规则。')
    if not breeze.api_key():
        raise ValueError('请在每日计划的「配音与预算」中保存 BreezeBlue API Key。')
    if not c.db.execute('SELECT 1 FROM breeze_presets LIMIT 1').fetchone():
        breeze.refresh_presets(c)
    def status(scope, msg):
        with c.db:
            c.db.execute('UPDATE daily_plan SET status=? WHERE account=? AND scope=?',(msg,account,scope))
    def enrolled(scope):
        return c.db.execute('SELECT 1 FROM daily_plan WHERE account=? AND scope=?',(account,scope)).fetchone()
    from quest_library import playable, prepare_cast
    audio,_ = playable(c,cfg)
    version = c.local_version()
    limit = min(400,int(c.meta('breeze_daily_credits','0'))) * 10
    made = 0
    deferred = False
    scanned_at = monotonic()
    if limit <= 0:
        raise ValueError('每日预算为 0，请在每日计划的「配音与预算」中填写当日积分上限。')
    for scope,name in queued:
        if not enrolled(scope):
            continue
        if scope not in active:
            status(scope,'本次未检测到未完成证据，已暂停')
            continue
        rows = c.lines([scope])
        approved = verified.get(scope,{})
        pending = [r for r in rows if r['id'] in approved and r['id'] not in audio]
        ready = sum(i in audio for i in approved)
        if not approved:
            status(scope,'该任务在当前资源中没有可配音台词')
            continue
        counts = Counter()
        missing = len(approved.keys() - {r['id'] for r in rows} - audio.keys())
        if missing:
            counts['台词索引尚未覆盖'] = missing
        # Bind a character once, then reuse that decision for all of their lines.
        voices = {}
        checkpoint('处理中 · '+name)
        for row in pending:
            if monotonic()-scanned_at >= 60:
                active_account, latest = scan(c,cfg)
                if active_account != account:
                    raise ValueError('游戏账号已切换，计划已暂停')
                active = {s for s,_,_ in latest}
                scanned_at = monotonic()
            if scope not in active:
                counts['任务进度已变化，停止补录'] += 1
                break
            if not enrolled(scope) or (scheduled and c.meta('daily_enabled')!='1'):
                return '计划已修改，本次运行结束'
            if date.today().isoformat()!=today:
                return '日期已切换，下次运行继续'
            identity,sid = row['id'],row['speaker_id']
            if row['fingerprint']!=approved[identity] or not c.voice_basis(row,version,allow_branch=True):
                counts['原声或版本待核实'] += 1
                continue
            body, unresolved = resolve_text(row['raw_text'], cfg.get('nickname','漂泊者'), cfg.get('traveler_gender','female'))
            spoken = breeze.spoken_text(body,c.root)
            if unresolved or not any(ch.isalnum() for ch in spoken):
                counts['台词无可合成正文或含未知变量'] += 1
                continue
            if sid not in voices and not c.binding(sid) and today_units(c)+2*len(spoken)>limit:
                counts['等待次日预算'] += 1
                continue
            if sid not in voices:
                try:
                    voices[sid] = (voice_for(c,scope,row,rows), '')
                except ValueError as exc:
                    if '服务' in str(exc) or 'API Key' in str(exc):
                        raise
                    voices[sid] = ('',str(exc))
            vid,problem = voices[sid]
            if problem:
                counts[row['speaker']+' · '+problem] += 1
                continue
            recipe = breeze.recipe(body,vid,breeze.line_direction(c,identity,vid),spoken=spoken)
            prior = c.db.execute('SELECT state FROM breeze_calls WHERE cache_key=?',(recipe,)).fetchone()
            if prior and prior[0]=='submitted':
                counts['云端历史待核对，已防止重复扣费'] += 1
                continue
            if not prior and today_units(c)+2*len(spoken)>limit:
                counts['等待次日预算'] += 1
                continue
            prepare_cast(c,scope,[row])
            try:
                project,_ = c.prepare_project(['flow:'+row['flow_id']],cfg,{identity},live_line=True)
                entry, = project.rows
                if entry.source_id!=identity or entry.voice!='breeze:'+vid or entry.text!=body:
                    raise ValueError('角色绑定或台词已更新')
                project_guard = QLockFile(str(project.directory/'project.lock'))
                project_guard.setStaleLockTime(0)
                if not project_guard.tryLock(0):
                    counts['另一窗口正在生成'] += 1
                    continue
                try:
                    def current():
                        binding = c.binding(sid)
                        return (enrolled(scope) and (not scheduled or c.meta('daily_enabled')=='1')
                            and date.today().isoformat()==today and binding and binding['voice']==entry.voice
                            and all(file_digest(m['path'])==m['sha256'] for m in inv['mounts']))
                    clip = export_cloud(c,project,entry,vid,current,Event(),Lock(),max_daily_credits=400)
                finally:
                    project_guard.unlock()
                audio[identity]=clip
                made+=1
                ready+=1
                status(scope,f'已就绪 {ready}/{len(approved)} 句')
                checkpoint(f'{name} · 已就绪 {ready}/{len(approved)} 句')
            except (ValueError,OSError) as exc:
                msg=str(exc)
                if any(word in msg for word in ('HTTP 401','HTTP 402','HTTP 403','HTTP 429','timed out','urlopen error','WinError')):
                    raise ValueError('云端连接或账户需要处理 · '+msg) from None
                counts[msg] += 1
        details = '；'.join(f'{why} {n} 句' for why,n in counts.items())
        deferred |= bool(counts)
        status(scope,f'已就绪 {ready}/{len(approved)} 句'+(' · '+details if details else ''))
    summary=f'本次补齐 {made} 句 · 今日共预留 {today_units(c)/10:g}/{limit/10:g} 积分'
    checkpoint(summary)
    if scheduled and not deferred:
        save_meta(c,daily_finished_slot=slot)
    return summary


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--root',required=True,type=Path)
    parser.add_argument('--scheduled',action='store_true')
    parser.add_argument('--dry-run',action='store_true')
    args=parser.parse_args()
    catalog=Catalog(args.root.resolve())
    try:
        result=run(catalog,scheduled=args.scheduled,dry=args.dry_run)
        if __import__('sys').stdout:
            print(result)
        return 0
    except Exception as exc:
        if __import__('sys').stderr:
            print(str(exc),file=__import__('sys').stderr)
        return 1
    finally:
        catalog.close()


if __name__=='__main__':
    raise SystemExit(main())

