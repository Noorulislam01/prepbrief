$ErrorActionPreference = "Stop"
$TaskName = "PrepBrief-RSS-Fetch"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Runner = Join-Path $PSScriptRoot "run_rss_fetch.ps1"
$PowerShell = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"

if (-not (Test-Path -LiteralPath (Join-Path $ProjectRoot ".venv\Scripts\python.exe"))) {
    throw "PrepBrief virtual environment was not found. Set up .venv and install requirements before scheduling RSS."
}

$EscapeXml = { param([string]$Value) [System.Security.SecurityElement]::Escape($Value) }
$StartBoundary = (Get-Date -Hour 0 -Minute 0 -Second 0).ToString("yyyy-MM-ddTHH:mm:sszzz")
$UserId = & $EscapeXml "$env:USERDOMAIN\$env:USERNAME"
$PowerShellXml = & $EscapeXml $PowerShell
$RunnerXml = & $EscapeXml $Runner
$RootXml = & $EscapeXml $ProjectRoot
$TaskXml = @"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>Fetch PrepBrief RSS headlines hourly while this Windows account is signed in.</Description></RegistrationInfo>
  <Triggers><CalendarTrigger><Repetition><Interval>PT1H</Interval><Duration>P1D</Duration><StopAtDurationEnd>false</StopAtDurationEnd></Repetition><StartBoundary>$StartBoundary</StartBoundary><Enabled>true</Enabled><ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay></CalendarTrigger></Triggers>
  <Principals><Principal id="Author"><UserId>$UserId</UserId><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals>
  <Settings><MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy><DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries><StopIfGoingOnBatteries>false</StopIfGoingOnBatteries><StartWhenAvailable>true</StartWhenAvailable><ExecutionTimeLimit>PT20M</ExecutionTimeLimit><Enabled>true</Enabled></Settings>
  <Actions Context="Author"><Exec><Command>$PowerShellXml</Command><Arguments>-NoProfile -NonInteractive -ExecutionPolicy Bypass -File &quot;$RunnerXml&quot;</Arguments><WorkingDirectory>$RootXml</WorkingDirectory></Exec></Actions>
</Task>
"@
$XmlPath = Join-Path $env:TEMP "$TaskName.xml"
Set-Content -LiteralPath $XmlPath -Value $TaskXml -Encoding Unicode
& schtasks.exe /Create /TN $TaskName /XML $XmlPath /F
if ($LASTEXITCODE -ne 0) {
    throw "Task Scheduler could not register the hourly RSS task (exit code $LASTEXITCODE)."
}
Remove-Item -LiteralPath $XmlPath -Force
Write-Host "Scheduled '$TaskName' to fetch RSS hourly while you are signed in."
Write-Host "Log: $(Join-Path $ProjectRoot '.logs\rss-fetch.log')"
Write-Host "To remove it: Unregister-ScheduledTask -TaskName '$TaskName' -Confirm:`$false"
