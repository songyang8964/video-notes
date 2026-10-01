# One-time installer for video-notes (Windows PowerShell).
#   powershell -ExecutionPolicy Bypass -File install.ps1            # install into .venv
#   powershell -ExecutionPolicy Bypass -File install.ps1 -AddToPath # also make `video-notes` work in any folder
param([switch]$AddToPath, [string]$Python = "python")

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$venv = Join-Path $root ".venv"
if (-not (Test-Path (Join-Path $venv "Scripts\python.exe"))) {
    & $Python -m venv $venv
}
$py = Join-Path $venv "Scripts\python.exe"
& $py -m pip install --upgrade pip | Out-Null
& $py -m pip install -e $root
$scripts = Join-Path $venv "Scripts"
if ($AddToPath) {
    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    if (($userPath -split ";") -notcontains $scripts) {
        [Environment]::SetEnvironmentVariable("Path", "$userPath;$scripts", "User")
        Write-Host "Added $scripts to your user PATH. Open a new terminal to use 'video-notes'."
    }
} else {
    Write-Host "Installed. Run: $scripts\video-notes.exe  (or rerun with -AddToPath to use 'video-notes' anywhere)"
}
& (Join-Path $scripts "video-notes.exe") doctor
