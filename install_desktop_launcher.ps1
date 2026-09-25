param([Parameter(Mandatory=$true)][string]$PythonExe)
$ErrorActionPreference='Stop'
$exe=(Resolve-Path -LiteralPath $PythonExe).Path
if ([IO.Path]::GetFileName($exe) -ne 'pythonw.exe') { throw 'Select the installed pythonw.exe' }
$worker=(Resolve-Path -LiteralPath (Join-Path $PSScriptRoot 'desktop_launcher.py')).Path
$taskName='NectarDock Desktop Launcher'
$sid=[System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$queue=Join-Path $PSScriptRoot 'Desktop-Launch-Requests'
New-Item -ItemType Directory -Path $queue -Force | Out-Null
if ((Get-Item -LiteralPath $queue).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Redirected launcher queue' }
& icacls.exe $queue /inheritance:r /grant:r "*${sid}:(OI)(CI)F" '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Cannot protect launcher queue' }
$allowedSids=@($sid,'S-1-5-18','S-1-5-32-544')
foreach ($rule in (Get-Acl -LiteralPath $queue).Access) {
    $ruleSid=$rule.IdentityReference.Translate([System.Security.Principal.SecurityIdentifier]).Value
    if ($rule.AccessControlType -eq 'Allow' -and $ruleSid -notin $allowedSids) {
        throw 'Launcher queue grants another identity access. Review its ACL before installation.'
    }
}
$service=New-Object -ComObject Schedule.Service
$service.Connect()
$folder=$service.GetFolder('\')
$existing=$null
try { $existing=$folder.GetTask($taskName) } catch { if ($_.Exception.HResult -ne -2147024894) { throw } }
if ($existing -and $existing.Definition.RegistrationInfo.Source -ne 'NectarDock same-user desktop launcher v1') { throw 'Existing task is not owned by this launcher' }
$definition=$service.NewTask(0)
$definition.RegistrationInfo.Description='Explicit NectarDock desktop launches independent of AI host lifetime. No automatic triggers or elevation.'
$definition.RegistrationInfo.Source='NectarDock same-user desktop launcher v1'
$definition.Principal.UserId=$sid
$definition.Principal.LogonType=3
$definition.Principal.RunLevel=0
$definition.Settings.Enabled=$true
$definition.Settings.AllowDemandStart=$true
$definition.Settings.DisallowStartIfOnBatteries=$false
$definition.Settings.StopIfGoingOnBatteries=$false
$definition.Settings.ExecutionTimeLimit='PT0S'
$definition.Settings.MultipleInstances=0
$action=$definition.Actions.Create(0)
$action.Path=$exe
$action.Arguments='-B "'+$worker+'" --serve "$(Arg0)"'
$action.WorkingDirectory=$PSScriptRoot
$registered=$folder.RegisterTaskDefinition($taskName,$definition,6,$sid,$null,3,$null)
[pscustomobject]@{Task=$registered.Name;User=$registered.Definition.Principal.UserId;RunLevel=$registered.Definition.Principal.RunLevel;LogonType=$registered.Definition.Principal.LogonType;Action=$registered.Definition.Actions.Item(1).Path;Arguments=$registered.Definition.Actions.Item(1).Arguments;Queue=$queue}|ConvertTo-Json
