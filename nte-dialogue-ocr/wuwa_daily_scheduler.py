"""Register the standalone worker for the current interactive Windows account."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys


def task_name(root):
    suffix = hashlib.sha256(str(Path(root).resolve()).casefold().encode()).hexdigest()[:12]
    return 'ASGSV-Daily-Dubbing-' + suffix


def python_path():
    embedded = Path(__file__).resolve().parents[1]/'runtime/python/pythonw.exe'
    if embedded.is_file():
        return embedded
    current = Path(sys.executable).resolve()
    windowless = current.with_name('pythonw.exe')
    if windowless.is_file():
        return windowless
    return current


def manage(root, action, at='09:00'):
    if os.name!='nt':
        raise ValueError('每日后台执行需要 Windows 任务计划程序。')
    if action not in ('register','remove','query') or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d',at):
        raise ValueError('无效的调度操作或执行时间。')
    work = Path(__file__).resolve().parent
    args = subprocess.list2cmdline(['-X','utf8',str(work/'wuwa_daily_plan.py'),'--root',str(Path(root).resolve()),'--scheduled'])
    environ = dict(os.environ, ASGSV_TASK=task_name(root), ASGSV_EXE=str(python_path()), ASGSV_ARGS=args,
                   ASGSV_WORK=str(work), ASGSV_AT=at, ASGSV_ACTION=action)
    code = r'''
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)
$name=$env:ASGSV_TASK
if ($env:ASGSV_ACTION -eq 'register') {
  $user=[Security.Principal.WindowsIdentity]::GetCurrent().Name
  $act=New-ScheduledTaskAction -Execute $env:ASGSV_EXE -Argument $env:ASGSV_ARGS -WorkingDirectory $env:ASGSV_WORK
  $at=[DateTime]::ParseExact($env:ASGSV_AT,'HH:mm',[cultureinfo]::InvariantCulture)
  $triggers=@((New-ScheduledTaskTrigger -Daily -At $at),(New-ScheduledTaskTrigger -AtLogOn -User $user))
  $opts=New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -RestartCount 2 -RestartInterval (New-TimeSpan -Minutes 15)
  $principal=New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
  Register-ScheduledTask -TaskName $name -Action $act -Trigger $triggers -Settings $opts -Principal $principal -Force | Out-Null
}
$task=Get-ScheduledTask | Where-Object { $_.TaskName -eq $name -and $_.TaskPath -eq '\' } | Select-Object -First 1
if ($task -and $env:ASGSV_ACTION -eq 'remove') {
  Unregister-ScheduledTask -TaskName $name -Confirm:$false
  $task=$null
}
if ($task) {
  $info=Get-ScheduledTaskInfo -InputObject $task
  @{registered=$true;state=[string]$task.State;next=$info.NextRunTime.ToString('yyyy-MM-dd HH:mm')} | ConvertTo-Json -Compress
} else { '{"registered":false}' }
'''
    powershell=Path(os.environ['SystemRoot'])/'System32/WindowsPowerShell/v1.0/powershell.exe'
    proc=subprocess.run([str(powershell),'-NoLogo','-NoProfile','-NonInteractive','-Command',code],
        capture_output=True,encoding='utf8',errors='replace',env=environ,timeout=45,
        creationflags=subprocess.CREATE_NO_WINDOW)
    if proc.returncode:
        detail=next((s.strip() for s in proc.stderr.splitlines() if s.strip()),'')
        raise ValueError('Windows 任务计划程序操作失败 · '+detail[:220])
    return json.loads(proc.stdout.strip())

