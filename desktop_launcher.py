"""Allowlisted same-user desktop launches, brokered by Windows Task Scheduler.

No arbitrary executable, environment, elevation, transfer or termination requests.
"""
import argparse
import base64
import ctypes
from ctypes import wintypes as w
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

BASE=Path(__file__).resolve().parent
# Beside the installed source: packaged AI hosts may virtualize LocalAppData,
# making that apparent path different from the desktop worker's filesystem view.
QUEUE=BASE/'Desktop-Launch-Requests'
TASK='NectarDock Desktop Launcher'
_verified_worker=False


def require(ok,message):
    if not ok:raise RuntimeError(message)


def in_job(pid=None):
    k=ctypes.WinDLL('kernel32',use_last_error=True)
    k.GetCurrentProcess.restype=w.HANDLE
    k.OpenProcess.argtypes=[w.DWORD,w.BOOL,w.DWORD];k.OpenProcess.restype=w.HANDLE
    k.IsProcessInJob.argtypes=[w.HANDLE,w.HANDLE,ctypes.POINTER(w.BOOL)]
    k.CloseHandle.argtypes=[w.HANDLE]
    handle=k.OpenProcess(0x400,False,pid) if pid is not None else k.GetCurrentProcess()
    try:
        result=w.BOOL()
        require(handle and k.IsProcessInJob(handle,None,ctypes.byref(result)),
                'Process lifetime diagnostic unavailable; no desktop launch')
        return bool(result.value)
    finally:
        if pid is not None and handle:k.CloseHandle(handle)


def validate_id(value):
    try:valid=isinstance(value,str) and str(uuid.UUID(value))==value
    except (ValueError,TypeError,AttributeError):valid=False
    require(valid,'Invalid launch request UUID')
    return value


def job_flags():
    if not in_job():return 0
    class Basic(ctypes.Structure):
        _fields_=[('per_process',ctypes.c_int64),('per_job',ctypes.c_int64),
            ('flags',w.DWORD),('min_ws',ctypes.c_size_t),('max_ws',ctypes.c_size_t),
            ('active',w.DWORD),('affinity',ctypes.c_size_t),('priority',w.DWORD),('scheduling',w.DWORD)]
    class Extended(ctypes.Structure):
        _fields_=[('basic',Basic),('io',ctypes.c_uint64*6),('process_mem',ctypes.c_size_t),
            ('job_mem',ctypes.c_size_t),('peak_process',ctypes.c_size_t),('peak_job',ctypes.c_size_t)]
    k=ctypes.WinDLL('kernel32',use_last_error=True)
    k.QueryInformationJobObject.argtypes=[w.HANDLE,ctypes.c_int,ctypes.c_void_p,w.DWORD,ctypes.POINTER(w.DWORD)]
    info=Extended();length=w.DWORD()
    require(k.QueryInformationJobObject(None,9,ctypes.byref(info),ctypes.sizeof(info),ctypes.byref(length)),
            'Windows job limits unavailable; no desktop launch')
    return info.basic.flags


def verify_worker_boundary(parent_pid,scheduler_pid,flags):
    # A zero flag alone cannot exclude an outer kill-on-close job. Require the
    # actual Schedule service as direct parent, not an environment marker.
    require(scheduler_pid and parent_pid==scheduler_pid and flags==0,
            'Desktop worker job or Task Scheduler parent is not independent; no launch')


def certify_worker():
    run=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',
        "$ErrorActionPreference='Stop'; (Get-CimInstance Win32_Service -Filter \"Name='Schedule'\").ProcessId"],
        capture_output=True,text=True,timeout=15,creationflags=0x08000000)
    require(run.returncode==0,'Task Scheduler ownership diagnostic unavailable')
    verify_worker_boundary(os.getppid(),int(run.stdout.strip()),job_flags())


def needs_broker():
    return not _verified_worker and in_job()


def process_created(pid):
    k=ctypes.WinDLL('kernel32',use_last_error=True)
    k.OpenProcess.argtypes=[w.DWORD,w.BOOL,w.DWORD];k.OpenProcess.restype=w.HANDLE
    k.GetProcessTimes.argtypes=[w.HANDLE]+[ctypes.POINTER(w.FILETIME)]*4
    k.CloseHandle.argtypes=[w.HANDLE]
    handle=k.OpenProcess(0x1000,False,pid)
    try:
        stamps=[w.FILETIME() for _ in range(4)]
        require(handle and k.GetProcessTimes(handle,*(ctypes.byref(s) for s in stamps)),
                'Process creation identity unavailable; no launch')
        return str((stamps[0].dwHighDateTime<<32)|stamps[0].dwLowDateTime)
    finally:
        if handle:k.CloseHandle(handle)


