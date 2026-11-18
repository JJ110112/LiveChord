# LiveChord PC speaker in the system tray (started by speaker.bat).
#
# Runs Edge as the sync speaker (?follow=1) in its own profile, out of sight —
# no taskbar button, no Alt-Tab entry, only a tray icon:
#   double-click / 顯示播放器   show or hide the player window
#   重新啟動喇叭                 restart Edge (e.g. after a server restart)
#   結束                         close the speaker
# If Edge exits for any other reason (window closed, crash) it is started
# again: the speaker is meant to stay up; 結束 is the way to stop it.
#
# "Out of sight" is NOT ShowWindow(SW_HIDE): Chromium never loads <audio> in
# a page that has not been visible, and every song change is a new page, so a
# hidden window goes silent. The window stays shown but fully transparent,
# click-through and a tool window (no taskbar / Alt-Tab).
param([string]$Url = "http://192.168.50.6:8800/")

Add-Type -AssemblyName System.Windows.Forms, System.Drawing
Add-Type @"
using System; using System.Runtime.InteropServices;
public class LcWin {
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int c);
  [DllImport("user32.dll")] public static extern int GetWindowLong(IntPtr h, int i);
  [DllImport("user32.dll")] public static extern int SetWindowLong(IntPtr h, int i, int v);
  [DllImport("user32.dll")] public static extern bool SetLayeredWindowAttributes(IntPtr h, uint key, byte alpha, uint flags);
  [DllImport("user32.dll")] public static extern bool SetWindowPos(IntPtr h, IntPtr after, int x, int y, int cx, int cy, uint f);
}
"@
$GWL_EXSTYLE = -20
$WS_EX_TRANSPARENT = 0x20; $WS_EX_TOOLWINDOW = 0x80; $WS_EX_APPWINDOW = 0x40000; $WS_EX_LAYERED = 0x80000

# One tray per user
$created = $false
$mutex = New-Object System.Threading.Mutex($true, "LiveChordSpeakerTray", [ref]$created)
if (-not $created) { exit 0 }

