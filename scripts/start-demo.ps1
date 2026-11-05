<#
.SYNOPSIS
  OFFLINE demo startup. Uses only the existing .venv and an already-pulled model. Never downloads anything.

  - Starts Ollama on 127.0.0.1 if it is not already running (no pull)
  - Reports whether the configured model is present (if not, the API still runs in deterministic fallback)
  - Starts the ISOP API on http://127.0.0.1:8000 (Swagger UI: /docs)

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\start-demo.ps1
  powershell -ExecutionPolicy Bypass -File scripts\start-demo.ps1 -Deterministic   # no LLM at all
#>
param([switch]$Deterministic, [int]$Port = 8000)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$VenvPy = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $VenvPy)) { throw ".venv missing. Restore it (see README 'Restoring the environment') - this script will not download packages." }

# Hard offline guards for this process tree.
$env:ISOP_OFFLINE = "true"
$env:PIP_NO_INDEX = "1"
$env:OLLAMA_HOST = "127.0.0.1:11434"
Remove-Item Env:ANTHROPIC_API_KEY, Env:HF_TOKEN -ErrorAction SilentlyContinue

if ($Deterministic) {
    $env:LLM_PROVIDER = "deterministic"
    Write-Host "Planner: deterministic rules (no LLM)" -ForegroundColor Yellow
} else {
    $env:LLM_PROVIDER = "ollama"
    $Ollama = (Get-Command ollama -ErrorAction SilentlyContinue).Source
    if (-not $Ollama) { $Ollama = Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama.exe" }
    $running = $false
    try { Invoke-RestMethod http://127.0.0.1:11434/api/version -TimeoutSec 2 | Out-Null; $running = $true } catch {}
    if (-not $running -and (Test-Path $Ollama)) {
        Write-Host "Starting Ollama (local, 127.0.0.1:11434)..."
        Start-Process -FilePath $Ollama -ArgumentList "serve" -WindowStyle Hidden
        for ($i = 0; $i -lt 20 -and -not $running; $i++) {
            Start-Sleep -Milliseconds 500
            try { Invoke-RestMethod http://127.0.0.1:11434/api/version -TimeoutSec 2 | Out-Null; $running = $true } catch {}
        }
    }
    $model = $env:OLLAMA_MODEL
    $envFile = Join-Path $Root ".env"
    if (-not $model -and (Test-Path -LiteralPath $envFile)) {
        $m = Select-String -LiteralPath $envFile -Pattern '^OLLAMA_MODEL=(.+)$' | Select-Object -First 1
        if ($m) { $model = $m.Matches[0].Groups[1].Value.Trim() }
    }
    if (-not $model) { $model = "qwen2.5:3b" }
    if (-not $running) {
        Write-Host "WARNING: Ollama not running - API will use deterministic_fallback (clearly labelled)." -ForegroundColor Yellow
    } else {
        $tags = (Invoke-RestMethod http://127.0.0.1:11434/api/tags).models.name
        if ($tags -contains $model) { Write-Host "Planner: LLM via Ollama, model $model (present)" -ForegroundColor Green }
        else { Write-Host "WARNING: model $model not pulled - API will use deterministic_fallback. Available: $($tags -join ', ')" -ForegroundColor Yellow }
    }
}

Write-Host "Starting ISOP API on http://127.0.0.1:$Port  (Swagger: http://127.0.0.1:$Port/docs)  Ctrl+C to stop" -ForegroundColor Cyan
& $VenvPy -m uvicorn isop.api:app --host 127.0.0.1 --port $Port
