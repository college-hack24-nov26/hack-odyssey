# Launch Webots on a generated world with a scripted test, print its console, wait for it to finish.
#   powershell -File tools\webots_test.ps1 -World straight_t__mid_4wd -Test runs\test2.json -Peek 25 -Timeout 120
param(
  [Parameter(Mandatory = $true)][string]$World,
  [string]$Test = "",
  [int]$Peek = 20,          # seconds to wait before dumping the Webots console
  [int]$Timeout = 120,      # overall seconds before the process is stopped
  [string]$Mode = "realtime"
)
$root = Split-Path -Parent $PSScriptRoot
$wb = "$env:LOCALAPPDATA\Programs\Webots\msys64\mingw64\bin\webots.exe"
$wbt = if ($World.EndsWith(".wbt")) { $World } else { "$root\sim\webots\worlds\$World.wbt" }
Get-Process webots* -ErrorAction SilentlyContinue | Stop-Process -Force
if ($Test) { $env:SEWERBOT_TEST = (Resolve-Path $Test).Path } else { Remove-Item Env:SEWERBOT_TEST -ErrorAction SilentlyContinue }
$p = Start-Process -FilePath $wb -ArgumentList "--mode=$Mode", $wbt -PassThru
Start-Sleep -Seconds $Peek

Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
$wp = Get-Process webots-bin -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowHandle -ne 0 } | Select-Object -First 1
if ($wp) {
  $r = [System.Windows.Automation.AutomationElement]::FromHandle($wp.MainWindowHandle)
  foreach ($e in $r.FindAll([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.Condition]::TrueCondition)) {
    if ($e.Current.ControlType.ProgrammaticName -eq 'ControlType.Edit') {
      try { $e.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern).Current.Value; break } catch {}
    }
  }
} else { "(no Webots window - it may have finished already)" }

$deadline = (Get-Date).AddSeconds($Timeout - $Peek)
while ((Get-Process webots-bin -ErrorAction SilentlyContinue) -and ((Get-Date) -lt $deadline)) { Start-Sleep -Seconds 2 }
if (Get-Process webots-bin -ErrorAction SilentlyContinue) { "(timeout: stopping Webots)"; Get-Process webots* | Stop-Process -Force }
