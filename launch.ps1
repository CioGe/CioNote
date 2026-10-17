# ============================================================
#  CioNote launcher (ASCII only - PowerShell 5.1 reads UTF8 w/o BOM as GBK)
#  Usage:
#    launch.ps1              normal start (no console window for the app)
#    launch.ps1 -Debug       run app in this console, show all errors
#    launch.ps1 -Probe       only detect interpreter + ollama, no GUI
#    launch.ps1 -NoInstall   skip pip install step
#  Everything is logged to launcher.log next to this script.
# ============================================================
param(
    [switch]$Debug,
    [switch]$Probe,
    [switch]$NoInstall
)

$ErrorActionPreference = 'Continue'
$log = Join-Path $PSScriptRoot 'launcher.log'
Set-Content -Path $log -Value ("=== CioNote launcher " + (Get-Date -Format 'yyyy-MM-dd HH:mm:ss') + " ===")

function Log([string]$m) {
    $m | Out-File -FilePath $log -Append -Encoding utf8
    if ($Debug) { Write-Host $m }
}

function Test-Python([string]$path) {
    if (-not (Test-Path $path)) { return $false }
    & $path -c "import sys, tkinter" 2>$null | Out-Null
    return ($LASTEXITCODE -eq 0)
}

Log ("user=" + $env:USERNAME + "  script=" + $PSScriptRoot)

# ---------- 1) find a python that really has tkinter ----------
$py = $null

$cmd = Get-Command python -ErrorAction SilentlyContinue
if ($cmd -and ($cmd.Source -notmatch 'WindowsApps') -and (Test-Python $cmd.Source)) {
    $py = $cmd.Source
    Log ("found via PATH: " + $py)
}

if (-not $py) {
    foreach ($v in @('314','313','312','311','310')) {
        foreach ($base in @(
            (Join-Path $env:LOCALAPPDATA ("Programs\Python\Python" + $v)),
            ("C:\Python" + $v),
            ("C:\Program Files\Python" + $v))) {
            $cand = Join-Path $base 'python.exe'
            if (Test-Python $cand) { $py = $cand; Log ("found via install dir: " + $py); break }
        }
        if ($py) { break }
    }
}

if (-not $py) {
    foreach ($base in @((Join-Path $env:USERPROFILE 'anaconda3'), (Join-Path $env:USERPROFILE 'miniconda3'))) {
        $cand = Join-Path $base 'python.exe'
        if (Test-Python $cand) { $py = $cand; Log ("found via conda: " + $py); break }
    }
}

if (-not $py) {
    $wbRoot = Join-Path $env:USERPROFILE '.workbuddy\binaries\python'
    foreach ($sub in @('versions', 'envs')) {
        $dir = Join-Path $wbRoot $sub
        if (Test-Path $dir) {
            foreach ($d in (Get-ChildItem $dir -Directory)) {
                $cand = Join-Path $d.FullName 'python.exe'
                if (-not (Test-Path $cand)) { $cand = Join-Path $d.FullName 'Scripts\python.exe' }
                if (Test-Python $cand) { $py = $cand; Log ("found via workbuddy: " + $py); break }
            }
        }
        if ($py) { break }
    }
}

if (-not $py) {
    Log "NO usable python found (tkinter missing everywhere)"
    Add-Type -AssemblyName System.Windows.Forms
    [System.Windows.Forms.MessageBox]::Show(
        "No usable Python was found on this PC.`n`n" +
        "Please install Python 3.10 - 3.14 from python.org, and during setup tick:`n" +
        "  [x] Add python.exe to PATH`n" +
        "  [x] tcl/tk and IDLE   (CioNote needs tkinter)`n`n" +
        "Details: " + $log, "CioNote - Python not found") | Out-Null
    exit 1
}

$ver = & $py -c "import sys; print(sys.version)" 2>$null
Log ("using python: " + $py + "  (" + $ver + ")")

# ---------- 2) ollama connectivity report ----------
try {
    $resp = Invoke-WebRequest -Uri 'http://localhost:11434/api/tags' -TimeoutSec 5 -UseBasicParsing
    Log ("ollama: reachable, HTTP " + $resp.StatusCode)
    $models = ($resp.Content | ConvertFrom-Json).models
    if ($models) {
        foreach ($m in $models) { Log ("  model: " + $m.name) }
    } else {
        Log "  model: (none pulled yet - run Download-Model-ChinaMirror.bat)"
    }
} catch {
    Log ("ollama: NOT reachable - " + $_.Exception.Message)
    Log "  app will still start; use AI Assistant -> Check Ollama Connection in-app"
}

if ($Probe) { Log "probe done"; exit 0 }

# ---------- 3) deps ----------
if (-not $NoInstall) {
    Log "pip install pillow (only used for optional screenshot feature) ..."
    $pipOut = & $py -m pip install --quiet pillow -i https://pypi.tuna.tsinghua.edu.cn/simple 2>&1
    Log ("pip exit code: " + $LASTEXITCODE)
    if ($pipOut) { ($pipOut | Select-Object -First 3) | ForEach-Object { Log ("pip: " + $_) } }
}

# ---------- 4) start ----------
$dir = Split-Path $py -Parent
$pyw = Join-Path $dir 'pythonw.exe'
$runner = $py
if (Test-Path $pyw) { $runner = $pyw }
Log ("runner: " + $runner)

Set-Location $PSScriptRoot
if ($Debug) {
    & $py (Join-Path $PSScriptRoot 'cionote.py')
    Log ("app exited with code " + $LASTEXITCODE)
} else {
    Start-Process -FilePath $runner -ArgumentList ('"' + (Join-Path $PSScriptRoot 'cionote.py') + '"') -WorkingDirectory $PSScriptRoot
    Log "app started (detached)"
}
exit 0
