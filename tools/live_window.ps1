# Runs a SceneAccess command line call in this console window, so it can be followed live (and read by a screen
# reader), and writes the same output to a log file for whoever started it. Ctrl+C cancels the run; either way a
# last line "=== Ende ===" marks the log as finished, and the window closes; the log keeps everything.
# Usage: powershell -File tools\live_window.ps1 <log file> <arguments for python -m sceneaccess.cli>
param([Parameter(Mandatory)][string]$Log, [Parameter(ValueFromRemainingArguments)][string[]]$Arguments)

[Console]::OutputEncoding = [Text.Encoding]::UTF8
$repo = Split-Path -Parent $PSScriptRoot
$Host.UI.RawUI.WindowTitle = "SceneAccess: $($Arguments -join ' ')"
Set-Content -LiteralPath $Log -Value "=== Start $(Get-Date -Format 'HH:mm:ss'): $($Arguments -join ' ') ===" -Encoding utf8
try {
    & "$repo\.venv\Scripts\python.exe" -u -m sceneaccess.cli @Arguments 2>&1 | ForEach-Object {
        $_
        Add-Content -LiteralPath $Log -Value $_ -Encoding utf8
    }
} finally {
    Add-Content -LiteralPath $Log -Value "=== Ende $(Get-Date -Format 'HH:mm:ss') ===" -Encoding utf8
}
