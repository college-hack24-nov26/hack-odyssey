# Launch Webots with a scripted test, optionally toggle View > Optional Rendering items, and capture ONLY the Webots window.
#   powershell -File tools\webots_shot.ps1 -World straight_t__mid_4wd -Test runs\test4.json -Out shot.png -Toggle "Show All Bounding Objects","Show Contact Points" -Wait 10
param(
  [Parameter(Mandatory = $true)][string]$World,
  [Parameter(Mandatory = $true)][string]$Out,
  [string]$Test = "",
  [string[]]$Toggle = @(),
  [int]$Wait = 10
)
$root = Split-Path -Parent $PSScriptRoot
$wb = "$env:LOCALAPPDATA\Programs\Webots\msys64\mingw64\bin\webots.exe"
$wbt = if ($World.EndsWith(".wbt")) { $World } else { "$root\sim\webots\worlds\$World.wbt" }
Get-Process webots* -ErrorAction SilentlyContinue | Stop-Process -Force
if ($Test) { $env:SEWERBOT_TEST = (Resolve-Path $Test).Path } else { Remove-Item Env:SEWERBOT_TEST -ErrorAction SilentlyContinue }
Start-Process -FilePath $wb -ArgumentList "--mode=realtime", $wbt | Out-Null
Start-Sleep -Seconds 12

Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes, System.Drawing
Add-Type @"
using System; using System.Runtime.InteropServices;
public class WS { [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r); [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr h, IntPtr hdc, uint f);
 [StructLayout(LayoutKind.Sequential)] public struct RECT { public int L,T,R,B; } }
"@
$wp = Get-Process webots-bin | Where-Object { $_.MainWindowHandle -ne 0 } | Select-Object -First 1
$pidCond = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ProcessIdProperty, $wp.Id)
function Menu-Items {
  $o = @()
  foreach ($w in [System.Windows.Automation.AutomationElement]::RootElement.FindAll([System.Windows.Automation.TreeScope]::Children, $pidCond)) {
    foreach ($e in $w.FindAll([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.Condition]::TrueCondition)) {
      if ($e.Current.ControlType.ProgrammaticName -eq 'ControlType.MenuItem' -and $e.Current.Name) { $o += ,$e }
    }
  }
  $o
}
function Pick($name) { Menu-Items | Where-Object { $_.Current.Name -eq $name } | Select-Object -First 1 }
function Open($el) {
  try { $el.GetCurrentPattern([System.Windows.Automation.ExpandCollapsePattern]::Pattern).Expand() }
  catch { $el.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke() }
}
$Toggle = @($Toggle | ForEach-Object { $_ -split '\|' } | Where-Object { $_ })   # -File mode joins arrays; use "a|b"
foreach ($t in $Toggle) {
  Open (Pick "View"); Start-Sleep -Milliseconds 500
  Open (Pick "Optional Rendering"); Start-Sleep -Milliseconds 500
  $item = Pick $t
  if ($item) { $item.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke(); "toggled: $t" } else { "NOT FOUND: $t" }
  Start-Sleep -Milliseconds 500
}
Start-Sleep -Seconds $Wait
$r = New-Object WS+RECT; [WS]::GetWindowRect($wp.MainWindowHandle, [ref]$r) | Out-Null
$w = $r.R - $r.L; $h = $r.B - $r.T
$bmp = New-Object System.Drawing.Bitmap $w, $h; $g = [System.Drawing.Graphics]::FromImage($bmp); $hdc = $g.GetHdc()
[WS]::PrintWindow($wp.MainWindowHandle, $hdc, 2) | Out-Null; $g.ReleaseHdc($hdc); $g.Dispose()
$bmp.Save($Out); $bmp.Dispose(); "saved $Out (${w}x${h})"
Get-Process webots* -ErrorAction SilentlyContinue | Stop-Process -Force