def record_code_child(pid,profile):
    if not _verified_worker:return
    created=process_created(pid)
    data={'pid':pid,'created':created,'profile':os.path.normcase(os.path.abspath(profile)),
          'boundary':'VERIFIED_SCHEDULER_CHILD'}
    path=QUEUE/f'code-{pid}-{created}.json'
    with path.open('x',encoding='utf-8') as stream:json.dump(data,stream)


def assert_existing_code_safe(pid,profile):
    if not in_job(pid):return
    created=process_created(pid)
    try:data=json.loads((QUEUE/f'code-{pid}-{created}.json').read_text(encoding='utf-8'))
    except (OSError,ValueError):data={}
    require(data=={'pid':pid,'created':created,'profile':os.path.normcase(os.path.abspath(profile)),
                   'boundary':'VERIFIED_SCHEDULER_CHILD'},
        f'The existing VS Code account process (PID {pid}) is in an unverified Windows job. '
        'It may close with its original launcher. Save work and close only this account profile, '
        'then reopen through NectarDock. No process was closed.')


def validate_request(data):
    require(isinstance(data,dict),'Invalid desktop launch request')
    kind=data.get('kind')
    fields={'ui':{'kind'},'probe':{'kind'},'workspace':{'kind','group','color','ids'},'login':{'kind','color'}}
    require(kind in fields and set(data)==fields[kind],'Unapproved launch action or fields')
    if kind in ('workspace','login'):
        import switcher as s
        require(data['color'] in s.ACCOUNTS,'Unknown account')
        if kind=='workspace':
            require(data['group'] in s.GROUPS,'Unknown workspace group')
            ids=data['ids'];require(isinstance(ids,list) and 0<len(ids)<=200,'Select 1-200 exact conversations')
            for sid in ids:validate_id(sid)
            require(len(set(ids))==len(ids),'Duplicate conversation UUID')
    return data


def perform(data):
    validate_request(data)
    kind=data['kind']
    bound=in_job()
    if kind=='probe':
        result={'state':'DESKTOP_JOB_DIAGNOSTIC' if bound else 'INDEPENDENT_LAUNCHER_VERIFIED','pid':os.getpid(),'parent_pid':os.getppid(),'in_job':bound}
        if bound:
            result['job_flags']=hex(job_flags())
        if _verified_worker:result['state']='INDEPENDENT_SCHEDULER_VERIFIED'
        return result
    require(_verified_worker or not bound,'Desktop worker is still in an unverified Windows job; no persistent app launched. Use File Explorer.')
    if kind=='ui':
        # The scheduled worker hosts Tk itself after publishing the receipt.
        # No inherited marker is used to bypass lifetime checks in a child.
        return {'state':'APP_LAUNCH_REQUESTED','pid':os.getpid(),'launcher':'WINDOWS_TASK_SCHEDULER'}
    import switcher as s
    if kind=='workspace':return s.open_workspace(data['group'],data['color'],data['ids'])
    return s.launch_login(data['color'])


def serve(rid):
    global _verified_worker
    try:validate_id(rid)
    except (ValueError,TypeError,AttributeError) as exc:raise RuntimeError('Invalid launch request UUID') from exc
    folder=QUEUE/rid
    # Same user's protected queue. Never follow a redirected request directory.
    require(not folder.is_symlink() and not (folder.stat().st_file_attributes&0x400),'Redirected request directory')
    with (folder/'claim').open('x'):pass
    try:
        path=folder/'request.json'
        stat=path.stat()
        require(stat.st_size<=65536 and not (stat.st_file_attributes&0x400),'Invalid request file')
        require(0<=time.time()-stat.st_mtime<=120,'Expired or future-dated desktop request; no launch')
        data=json.loads(path.read_text(encoding='utf-8'))
        validate_request(data)
        if in_job() or data['kind']!='probe':
            certify_worker()
            _verified_worker=True
        result=perform(data)
    except Exception as exc:result={'state':'HELD','error':str(exc)}
    result['request_id']=rid
    with (folder/'result.tmp').open('x',encoding='utf-8') as stream:
        json.dump(result,stream);stream.flush();os.fsync(stream.fileno())
    os.replace(folder/'result.tmp',folder/'result.json')
    if result.get('state')=='APP_LAUNCH_REQUESTED':
        import switcher_ui
        switcher_ui.main()
    return result


