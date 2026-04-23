param(
    [int]$IntervalMinutes = 1
)

$ErrorActionPreference = "Stop"

$RootDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ConfigPath = Join-Path $RootDir "config.json"
$TaskName = "HermesCPR"

if (-not (Test-Path $ConfigPath)) {
    Write-Error "config.json not found in $RootDir. Copy config.example.json to config.json first."
}

$PyCommand = Get-Command py -ErrorAction SilentlyContinue
if ($PyCommand) {
    $Execute = $PyCommand.Source
    $Arguments = "-3 `"$RootDir\hermes_cpr.py`" --config `"$ConfigPath`""
} else {
    $PythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if (-not $PythonCommand) {
        Write-Error "Neither 'py' nor 'python' was found in PATH."
    }
    $Execute = $PythonCommand.Source
    $Arguments = "`"$RootDir\hermes_cpr.py`" --config `"$ConfigPath`""
}

$Action = New-ScheduledTaskAction -Execute $Execute -Argument $Arguments -WorkingDirectory $RootDir
$Trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).Date
$Trigger.Repetition = New-ScheduledTaskRepetitionSettingsSet -Interval (New-TimeSpan -Minutes $IntervalMinutes) -Duration ([TimeSpan]::MaxValue)
$Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable

Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName

Write-Output "Installed scheduled task: $TaskName"
Write-Output "Interval: $IntervalMinutes minute(s)"
Write-Output "Config: $ConfigPath"
