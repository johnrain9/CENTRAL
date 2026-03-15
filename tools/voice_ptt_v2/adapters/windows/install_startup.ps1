Param(
    [string]$AutoHotkeyPath = "",
    [string]$ScriptPath = ""
)

$repoRoot = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $PSScriptRoot))
if (-not $ScriptPath) {
    $ScriptPath = Join-Path $repoRoot 'tools\voice_ptt_v2\adapters\windows\voice_ptt_hotkey.ahk'
}

if (-not (Test-Path $ScriptPath)) {
    throw "Voice PTT AutoHotkey script not found at $ScriptPath"
}

if (-not $AutoHotkeyPath) {
    $candidate = Get-Command AutoHotkey.exe -ErrorAction SilentlyContinue
    if ($candidate) {
        $AutoHotkeyPath = $candidate.Source
    } else {
        $commonPaths = @(
            'C:\Program Files\AutoHotkey\v2\AutoHotkey64.exe',
            'C:\Program Files\AutoHotkey\v2\AutoHotkey.exe',
            'C:\Program Files\AutoHotkey\AutoHotkey64.exe',
            'C:\Program Files\AutoHotkey\AutoHotkey.exe'
        )
        $AutoHotkeyPath = $commonPaths | Where-Object { Test-Path $_ } | Select-Object -First 1
    }
}

if (-not $AutoHotkeyPath -or -not (Test-Path $AutoHotkeyPath)) {
    throw "AutoHotkey executable was not found. Pass -AutoHotkeyPath with the full v2 executable path."
}

$startupDir = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Startup'
$shortcutPath = Join-Path $startupDir 'Voice PTT.lnk'
New-Item -ItemType Directory -Path $startupDir -Force | Out-Null
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $AutoHotkeyPath
$shortcut.Arguments = '"' + $ScriptPath + '"'
$shortcut.WorkingDirectory = Split-Path $ScriptPath
$shortcut.Save()

Write-Host "Voice PTT startup shortcut refreshed at $shortcutPath"