$profileDir = Join-Path $env:LOCALAPPDATA "LiveChordSpeaker"
$edge = "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe"
if (-not (Test-Path $edge)) { $edge = "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe" }
$sep = if ($Url.Contains("?")) { "&" } else { "?" }
# Autoplay flag: no "tap to start" overlay. The three background flags keep
# the 200 ms drift correction running while the window is covered.
$edgeArgs = @(
  "--user-data-dir=`"$profileDir`"", "--no-first-run", "--no-default-browser-check",
  "--autoplay-policy=no-user-gesture-required",
  "--disable-background-timer-throttling", "--disable-renderer-backgrounding",
  "--disable-backgrounding-occluded-windows"
)
if ($env:LIVECHORD_SPEAKER_FLAGS) { $edgeArgs += $env:LIVECHORD_SPEAKER_FLAGS }
$edgeArgs += "--app=`"$Url$($sep)follow=1`""

$script:hwnd = [IntPtr]::Zero
$script:shown = $false
$script:quitting = $false
$script:state = "wait"      # wait -> launch -> run

function Get-SpeakerEdge {
  Get-CimInstance Win32_Process -Filter "Name='msedge.exe'" |
    Where-Object { $_.CommandLine -like '*LiveChordSpeaker*' -and $_.CommandLine -notlike '*--type=*' }
}
function Stop-SpeakerEdge {
  Get-SpeakerEdge | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
  $script:hwnd = [IntPtr]::Zero
  $script:shown = $false
}
function Test-Server {
  try {
    $r = [System.Net.WebRequest]::Create($Url); $r.Timeout = 3000
    $r.GetResponse().Close(); return $true
  } catch { return $false }
}
function Set-Shown([bool]$show) {
  $h = $script:hwnd
  if ($h -eq [IntPtr]::Zero) { return }
  $ex = [LcWin]::GetWindowLong($h, $GWL_EXSTYLE)
  $ex = ($ex -bor $WS_EX_LAYERED -bor $WS_EX_TOOLWINDOW) -band (-bnot $WS_EX_APPWINDOW)
  if ($show) { $ex = $ex -band (-bnot $WS_EX_TRANSPARENT) } else { $ex = $ex -bor $WS_EX_TRANSPARENT }
  # Taskbar membership only updates while the window is hidden, hence the
  # quick hide/show around the style change.
  [void][LcWin]::ShowWindow($h, 0)
  [void][LcWin]::SetWindowLong($h, $GWL_EXSTYLE, $ex)
  [void][LcWin]::SetLayeredWindowAttributes($h, 0, $(if ($show) { 255 } else { 0 }), 2)   # LWA_ALPHA
  if ($show) {
    [void][LcWin]::ShowWindow($h, 9)                                  # SW_RESTORE
    [void][LcWin]::SetForegroundWindow($h)
  } else {
    [void][LcWin]::ShowWindow($h, 4)                                  # SW_SHOWNOACTIVATE
    # bottom of the z-order, so it never sits over anything
    [void][LcWin]::SetWindowPos($h, [IntPtr]1, 0, 0, 0, 0, 0x13)      # HWND_BOTTOM, NOSIZE|NOMOVE|NOACTIVATE
  }
  $script:shown = $show
}

# Tray icon: the app's PWA icon, else a stock one
$tray = New-Object System.Windows.Forms.NotifyIcon
$png = Join-Path $PSScriptRoot "..\frontend\img\livechord-icon-192.png"
try {
  $bmp = New-Object System.Drawing.Bitmap((New-Object System.Drawing.Bitmap $png), 32, 32)
  $tray.Icon = [System.Drawing.Icon]::FromHandle($bmp.GetHicon())
} catch { $tray.Icon = [System.Drawing.SystemIcons]::Application }
$tray.Text = "LiveChord 喇叭：等待伺服器…"
$tray.Visible = $true

$menu = New-Object System.Windows.Forms.ContextMenuStrip
$miShow = $menu.Items.Add("顯示播放器")
$miRestart = $menu.Items.Add("重新啟動喇叭")
[void]$menu.Items.Add("-")
$miQuit = $menu.Items.Add("結束")
$tray.ContextMenuStrip = $menu

$menu.add_Opening({
  $miShow.Text = if ($script:shown) { "隱藏播放器" } else { "顯示播放器" }
  $miShow.Enabled = $script:hwnd -ne [IntPtr]::Zero
})
$miShow.add_Click({ Set-Shown (-not $script:shown) })
$tray.add_MouseDoubleClick({ Set-Shown (-not $script:shown) })
$miRestart.add_Click({ Stop-SpeakerEdge; $script:state = "wait" })
$miQuit.add_Click({
  $script:quitting = $true
  $timer.Stop(); Stop-SpeakerEdge
  $tray.Visible = $false; $tray.Dispose()
  [System.Windows.Forms.Application]::Exit()
})

$script:tries = 0
$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = 1000
$timer.add_Tick({
  if ($script:quitting) { return }
  switch ($script:state) {
    "wait" {
      # At logon the network / NUC may not be up yet: wait up to ~3 min,
      # otherwise Edge would sit on an error page and never follow.
      $script:tries++
      if ((Test-Server) -or $script:tries -ge 60) {
        Stop-SpeakerEdge
        Start-Process -FilePath $edge -ArgumentList $edgeArgs
        $script:state = "launch"; $script:tries = 0
        $timer.Interval = 50           # catch the window as soon as it appears
        $tray.Text = "LiveChord 喇叭：啟動中…"
      }
    }
    "launch" {
      $script:tries++
      foreach ($p in Get-SpeakerEdge) {
        $proc = Get-Process -Id $p.ProcessId -ErrorAction SilentlyContinue
        if ($proc -and $proc.MainWindowHandle -ne 0) {
          $script:hwnd = $proc.MainWindowHandle
          Set-Shown $false
          $script:state = "run"; $timer.Interval = 1000
          $tray.Text = "LiveChord 喇叭：跟隨中（點兩下顯示）"
          return
        }
      }
      if ($script:tries -ge 300) { $script:state = "wait"; $script:tries = 0; $timer.Interval = 1000 }
    }
    "run" {
      if (-not (Get-SpeakerEdge)) {
        $script:hwnd = [IntPtr]::Zero; $script:shown = $false
        $script:state = "wait"
        $tray.Text = "LiveChord 喇叭：重新啟動中…"
      }
    }
  }
})
$timer.Start()
[System.Windows.Forms.Application]::Run()
