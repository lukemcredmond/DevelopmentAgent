<#
.SYNOPSIS
    Prepare this machine to develop with no internet connection.

.DESCRIPTION
    Pulls the models the agent roles need, builds a pip wheelhouse so the backend can
    be reinstalled offline, and caches frontend npm packages. Run it while you still
    have a connection; afterwards `scripts/run_eval.py` should pass with networking
    disabled.

    Models are pulled on whichever host serves the API, so point -OllamaUrl at the
    inference machine if it is not this one.

.PARAMETER OllamaUrl
    Base URL of the Ollama server to pull into. Defaults to http://localhost:11434.

.PARAMETER Models
    Models to pull. Defaults to a single-model-mode set that fits a 12 GB card.

.PARAMETER SkipModels
    Skip model pulls (Python/npm dependencies only).

.PARAMETER SkipPython
    Skip building the pip wheelhouse.

.EXAMPLE
    ./scripts/offline_bundle.ps1 -OllamaUrl http://192.168.1.50:11434
#>

[CmdletBinding()]
param(
    [string]$OllamaUrl = $(if ($env:OLLAMA_HOST) { $env:OLLAMA_HOST } else { "http://localhost:11434" }),
    [string[]]$Models = @(
        # Drives all four roles in single-model mode; strong native tool calling.
        "qwen2.5-coder:7b",
        # Fallback for a smaller card, and a fast lane for trivial cards.
        "qwen2.5-coder:3b",
        # Local embeddings for semantic search.
        "nomic-embed-text"
    ),
    [switch]$SkipModels,
    [switch]$SkipPython,
    [switch]$SkipNode
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$BundleDir = Join-Path $RepoRoot ".offline-bundle"

function Write-Step { param([string]$Text) Write-Host "`n=== $Text ===" -ForegroundColor Cyan }
function Write-Ok   { param([string]$Text) Write-Host "  [ok] $Text" -ForegroundColor Green }
function Write-Warn { param([string]$Text) Write-Host "  [warn] $Text" -ForegroundColor Yellow }

New-Item -ItemType Directory -Force -Path $BundleDir | Out-Null
$failures = @()

# --- Models -----------------------------------------------------------------
if (-not $SkipModels) {
    Write-Step "Pulling models into $OllamaUrl"
    try {
        $tags = Invoke-RestMethod -Uri "$OllamaUrl/api/tags" -TimeoutSec 10
        $installed = @($tags.models | ForEach-Object { $_.name })
        Write-Ok "Server reachable; $($installed.Count) model(s) already present"
    }
    catch {
        Write-Warn "Cannot reach $OllamaUrl - is Ollama running there? ($($_.Exception.Message))"
        $installed = @()
        $failures += "ollama unreachable"
    }

    foreach ($model in $Models) {
        if ($installed -contains $model -or $installed -contains "${model}:latest") {
            Write-Ok "$model already present"
            continue
        }
        Write-Host "  pulling $model ..." -ForegroundColor Gray
        try {
            # Stream the pull so a multi-GB download shows progress rather than hanging.
            $body = @{ name = $model; stream = $false } | ConvertTo-Json
            Invoke-RestMethod -Uri "$OllamaUrl/api/pull" -Method Post -Body $body `
                -ContentType "application/json" -TimeoutSec 3600 | Out-Null
            Write-Ok "$model pulled"
        }
        catch {
            Write-Warn "Failed to pull ${model}: $($_.Exception.Message)"
            $failures += "pull $model"
        }
    }
}

# --- Python wheelhouse ------------------------------------------------------
if (-not $SkipPython) {
    Write-Step "Building pip wheelhouse"
    $req = Join-Path $RepoRoot "requirements.txt"
    if (-not (Test-Path $req)) {
        Write-Warn "requirements.txt not found; skipping"
    }
    else {
        $wheelhouse = Join-Path $BundleDir "wheelhouse"
        New-Item -ItemType Directory -Force -Path $wheelhouse | Out-Null
        try {
            python -m pip download -r $req -d $wheelhouse
            if ($LASTEXITCODE -ne 0) { throw "pip download exited $LASTEXITCODE" }
            $count = (Get-ChildItem $wheelhouse -File).Count
            Write-Ok "$count package file(s) in $wheelhouse"
            Write-Host "  Reinstall offline with:" -ForegroundColor Gray
            Write-Host "    python -m pip install --no-index --find-links $wheelhouse -r requirements.txt" -ForegroundColor Gray
        }
        catch {
            Write-Warn "Wheelhouse build failed: $($_.Exception.Message)"
            $failures += "wheelhouse"
        }
    }
}

# --- Node modules -----------------------------------------------------------
if (-not $SkipNode) {
    Write-Step "Caching frontend dependencies"
    $frontend = Join-Path $RepoRoot "frontend"
    if (-not (Test-Path (Join-Path $frontend "package.json"))) {
        Write-Warn "No frontend/package.json; skipping"
    }
    else {
        try {
            Push-Location $frontend
            # npm ci populates node_modules and the local npm cache, both of which
            # make a later offline `npm ci --offline` work.
            npm ci
            if ($LASTEXITCODE -ne 0) { throw "npm ci exited $LASTEXITCODE" }
            Write-Ok "node_modules installed and npm cache warmed"
        }
        catch {
            Write-Warn "npm ci failed: $($_.Exception.Message)"
            $failures += "npm"
        }
        finally {
            Pop-Location
        }
    }
}

# --- Verify -----------------------------------------------------------------
Write-Step "Preflight"
try {
    Push-Location $RepoRoot
    python -c "from backend.bootstrap import initialize; initialize(); from backend.services.preflight import run_preflight; import json; r = run_preflight(); print(r['summary']); [print(f\"  [{c['status']}] {c['name']}: {c['detail']}\") for c in r['checks'] if c['status'] != 'ok']"
}
catch {
    Write-Warn "Preflight could not run: $($_.Exception.Message)"
}
finally {
    Pop-Location
}

Write-Step "Summary"
if ($failures.Count -eq 0) {
    Write-Ok "Offline bundle complete."
    Write-Host "  Verify by disabling networking and running: python scripts/run_eval.py" -ForegroundColor Gray
    exit 0
}
Write-Warn "Completed with issues: $($failures -join ', ')"
exit 1
