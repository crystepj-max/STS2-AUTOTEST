param(
    [string]$TestPlan = "test-plans/gawain-localization-smoke.yaml",
    [string]$ModProject = "../STS2-GAWAIN",
    [string]$TaskId = "gawain-localization-key-fix",
    [string]$InfraPath = "../sts2-dev-infra",
    [string]$GameModsPath = "",
    [string]$SteamAppId = "2868840",
    [int]$PingTimeoutSeconds = 90,
    [switch]$SkipLaunchGame,
    [switch]$SkipDeploy,
    [switch]$SkipGameSmoke
)

$ErrorActionPreference = "Stop"

function Resolve-FullPath([string]$PathValue) {
    return [System.IO.Path]::GetFullPath((Join-Path (Get-Location) $PathValue))
}

function Ensure-Directory([string]$PathValue) {
    if (-not (Test-Path $PathValue)) {
        New-Item -ItemType Directory -Path $PathValue | Out-Null
    }
}

function Add-Result($Results, [string]$Name, [string]$Status, [string]$Evidence, [string]$Details = "") {
    [void]$Results.Add([pscustomobject]@{ Name = $Name; Status = $Status; Evidence = $Evidence; Details = $Details })
}

function Run-Command([string]$Name, [string]$FilePath, [string[]]$Arguments, [string]$LogPath) {
    Add-Content -Path $LogPath -Value "# $Name"
    Add-Content -Path $LogPath -Value "> $FilePath $($Arguments -join ' ')"
    $output = & $FilePath @Arguments 2>&1
    $exit = $LASTEXITCODE
    $output | ForEach-Object { Add-Content -Path $LogPath -Value $_ }
    Add-Content -Path $LogPath -Value "ExitCode: $exit"
    return [pscustomobject]@{ ExitCode = $exit; Output = $output }
}

function Write-TestReport($ReportPath, $Conclusion, $Results, $TaskId, $ModProjectPath, $InfraFullPath, $TestPlanPath, $FailureDetails, $BlockedDetails, $ArtifactDir) {
    $rows = $Results | ForEach-Object { "| $($_.Name) | $($_.Status) | $($_.Evidence) |" }
    if (-not $rows) { $rows = @("| No checks executed | BLOCKED | test-report.md |") }
    $content = @"
# Test Report: $TaskId

## 测试结论

$Conclusion

## 环境

- Repo: $ModProjectPath
- Branch:
- Commit:
- STS2 version:
- BaseLib version:
- OS: $([System.Environment]::OSVersion.VersionString)
- Test runner: STS2-AUTOTEST/scripts/run-test-agent.ps1
- Test plan: $TestPlanPath
- Infra path: $InfraFullPath

## 执行命令

``````powershell
pwsh scripts/run-test-agent.ps1 -ModProject "$ModProjectPath" -TaskId "$TaskId" -InfraPath "$InfraFullPath"
``````

## 测试结果

| 测试项 | 结果 | 证据 |
|---|---|---|
$($rows -join "`n")

## 失败详情

$FailureDetails

## 阻塞详情

$BlockedDetails

## 附件

- artifact dir: $ArtifactDir
- build log: build.log
- localization log: localization-check.log
- deploy log: deploy.log
- launch log: launch.log
- sts2 cli log: sts2-cli.log
- game smoke log: game-smoke.log
- state snapshots: state/
- screenshots: screenshots/

## 建议

- FAILED：交回 Developer Agent 修复。
- BLOCKED：先补齐环境、游戏、自动化接口或构建目标。
"@
    Set-Content -Path $ReportPath -Value $content -Encoding UTF8
}

$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location (Join-Path $scriptRoot "..")

$testPlanPath = Resolve-FullPath $TestPlan
$modProjectPath = Resolve-FullPath $ModProject
$infraFullPath = Resolve-FullPath $InfraPath
$artifactDir = Join-Path $modProjectPath ".agent-runs/$TaskId"
$stateDir = Join-Path $artifactDir "state"
$screenshotDir = Join-Path $artifactDir "screenshots"
$reportPath = Join-Path $artifactDir "test-report.md"

