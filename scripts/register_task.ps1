param(
    [string]$ProjectPath = (Split-Path -Parent $PSScriptRoot),
    [string]$TaskName = 'OrdersDataPipeline'
)
$ErrorActionPreference = 'Stop'
$scriptPath = Join-Path $ProjectPath 'scripts\run_pipeline.ps1'
$arguments = '-NoProfile -ExecutionPolicy Bypass -File "{0}" -ProjectPath "{1}"' -f $scriptPath, $ProjectPath
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $arguments -WorkingDirectory $ProjectPath
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 15)
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 14)
# Interactive logon matches a local Docker Desktop session; no stored Windows password.
$principal = New-ScheduledTaskPrincipal -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal
