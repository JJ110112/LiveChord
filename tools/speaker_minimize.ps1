# Minimize the LiveChord speaker Edge window (speaker.bat). Edge ignores
# `start /min`, so wait for its window (up to ~15 s) and minimize it.
Add-Type @"
using System; using System.Runtime.InteropServices;
public class LcWin { [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int c); }
"@
for ($i = 0; $i -lt 30; $i++) {
  $ids = Get-CimInstance Win32_Process -Filter "Name='msedge.exe'" |
    Where-Object { $_.CommandLine -like '*LiveChordSpeaker*' -and $_.CommandLine -notlike '*--type=*' } |
    ForEach-Object { $_.ProcessId }
  foreach ($id in $ids) {
    $p = Get-Process -Id $id -ErrorAction SilentlyContinue
    if ($p -and $p.MainWindowHandle -ne 0) { [LcWin]::ShowWindow($p.MainWindowHandle, 6) | Out-Null; exit 0 }
  }
  Start-Sleep -Milliseconds 500
}