Ensure-Directory $artifactDir
Ensure-Directory $stateDir
Ensure-Directory $screenshotDir

$results = [System.Collections.ArrayList]::new()
$conclusion = "PASSED"
$failureDetails = ""
$blockedDetails = ""

try {
    if (-not (Test-Path $testPlanPath)) {
        $conclusion = "BLOCKED"
        $blockedDetails = "Test plan not found: $testPlanPath"
        Add-Result $results "Test Plan" "BLOCKED" $testPlanPath $blockedDetails
        Write-TestReport $reportPath $conclusion $results $TaskId $modProjectPath $infraFullPath $testPlanPath $failureDetails $blockedDetails $artifactDir
        exit 2
    }
    Add-Result $results "Test Plan" "PASSED" $testPlanPath

    if (-not (Test-Path $modProjectPath)) {
        $conclusion = "BLOCKED"
        $blockedDetails = "Mod project path not found: $modProjectPath"
        Add-Result $results "Mod Project" "BLOCKED" $modProjectPath $blockedDetails
        Write-TestReport $reportPath $conclusion $results $TaskId $modProjectPath $infraFullPath $testPlanPath $failureDetails $blockedDetails $artifactDir
        exit 2
    }
    Add-Result $results "Mod Project" "PASSED" $modProjectPath

    if (-not (Test-Path $infraFullPath)) {
        $conclusion = "BLOCKED"
        $blockedDetails = "Infra path not found: $infraFullPath"
        Add-Result $results "Infra Path" "BLOCKED" $infraFullPath $blockedDetails
        Write-TestReport $reportPath $conclusion $results $TaskId $modProjectPath $infraFullPath $testPlanPath $failureDetails $blockedDetails $artifactDir
        exit 2
    }
    Add-Result $results "Infra Path" "PASSED" $infraFullPath

    $buildLog = Join-Path $artifactDir "build.log"
    $solutions = Get-ChildItem -Path $modProjectPath -Filter *.sln -Recurse -File -ErrorAction SilentlyContinue
    $projects = Get-ChildItem -Path $modProjectPath -Filter *.csproj -Recurse -File -ErrorAction SilentlyContinue
    if ($solutions.Count -eq 0 -and $projects.Count -eq 0) {
        $conclusion = "BLOCKED"
        $blockedDetails = "No .sln or .csproj build target found under $modProjectPath"
        Set-Content -Path $buildLog -Value $blockedDetails -Encoding UTF8
        Add-Result $results "Build" "BLOCKED" "build.log" $blockedDetails
        Write-TestReport $reportPath $conclusion $results $TaskId $modProjectPath $infraFullPath $testPlanPath $failureDetails $blockedDetails $artifactDir
        exit 2
    }

    $buildTarget = if ($solutions.Count -gt 0) { $solutions[0].FullName } else { $projects[0].FullName }
    $restore = Run-Command "dotnet restore" "dotnet" @("restore", $buildTarget) $buildLog
    if ($restore.ExitCode -ne 0) {
        $conclusion = "FAILED"
        $failureDetails = "dotnet restore failed. See build.log."
        Add-Result $results "Build" "FAILED" "build.log" $failureDetails
        Write-TestReport $reportPath $conclusion $results $TaskId $modProjectPath $infraFullPath $testPlanPath $failureDetails $blockedDetails $artifactDir
        exit 1
    }
    $build = Run-Command "dotnet build" "dotnet" @("build", $buildTarget, "--no-restore") $buildLog
    if ($build.ExitCode -ne 0) {
        $conclusion = "FAILED"
        $failureDetails = "dotnet build failed. See build.log."
        Add-Result $results "Build" "FAILED" "build.log" $failureDetails
        Write-TestReport $reportPath $conclusion $results $TaskId $modProjectPath $infraFullPath $testPlanPath $failureDetails $blockedDetails $artifactDir
        exit 1
    }
    Add-Result $results "Build" "PASSED" "build.log"

    $locLog = Join-Path $artifactDir "localization-check.log"
    $locScript = Join-Path $infraFullPath "scripts/check-localization.py"
    if (-not (Test-Path $locScript)) {
        $conclusion = "BLOCKED"
        $blockedDetails = "Localization checker not found: $locScript"
        Add-Result $results "Localization Check" "BLOCKED" "localization-check.log" $blockedDetails
        Write-TestReport $reportPath $conclusion $results $TaskId $modProjectPath $infraFullPath $testPlanPath $failureDetails $blockedDetails $artifactDir
        exit 2
    }
    $loc = Run-Command "localization check" "python" @($locScript, "--project", $modProjectPath) $locLog
    if ($loc.ExitCode -ne 0) {
        if ($loc.ExitCode -eq 2) {
            $conclusion = "BLOCKED"
            $blockedDetails = "Localization checker could not run. See localization-check.log."
            Add-Result $results "Localization Check" "BLOCKED" "localization-check.log" $blockedDetails
            Write-TestReport $reportPath $conclusion $results $TaskId $modProjectPath $infraFullPath $testPlanPath $failureDetails $blockedDetails $artifactDir
            exit 2
        }
        $conclusion = "FAILED"
        $failureDetails = "Localization check failed. See localization-check.log."
        Add-Result $results "Localization Check" "FAILED" "localization-check.log" $failureDetails
        Write-TestReport $reportPath $conclusion $results $TaskId $modProjectPath $infraFullPath $testPlanPath $failureDetails $blockedDetails $artifactDir
        exit 1
    }
    Add-Result $results "Localization Check" "PASSED" "localization-check.log"

    $deployLog = Join-Path $artifactDir "deploy.log"
    if ($SkipDeploy) {
        Set-Content -Path $deployLog -Value "Skipped by -SkipDeploy" -Encoding UTF8
        Add-Result $results "Deploy Mod" "SKIPPED" "deploy.log"
    } elseif ([string]::IsNullOrWhiteSpace($GameModsPath)) {
        Set-Content -Path $deployLog -Value "GameModsPath not provided; deploy skipped." -Encoding UTF8
        Add-Result $results "Deploy Mod" "BLOCKED" "deploy.log" "GameModsPath not provided."
        if (-not $SkipGameSmoke) {
            $conclusion = "BLOCKED"
            $blockedDetails = "GameModsPath not provided. Use -GameModsPath or -SkipDeploy -SkipGameSmoke."
            Write-TestReport $reportPath $conclusion $results $TaskId $modProjectPath $infraFullPath $testPlanPath $failureDetails $blockedDetails $artifactDir
            exit 2
        }
    } else {
        $modsFullPath = Resolve-FullPath $GameModsPath
        Ensure-Directory $modsFullPath
        $releaseDirs = Get-ChildItem -Path $modProjectPath -Directory -Recurse -ErrorAction SilentlyContinue | Where-Object { $_.FullName -match "bin.*(Release|Debug)" } | Select-Object -First 1
        if ($null -eq $releaseDirs) {
            $conclusion = "BLOCKED"
            $blockedDetails = "No build output directory found for deployment."
            Set-Content -Path $deployLog -Value $blockedDetails -Encoding UTF8
            Add-Result $results "Deploy Mod" "BLOCKED" "deploy.log" $blockedDetails
            Write-TestReport $reportPath $conclusion $results $TaskId $modProjectPath $infraFullPath $testPlanPath $failureDetails $blockedDetails $artifactDir
            exit 2
        }
        $target = Join-Path $modsFullPath "GawainMod"
        Ensure-Directory $target
        Copy-Item -Path (Join-Path $releaseDirs.FullName "*") -Destination $target -Recurse -Force
        Set-Content -Path $deployLog -Value "Copied $($releaseDirs.FullName) to $target" -Encoding UTF8
        Add-Result $results "Deploy Mod" "PASSED" "deploy.log"
    }

    $launchLog = Join-Path $artifactDir "launch.log"
    if ($SkipLaunchGame -or $SkipGameSmoke) {
        Set-Content -Path $launchLog -Value "Skipped game launch." -Encoding UTF8
        Add-Result $results "Launch Game" "SKIPPED" "launch.log"
    } else {
        Start-Process "steam://rungameid/$SteamAppId"
        Set-Content -Path $launchLog -Value "Started steam://rungameid/$SteamAppId" -Encoding UTF8
        Add-Result $results "Launch Game" "PASSED" "launch.log"
    }

    if ($SkipGameSmoke) {
        Add-Result $results "Game Smoke" "SKIPPED" "game-smoke.log" "Skipped by -SkipGameSmoke"
        Write-TestReport $reportPath $conclusion $results $TaskId $modProjectPath $infraFullPath $testPlanPath $failureDetails $blockedDetails $artifactDir
        exit 0
    }

    $cliLog = Join-Path $artifactDir "sts2-cli.log"
    $deadline = (Get-Date).AddSeconds($PingTimeoutSeconds)
    $pingPassed = $false
    while ((Get-Date) -lt $deadline) {
        $ping = & sts2 ping 2>&1
        $code = $LASTEXITCODE
        Add-Content -Path $cliLog -Value $ping
        Add-Content -Path $cliLog -Value "ExitCode: $code"
        if ($code -eq 0) { $pingPassed = $true; break }
        Start-Sleep -Seconds 3
    }
    if (-not $pingPassed) {
        $conclusion = "BLOCKED"
        $blockedDetails = "sts2 ping did not succeed within $PingTimeoutSeconds seconds."
        Add-Result $results "STS2-Cli-Mod Ping" "BLOCKED" "sts2-cli.log" $blockedDetails
        Write-TestReport $reportPath $conclusion $results $TaskId $modProjectPath $infraFullPath $testPlanPath $failureDetails $blockedDetails $artifactDir
        exit 2
    }
    Add-Result $results "STS2-Cli-Mod Ping" "PASSED" "sts2-cli.log"

    $gameSmokeLog = Join-Path $artifactDir "game-smoke.log"
    $statePath = Join-Path $stateDir "initial-state.json"
    $state = & sts2 state -p 2>&1
    $stateExit = $LASTEXITCODE
    $state | Set-Content -Path $statePath -Encoding UTF8
    $state | Set-Content -Path $gameSmokeLog -Encoding UTF8
    if ($stateExit -ne 0) {
        $conclusion = "FAILED"
        $failureDetails = "sts2 state -p failed. See game-smoke.log."
        Add-Result $results "Game Smoke" "FAILED" "game-smoke.log" $failureDetails
        Write-TestReport $reportPath $conclusion $results $TaskId $modProjectPath $infraFullPath $testPlanPath $failureDetails $blockedDetails $artifactDir
        exit 1
    }

    $joinedState = ($state -join "`n")
    foreach ($pattern in @("GAWAIN_", "MISSING", "missing localization", "KeyNotFound")) {
        if ($joinedState -match [regex]::Escape($pattern)) {
            $conclusion = "FAILED"
            $failureDetails = "Raw key or missing localization pattern found in game state: $pattern"
            Add-Result $results "No Raw Key" "FAILED" "state/initial-state.json" $failureDetails
            Write-TestReport $reportPath $conclusion $results $TaskId $modProjectPath $infraFullPath $testPlanPath $failureDetails $blockedDetails $artifactDir
            exit 1
        }
    }
    Add-Result $results "Game Smoke" "PASSED" "game-smoke.log; state/initial-state.json"
    Add-Result $results "No Raw Key" "PASSED" "state/initial-state.json"

    Write-TestReport $reportPath $conclusion $results $TaskId $modProjectPath $infraFullPath $testPlanPath $failureDetails $blockedDetails $artifactDir
    exit 0
}
catch {
    $conclusion = "BLOCKED"
    $blockedDetails = $_.Exception.Message
    Add-Result $results "Runner" "BLOCKED" "test-report.md" $blockedDetails
    Write-TestReport $reportPath $conclusion $results $TaskId $modProjectPath $infraFullPath $testPlanPath $failureDetails $blockedDetails $artifactDir
    Write-Error $_
    exit 2
}
