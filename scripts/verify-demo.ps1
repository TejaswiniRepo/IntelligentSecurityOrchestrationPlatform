<#
.SYNOPSIS
  Pre-presentation verification. Run in a second terminal while start-demo.ps1 is running. Offline only.

  Checks: API health, MCP tools, policy version, Ollama + model, a real LLM inference (also warms the model),
  and the ALLOW / REVIEW / REJECT demo cases end to end. Prints PASS/FAIL and exits 1 on any failure.
#>
param([string]$Api = "http://127.0.0.1:8000", [switch]$AllowFallback)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$fail = 0
function Check([string]$Name, [bool]$Ok, [string]$Detail = "") {
    $tag = if ($Ok) { "PASS" } else { "FAIL" }
    $color = if ($Ok) { "Green" } else { "Red" }
    Write-Host ("[{0}] {1,-44} {2}" -f $tag, $Name, $Detail) -ForegroundColor $color
    if (-not $Ok) { $script:fail++ }
}

try { $h = Invoke-RestMethod "$Api/health" -TimeoutSec 5 } catch { Check "API reachable at $Api" $false "start scripts\start-demo.ps1 first"; exit 1 }
Check "API reachable" $true "$Api"
Check "Offline mode enforced" ($h.config.offline -eq $true) "offline=$($h.config.offline)"
$expectedTools = @("check_device_history","check_device_risk","check_email_reputation","check_ip_risk","check_phone_risk","get_registration_history")
Check "MCP tools available (6)" ((Compare-Object $expectedTools $h.mcp_tools) -eq $null) ($h.mcp_tools -join ",")
Check "Policy loaded" ([bool]$h.policy_version) $h.policy_version

$llmMode = $h.config.llm_provider -eq "ollama"
if ($llmMode) {
    Check "Ollama reachable on 127.0.0.1" ($h.llm.reachable -eq $true) "version=$($h.llm.version)"
    Check "Model present: $($h.llm.model)" ($h.llm.model_present -eq $true) ""
    if ($h.llm.reachable -and $h.llm.model_present) {
        $body = @{ model = $h.llm.model; stream = $false; format = "json"; keep_alive = "30m"
                   messages = @(@{ role = "user"; content = 'Reply with JSON {"ok": true}' }); options = @{ temperature = 0 } } | ConvertTo-Json -Depth 5
        $sw = [Diagnostics.Stopwatch]::StartNew()
        try {
            $r = Invoke-RestMethod http://127.0.0.1:11434/api/chat -Method Post -Body $body -ContentType "application/json" -TimeoutSec 240
            Check "Real local inference (warm-up)" ($r.message.content -match "ok") ("{0:N1}s" -f $sw.Elapsed.TotalSeconds)
        } catch { Check "Real local inference (warm-up)" $false $_.Exception.Message }
    }
} else {
    Write-Host "[INFO] LLM_PROVIDER=$($h.config.llm_provider) - skipping Ollama checks" -ForegroundColor Yellow
}

$cases = @(
    @{ file = "allow.json";              expect = "ALLOW"  },
    @{ file = "review_tor_ip.json";      expect = "REVIEW" },
    @{ file = "review_conflicting.json"; expect = "REVIEW" },
    @{ file = "reject_device_farm.json"; expect = "REJECT" }
)
foreach ($c in $cases) {
    $json = Get-Content (Join-Path $Root "examples\$($c.file)") -Raw
    $sw = [Diagnostics.Stopwatch]::StartNew()
    $r = Invoke-RestMethod "$Api/register/check" -Method Post -Body $json -ContentType "application/json" -TimeoutSec 300
    $modeOk = (-not $llmMode) -or $AllowFallback -or ($r.planner_mode -eq "llm")
    $tools = ($r.trace.tools_invoked | Where-Object phase -eq "follow_up" | ForEach-Object { "$($_.tool)$(if ($_.entity) { ':' + $_.entity })" }) -join ","
    Check ("{0} -> {1}" -f $c.file, $c.expect) (($r.decision -eq $c.expect) -and $modeOk) `
        ("got {0}, planner={1}, follow-ups=[{2}], {3:N1}s" -f $r.decision, $r.planner_mode, $tools, $sw.Elapsed.TotalSeconds)
}

try {
    Invoke-RestMethod "$Api/register/check" -Method Post -Body (Get-Content (Join-Path $Root "examples\invalid.json") -Raw) -ContentType "application/json" | Out-Null
    Check "Invalid request rejected (422)" $false "was accepted"
} catch { Check "Invalid request rejected (422)" ($_.Exception.Response.StatusCode.value__ -eq 422) "" }

try {
    $ui = Invoke-WebRequest "$Api/ui/" -UseBasicParsing -TimeoutSec 5
    Check "Dashboard served at /ui/ (offline assets)" ($ui.StatusCode -eq 200 -and $ui.Content -match "Show Intelligent Security Orchestration") ""
} catch { Check "Dashboard served at /ui/ (offline assets)" $false $_.Exception.Message }

# Async path used by the dashboard: case id returned at once, events persisted while it runs.
$normal = Get-Content (Join-Path $Root "examples\allow.json") -Raw
$sub = Invoke-RestMethod "$Api/register/check?mode=async" -Method Post -Body $normal -ContentType "application/json" -Headers @{ "Idempotency-Key" = "verify-$([guid]::NewGuid().ToString('N'))" }
$sw = [Diagnostics.Stopwatch]::StartNew()
do { Start-Sleep -Milliseconds 500; $case = Invoke-RestMethod "$Api/registrations/$($sub.case_id)" } while ($case.status -eq "running" -and $sw.Elapsed.TotalSeconds -lt 240)
$ev = Invoke-RestMethod "$Api/registrations/$($sub.case_id)/events"
Check "Async case + live events ($($sub.case_id))" (($case.status -eq "completed") -and ($ev.events[-1].type -eq "case.completed")) `
    ("{0} events, decision {1}, {2:N1}s" -f $ev.events.Count, $case.decision, $sw.Elapsed.TotalSeconds)

$audit = Invoke-RestMethod "$Api/audit?limit=1"
Check "Audit trail (SQLite) recording" ($audit.Count -ge 1) "latest=$($audit[0].decision) mode=$($audit[0].planner_mode)"

if ($fail -gt 0) { Write-Host "`n$fail check(s) FAILED" -ForegroundColor Red; exit 1 }
Write-Host "`nAll checks passed - ready to present." -ForegroundColor Green
