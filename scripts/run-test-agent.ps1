param(
    [string]$TestPlan = "test-plans/gawain-localization-smoke.yaml"
)

$ErrorActionPreference = "Stop"

Write-Host "STS2 Test Agent"
Write-Host "Test plan: $TestPlan"

if (-not (Test-Path $TestPlan)) {
    Write-Error "Test plan not found: $TestPlan"
    exit 2
}

Write-Host "This runner is the entry point for the scripted Test Agent."
Write-Host "Expected stages:"
Write-Host "1. Parse test-plan.yaml"
Write-Host "2. Build target mod"
Write-Host "3. Run localization check"
Write-Host "4. Deploy mod"
Write-Host "5. Launch STS2"
Write-Host "6. Wait for sts2 ping"
Write-Host "7. Execute smoke tests"
Write-Host "8. Collect logs, screenshots, state snapshots"
Write-Host "9. Write test-report.md"

Write-Warning "Implementation pending: this script currently defines the execution entry point only."
exit 2
