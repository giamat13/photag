"""A Windows Scheduled Task that runs "photag.exe --backup" so backups happen even when the app is closed
(this is what keeps a backup from silently "forgetting" to run, the way Windows File History sometimes does).

The task (per user, no admin rights):
  * runs every hour and at every sign-in; each run only backs up when a backup is actually due (app/backup.py)
  * StartWhenAvailable: a run missed because the PC was off or asleep happens as soon as possible afterwards
  * also runs on battery; never two at once; the running app and the task share a lock, so they never collide
  * below-normal priority + Windows background mode, and the process exits as soon as it is done (no memory stays used)
Several independent things wake the backup up, so that one failing is harmless: the task's hourly trigger, its
sign-in trigger, a HKCU "Run" entry at every sign-in, and the app itself (start-up catch-up + a check every 5 minutes).
The app (re)creates the task at start-up when backups are enabled, and removes it when they are switched off.
It is only registered from the packaged EXE; from source the tests point it at a stand-in program.
"""
import json
import os
import subprocess
import sys

NO_WIN = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0


def task_name() -> str:
    return os.environ.get("PHOTAG_BACKUP_TASK_NAME", "photag-backup")


def exe() -> str | None:
    env = os.environ.get("PHOTAG_BACKUP_TASK_EXE")        # tests
    if env:
        return env
    return sys.executable if getattr(sys, "frozen", False) else None


def supported() -> bool:
    return sys.platform == "win32" and bool(exe())


def _q(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def _ps(script: str, timeout: int = 90) -> subprocess.CompletedProcess:
    return subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
                          capture_output=True, text=True, timeout=timeout, creationflags=NO_WIN)


RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def _run_key(set_it: bool | None = None) -> bool:
    """HKCU Run entry '<exe> --backup': a second, independent wake-up at every sign-in. set_it None = just query."""
    if sys.platform != "win32":
        return False
    import winreg
    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            if set_it is True:
                winreg.SetValueEx(k, task_name(), 0, winreg.REG_SZ, f'"{exe()}" --backup')
            elif set_it is False:
                try:
                    winreg.DeleteValue(k, task_name())
                except FileNotFoundError:
                    pass
            try:
                return winreg.QueryValueEx(k, task_name())[0] == f'"{exe()}" --backup'
            except FileNotFoundError:
                return False
    except OSError:
        return False


def status() -> dict:
    out = {"supported": supported(), "registered": False, "name": task_name()}
    if not out["supported"]:
        return out
    script = f"""
$t = Get-ScheduledTask -TaskName {_q(task_name())} -ErrorAction SilentlyContinue
if (-not $t) {{ '{{"registered":false}}'; exit }}
$i = Get-ScheduledTaskInfo -TaskName {_q(task_name())}
[pscustomobject]@{{
  registered = $true; exe = $t.Actions[0].Execute; args = $t.Actions[0].Arguments
  start_when_available = [bool]$t.Settings.StartWhenAvailable; triggers = @($t.Triggers).Count; priority = [int]$t.Settings.Priority
  enabled = ($t.State -ne 'Disabled')
  last_run = $(if ($i.LastRunTime.Year -gt 2000) {{ $i.LastRunTime.ToString('s') }} else {{ $null }})
  last_result = $i.LastTaskResult
  next_run = $(if ($i.NextRunTime -and $i.NextRunTime.Year -gt 2000) {{ $i.NextRunTime.ToString('s') }} else {{ $null }})
}} | ConvertTo-Json -Compress"""
    try:
        r = _ps(script)
        out.update(json.loads(r.stdout.strip() or "{}"))
        out["run_key"] = _run_key()
    except Exception as e:
        out["error"] = str(e)[:200]
    return out


def register() -> dict:
    """Create (or replace) the task. Returns status(); raises RuntimeError with PowerShell's message on failure."""
    p = exe()
    if not supported():
        raise RuntimeError("scheduled task is only available from the packaged app")
    script = f"""
$ErrorActionPreference = 'Stop'
$a  = New-ScheduledTaskAction -Execute {_q(p)} -Argument '--backup'
$t1 = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$t2 = New-ScheduledTaskTrigger -Once -At ((Get-Date).Date.AddHours(12)) -RepetitionInterval (New-TimeSpan -Hours 1) -RepetitionDuration (New-TimeSpan -Days 3650)
$s  = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew -Priority 7 -ExecutionTimeLimit (New-TimeSpan -Hours 8)
Register-ScheduledTask -TaskName {_q(task_name())} -Action $a -Trigger $t1,$t2 -Settings $s -Force `
  -Description 'photag: automatic catalog backup. Runs even when the app is closed; only backs up when one is due.' | Out-Null"""
    r = _ps(script)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout).strip()[:300])
    _run_key(True)
    return status()


def unregister():
    if not supported():
        return
    _ps(f"Unregister-ScheduledTask -TaskName {_q(task_name())} -Confirm:$false -ErrorAction SilentlyContinue")
    _run_key(False)


def ensure(enabled: bool) -> dict:
    """Make the task match the setting (idempotent, cheap when nothing is to be done). Never raises."""
    try:
        if not supported():
            return status()
        st = status()
        if enabled:
            if (not st.get("registered") or st.get("exe") != exe() or st.get("args") != "--backup" or not st.get("start_when_available")
                    or not st.get("enabled") or st.get("priority") != 7 or not st.get("run_key")):
                return register()
        elif st.get("registered"):
            unregister()
        return status()
    except Exception as e:
        return {"supported": True, "registered": False, "error": str(e)[:200]}