def request(kind,**fields):
    data=validate_request(dict(kind=kind,**fields))
    require(QUEUE.is_dir(),'Independent desktop launcher is not installed. Run install_desktop_launcher.ps1 or launch NectarDock yourself from File Explorer.')
    rid=str(uuid.uuid4());folder=QUEUE/rid;folder.mkdir()
    with (folder/'request.json').open('x',encoding='utf-8') as stream:json.dump(data,stream)
    def quoted(value):return "'"+str(value).replace("'","''")+"'"
    expected='-B "'+str(BASE/'desktop_launcher.py')+'" --serve "$(Arg0)"'
    script=f"""$ErrorActionPreference='Stop';$ProgressPreference='SilentlyContinue'
$service=New-Object -ComObject Schedule.Service
$service.Connect()
$task=$service.GetFolder('\\').GetTask({quoted(TASK)})
$d=$task.Definition
$sid=[System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$taskUser=$d.Principal.UserId
if ($taskUser -notlike 'S-1-*') {{$taskUser=([System.Security.Principal.NTAccount]::new($taskUser)).Translate([System.Security.Principal.SecurityIdentifier]).Value}}
if ($taskUser -ne $sid -or $d.Principal.LogonType -ne 3 -or $d.Principal.RunLevel -ne 0) {{throw 'Desktop task user or privilege mismatch'}}
if ($d.Actions.Count -ne 1 -or $d.Actions.Item(1).Path -ne {quoted(Path(sys.executable).with_name('pythonw.exe'))} -or $d.Actions.Item(1).Arguments -cne {quoted(expected)}) {{throw 'Desktop task action mismatch'}}
if ($d.Actions.Item(1).WorkingDirectory -ne {quoted(BASE)} -or $d.RegistrationInfo.Source -ne 'NectarDock same-user desktop launcher v1' -or $d.Triggers.Count -ne 0) {{throw 'Desktop task registration mismatch'}}
if ($d.Settings.MultipleInstances -ne 0 -or $d.Settings.ExecutionTimeLimit -ne 'PT0S' -or $d.Settings.StopIfGoingOnBatteries -or $d.Settings.DisallowStartIfOnBatteries) {{throw 'Desktop task lifetime settings mismatch'}}
$null=$task.Run({quoted(rid)})
"""
    def receipt():
        path=folder/'result.json'
        if not path.exists():return None
        result=json.loads(path.read_text(encoding='utf-8'))
        require(result.get('request_id')==rid,'Launcher receipt identity mismatch: '+str(folder))
        require(result.get('state')!='HELD',result.get('error','Desktop launch held'))
        return result
    uncertain='Desktop launch outcome uncertain. Check '+str(folder)+' before retrying; do not assume it failed.'
    try:
        run=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-EncodedCommand',
            base64.b64encode(script.encode('utf-16-le')).decode()],capture_output=True,text=True,
            timeout=20,creationflags=0x08000000)
    except (subprocess.TimeoutExpired,OSError) as exc:
        result=receipt()
        if result is not None:return result
        raise RuntimeError(uncertain) from exc
    if run.returncode:
        result=receipt()
        if result is not None:return result
        raise RuntimeError(uncertain+' Dispatch diagnostic: '+run.stderr.strip()[:700])
    deadline=time.monotonic()+100
    while time.monotonic()<deadline:
        result=receipt()
        if result is not None:return result
        time.sleep(0.1)
    raise RuntimeError(uncertain)


if __name__=='__main__':
    sys.modules['desktop_launcher']=sys.modules[__name__]
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',nargs='?',choices=['ui','probe'])
    parser.add_argument('--serve')
    args=parser.parse_args()
    try:
        result=serve(args.serve) if args.serve else request(args.action or 'ui')
        if sys.stdout:print(json.dumps(result))
    except Exception as exc:
        if args.serve:
            with (BASE/'desktop-worker-errors.log').open('a',encoding='utf-8') as stream:
                stream.write(json.dumps({'pid':os.getpid(),'queue':str(QUEUE),'request':args.serve,'error':str(exc)})+'\n')
        elif sys.stderr:print(str(exc),file=sys.stderr)
        else:ctypes.windll.user32.MessageBoxW(None,str(exc),'NectarDock launch held',0x10)
        raise SystemExit(2)
