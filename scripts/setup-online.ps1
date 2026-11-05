<#
.SYNOPSIS
  ONE-TIME preparation for the ISOP demo. REQUIRES INTERNET. Run this before the demo day, never during it.

  1. Creates .venv and installs pinned Python packages (and caches wheels in vendor\wheels for offline reinstall)
  2. Installs a CPU-only Ollama runtime if Ollama is not already installed, and verifies its signature
  3. Starts Ollama on 127.0.0.1 and pulls the configured model from the Hugging Face Hub
     (official Qwen/Qwen2.5-3B-Instruct-GGUF, Q4_K_M, ~2 GB, Qwen Research License)
  4. Runs the offline test suite

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\setup-online.ps1
  # Alternative: the same model family from the Ollama library
  powershell -ExecutionPolicy Bypass -File scripts\setup-online.ps1 -Model qwen2.5:3b
#>
param(
    [string]$Model = "hf.co/Qwen/Qwen2.5-3B-Instruct-GGUF:Q4_K_M",
    [string]$Python = "py -3.12"
)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$VenvPy = Join-Path $Root ".venv\Scripts\python.exe"

Write-Host "== 1/4 Python environment" -ForegroundColor Cyan
if (-not (Test-Path $VenvPy)) { Invoke-Expression "$Python -m venv .venv" }
& $VenvPy -m pip install --upgrade pip
& $VenvPy -m pip install -r requirements.txt
& $VenvPy -m pip install -e . --no-deps
New-Item -ItemType Directory -Force vendor\wheels | Out-Null
& $VenvPy -m pip download -r requirements.txt setuptools wheel -d vendor\wheels --only-binary=:all: | Out-Null
Write-Host "   cached wheels in vendor\wheels (offline reinstall: see README 'Restoring the environment')"

Write-Host "== 2/4 Ollama runtime" -ForegroundColor Cyan
$Ollama = (Get-Command ollama -ErrorAction SilentlyContinue).Source
if (-not $Ollama) { $Ollama = Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama.exe" }
if (-not (Test-Path $Ollama)) {
    & $VenvPy scripts\install_ollama_cpu.py
}
$sig = Get-AuthenticodeSignature $Ollama
if ($sig.Status -ne "Valid" -or $sig.SignerCertificate.Subject -notmatch "O=Ollama Inc\.") {
    throw "Ollama binary signature check failed: $($sig.Status) $($sig.SignerCertificate.Subject)"
}
Write-Host "   $Ollama signed by Ollama Inc. ($(& $Ollama --version 2>$null | Select-Object -Last 1))"

Write-Host "== 3/4 Model $Model" -ForegroundColor Cyan
$env:OLLAMA_HOST = "127.0.0.1:11434"
try { Invoke-RestMethod http://127.0.0.1:11434/api/version -TimeoutSec 2 | Out-Null }
catch { Start-Process -FilePath $Ollama -ArgumentList "serve" -WindowStyle Hidden; Start-Sleep -Seconds 4 }
& $Ollama pull $Model
& $Ollama list

Write-Host "== 4/4 Offline test suite" -ForegroundColor Cyan
& $VenvPy -m pytest
if (-not (Test-Path .env)) {
    Copy-Item .env.example .env
    (Get-Content .env) -replace '^OLLAMA_MODEL=.*', "OLLAMA_MODEL=$Model" | Set-Content .env -Encoding utf8
    Write-Host "   created .env with OLLAMA_MODEL=$Model"
}
Write-Host "`nSetup complete. Demo day: scripts\start-demo.ps1 then scripts\verify-demo.ps1 (no internet needed)." -ForegroundColor Green
