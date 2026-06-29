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

# IMPORTANT: do NOT use $ErrorActionPreference='Stop' here. git/pip write normal
# progress to stderr, and under Stop PowerShell treats that as a fatal error and
# aborts mid-clone. We use Continue and check $LASTEXITCODE after each command.
$ErrorActionPreference = "Continue"
try { $PSNativeCommandUseErrorActionPreference = $false } catch {}

function Info($m) { Write-Host "  $m" -ForegroundColor Cyan }
function Ok($m)   { Write-Host "  $m" -ForegroundColor Green }
function Warn($m) { Write-Host "  $m" -ForegroundColor Yellow }
function Die($m)  { Write-Host "  $m" -ForegroundColor Red; throw $m }

Write-Host ""
Write-Host "  OMNI-DEV installer" -ForegroundColor Magenta
Write-Host ""

# --- Prerequisites ---------------------------------------------------------
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Die "git is required but not found. Install Git, then re-run."
}
$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) { $py = Get-Command py -ErrorAction SilentlyContinue }
if (-not $py) { Die "Python 3.10+ is required but not found. Install Python, then re-run." }

# --- Clone or update -------------------------------------------------------
if (Test-Path (Join-Path $InstallDir ".git")) {
    Info "Updating existing install at $InstallDir ..."
    git -C $InstallDir fetch --depth 1 origin $Branch 2>&1 | Out-Null
    git -C $InstallDir checkout -B $Branch "origin/$Branch" 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { Die "git update failed (exit $LASTEXITCODE)." }
} else {
    if (Test-Path $InstallDir) {
        Warn "Removing existing non-git folder at $InstallDir ..."
        Remove-Item -Recurse -Force $InstallDir
    }
    Info "Cloning $Repo (branch $Branch) ..."
    git clone --quiet --depth 1 --branch $Branch $Repo $InstallDir 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { Die "git clone failed (exit $LASTEXITCODE)." }
}
if (-not (Test-Path (Join-Path $InstallDir "omni_dev.py"))) {
    Die "Install failed: omni_dev.py not found in $InstallDir"
}

# --- Virtualenv + dependencies --------------------------------------------
$venvPython = Join-Path $InstallDir "venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Info "Creating virtualenv ..."
    & $py.Source -m venv (Join-Path $InstallDir "venv") 2>&1 | Out-Null
    if (-not (Test-Path $venvPython)) { Die "Failed to create virtualenv." }
}
Info "Installing dependencies (this can take a minute) ..."
& $venvPython -m pip install --upgrade pip 2>&1 | Out-Null
& $venvPython -m pip install -r (Join-Path $InstallDir "requirements.txt") 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) { Warn "pip reported issues; the CLI may still run. Check 'omni' and run /doctor." }

# --- Install the `omni` launcher on PATH -----------------------------------
New-Item -ItemType Directory -Force -Path $BinDir | Out-Null
$omniPy = Join-Path $InstallDir "omni_dev.py"

$cmdShim = "@echo off`r`n`"$venvPython`" `"$omniPy`" %*`r`n"
Set-Content -Path (Join-Path $BinDir "omni.cmd") -Value $cmdShim -Encoding ASCII -NoNewline

$ps1Shim = "& `"$venvPython`" `"$omniPy`" `$args`r`n"
Set-Content -Path (Join-Path $BinDir "omni.ps1") -Value $ps1Shim -Encoding UTF8 -NoNewline

# Add BinDir to the user PATH if it isn't already there.
$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
if (($userPath -split ';') -notcontains $BinDir) {
    Info "Adding $BinDir to your user PATH ..."
    [Environment]::SetEnvironmentVariable("Path", "$userPath;$BinDir", "User")
}
$env:Path = "$env:Path;$BinDir"  # make `omni` available in THIS session too

Write-Host ""
Ok "Installed to $InstallDir"
Write-Host ""
Write-Host "  Start the CLI from any project folder with:" -ForegroundColor White
Write-Host "      omni" -ForegroundColor Magenta
Write-Host ""
Warn "If 'omni' isn't found, open a NEW terminal so the updated PATH loads."
Write-Host ""
