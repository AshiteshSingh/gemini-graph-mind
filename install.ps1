<#
  Omni-Dev installer (Windows / PowerShell)

  One-line install:
    irm https://raw.githubusercontent.com/AshiteshSingh/gemini-graph-mind/main/install.ps1 | iex

  What it does:
    1. Clones (or updates) the repo into  %LOCALAPPDATA%\omni-dev
    2. Creates a Python virtualenv and installs dependencies
    3. Installs an `omni` command on your PATH so you can run the CLI
       from any project directory just by typing:  omni
#>

param(
    [string]$InstallDir = (Join-Path $env:LOCALAPPDATA "omni-dev"),
    [string]$Repo       = "https://github.com/AshiteshSingh/gemini-graph-mind",
    [string]$Branch     = "main",
    [string]$BinDir     = (Join-Path $env:USERPROFILE ".omni\bin")
)

$ErrorActionPreference = "Stop"

function Info($m)  { Write-Host "  $m" -ForegroundColor Cyan }
function Ok($m)    { Write-Host "  $m" -ForegroundColor Green }
function Warn($m)  { Write-Host "  $m" -ForegroundColor Yellow }
function Die($m)   { Write-Host "  $m" -ForegroundColor Red; exit 1 }

Write-Host ""
Write-Host "  OMNI-DEV installer" -ForegroundColor Magenta
Write-Host ""

# --- Prerequisites ---------------------------------------------------------
$git = Get-Command git -ErrorAction SilentlyContinue
if (-not $git) { Die "git is required but not found. Install Git, then re-run." }

$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) { $py = Get-Command py -ErrorAction SilentlyContinue }
if (-not $py) { Die "Python 3.10+ is required but not found. Install Python, then re-run." }

# --- Clone or update -------------------------------------------------------
if (Test-Path (Join-Path $InstallDir ".git")) {
    Info "Updating existing install at $InstallDir ..."
    git -C $InstallDir fetch --depth 1 origin $Branch 2>&1 | Out-Null
    git -C $InstallDir checkout $Branch 2>&1 | Out-Null
    git -C $InstallDir pull --ff-only 2>&1 | Out-Null
} else {
    Info "Cloning $Repo (branch $Branch) into $InstallDir ..."
    git clone --depth 1 --branch $Branch $Repo $InstallDir 2>&1 | Out-Null
}
if (-not (Test-Path (Join-Path $InstallDir "omni_dev.py"))) {
    Die "Clone/update failed: omni_dev.py not found in $InstallDir"
}

# --- Virtualenv + dependencies --------------------------------------------
$venvPython = Join-Path $InstallDir "venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Info "Creating virtualenv ..."
    & $py.Source -m venv (Join-Path $InstallDir "venv")
}
Info "Installing dependencies (this can take a minute) ..."
& $venvPython -m pip install --upgrade pip 2>&1 | Out-Null
& $venvPython -m pip install -r (Join-Path $InstallDir "requirements.txt") 2>&1 | Out-Null

# --- Install the `omni` launcher on PATH -----------------------------------
New-Item -ItemType Directory -Force -Path $BinDir | Out-Null

$cmdShim = @"
@echo off
"$venvPython" "$(Join-Path $InstallDir 'omni_dev.py')" %*
"@
Set-Content -Path (Join-Path $BinDir "omni.cmd") -Value $cmdShim -Encoding ASCII

$ps1Shim = @"
& "$venvPython" "$(Join-Path $InstallDir 'omni_dev.py')" `$args
"@
Set-Content -Path (Join-Path $BinDir "omni.ps1") -Value $ps1Shim -Encoding UTF8

# Add BinDir to the user PATH if it isn't already there.
$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
if ($userPath -notlike "*$BinDir*") {
    Info "Adding $BinDir to your user PATH ..."
    [Environment]::SetEnvironmentVariable("Path", "$userPath;$BinDir", "User")
    $env:Path = "$env:Path;$BinDir"  # current session
}

Write-Host ""
Ok "Installed."
Write-Host ""
Write-Host "  Start the CLI from any project folder with:" -ForegroundColor White
Write-Host "      omni" -ForegroundColor Magenta
Write-Host ""
Warn "Open a NEW terminal first (so the updated PATH is picked up)."
Write-Host ""
