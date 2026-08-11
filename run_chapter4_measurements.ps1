param(
    [Parameter(Mandatory = $true)]
    [ValidateSet(
        "spider-smoke",
        "spider-100",
        "northwind-final",
        "chinook-final",
        "ablation-smoke",
        "ablation-final",
        "ablation-no-validator-final",
        "consistency",
        "cache",
        "error-audit",
        "manifest"
    )]
    [string]$Phase,

    [string]$RunTag = (Get-Date -Format "yyyyMMdd_HHmm"),
    [double]$Delay = 3.0
)

$ErrorActionPreference = "Stop"
$Python = "venv\Scripts\python.exe"
$BaseOutputDir = "test\evaluation_runs\chapter4_$RunTag"
$OutputDir = Join-Path $BaseOutputDir $Phase
New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null

switch ($Phase) {
    "northwind-final" {
        & $Python test\evaluate_v2.py `
            --data data\northwind_test_100_balanced_fixed.json `
            --db data\northwind\northwind.sqlite `
            --dataset-type northwind `
            --profile full_no_cache `
            --analysis-mode fast `
            --seed 42 `
            --delay $Delay `
            --clear-checkpoint `
            --output-dir $OutputDir `
            --name northwind_chapter4_final
    }

    "chinook-final" {
        & $Python test\evaluate_v2.py `
            --data data\data_vn.json `
            --db data\chinook\Chinook_VN.sqlite `
            --dataset-type chinook_vn `
            --profile full_no_cache `
            --analysis-mode fast `
            --seed 42 `
            --delay $Delay `
            --clear-checkpoint `
            --output-dir $OutputDir `
            --name chinook_vn_chapter4_final
    }

    "spider-smoke" {
        & $Python test\evaluate_v2.py `
            --data data\spider_dev_100.json `
            --db auto `
            --db-root data\spider\spider_data\database `
            --dataset-type spider `
            --profile full_no_cache `
            --analysis-mode fast `
            --seed 42 `
            --stratified-limit 30 `
            --delay $Delay `
            --clear-checkpoint `
            --output-dir $OutputDir `
            --name spider_dev_smoke_30_v6
    }

    "spider-100" {
        & $Python test\evaluate_v2.py `
            --data data\spider_dev_100.json `
            --db auto `
            --db-root data\spider\spider_data\database `
            --dataset-type spider `
            --profile full_no_cache `
            --analysis-mode fast `
            --seed 42 `
            --delay $Delay `
            --clear-checkpoint `
            --output-dir $OutputDir `
            --name spider_dev_100_final_candidate
    }

    "ablation-smoke" {
        & $Python test\run_ablation.py `
            --data data\northwind_test_100_balanced_fixed.json `
            --db data\northwind\northwind.sqlite `
            --dataset-type northwind `
            --questions 20 `
            --seed 42 `
            --delay $Delay `
            --output-dir $OutputDir
    }

    "ablation-final" {
        & $Python test\run_ablation.py `
            --data data\northwind_test_100_balanced_fixed.json `
            --db data\northwind\northwind.sqlite `
            --dataset-type northwind `
            --questions 80 `
            --seed 42 `
            --delay $Delay `
            --output-dir $OutputDir
    }

    "ablation-no-validator-final" {
        & $Python test\evaluate_v2.py `
            --data data\northwind_test_100_balanced_fixed.json `
            --db data\northwind\northwind.sqlite `
            --dataset-type northwind `
            --profile no_validator `
            --stratified-limit 80 `
            --analysis-mode fast `
            --seed 42 `
            --delay $Delay `
            --clear-checkpoint `
            --output-dir $OutputDir `
            --name ablation_northwind_test_100_balanced_fixed_no_validator
    }

    "consistency" {
        & $Python test\evaluate_consistency.py `
            --data data\northwind_test_100_balanced_fixed.json `
            --db data\northwind\northwind.sqlite `
            --dataset-type northwind `
            --profile full_no_cache `
            --questions 30 `
            --repeats 3 `
            --seed 42 `
            --analysis-mode fast `
            --delay $Delay `
            --output-dir $OutputDir `
            --name consistency_northwind_30x3
    }

    "cache" {
        & $Python test\evaluate_cache.py `
            --cases test\cache_cases.json `
            --output-dir $OutputDir `
            --e2e `
            --db data\northwind\northwind.sqlite `
            --dataset-type northwind `
            --e2e-limit 10 `
            --analysis-mode fast
    }

    "error-audit" {
        $ChinookRun = Join-Path $BaseOutputDir "chinook-final\chinook_vn_chapter4_final.json"
        $NorthwindRun = Join-Path $BaseOutputDir "northwind-final\northwind_chapter4_final.json"
        if (-not (Test-Path -LiteralPath $ChinookRun)) {
            $ChinookRun = "test\evaluation_runs\chinook_vn_profiled_v2.json"
        }
        if (-not (Test-Path -LiteralPath $NorthwindRun)) {
            $NorthwindRun = "test\evaluation_runs\northwind_profiled_v1.json"
        }
        $SpiderRun = Join-Path $BaseOutputDir "spider-100\spider_dev_100_final_candidate.json"
        $AuditArgs = @(
            "test\export_error_audit.py",
            "--run", $ChinookRun,
            "--run", $NorthwindRun
        )
        if (Test-Path -LiteralPath $SpiderRun) {
            $AuditArgs += @("--run", $SpiderRun)
        }
        $AuditArgs += @("--output", "$OutputDir\main_error_audit.csv")
        & $Python @AuditArgs
    }

    "manifest" {
        $Commit = git rev-parse HEAD
        $Status = git status --short
        $PythonVersion = & $Python --version
        $CpuName = $env:PROCESSOR_IDENTIFIER
        try {
            Add-Type -AssemblyName Microsoft.VisualBasic
            $ComputerInfo = New-Object Microsoft.VisualBasic.Devices.ComputerInfo
            $MemoryGb = [math]::Round($ComputerInfo.TotalPhysicalMemory / 1GB, 2)
        }
        catch {
            $MemoryGb = $null
        }
        $VertexLocation = & $Python -c "from src.config import config; print(config.GOOGLE_CLOUD_LOCATION)"
        $ArtifactHashes = [ordered]@{}
        @(
            "data\northwind_test_100_balanced_fixed.json",
            "data\northwind\northwind.sqlite",
            "data\data_vn.json",
            "data\chinook\Chinook_VN.sqlite",
            "data\spider_dev_100.json",
            "data\spider\spider_data\tables.json"
        ) | ForEach-Object {
            if (Test-Path -LiteralPath $_) {
                $ArtifactHashes[$_] = (Get-FileHash -LiteralPath $_ -Algorithm SHA256).Hash
            }
        }
        $Manifest = [ordered]@{
            run_tag = $RunTag
            generated_at = (Get-Date).ToString("o")
            git_commit = $Commit
            git_status = @($Status)
            workspace_dirty = (@($Status).Count -gt 0)
            python = $PythonVersion
            model_provider = "Google Cloud Vertex AI"
            model = "gemini-2.5-pro"
            vertex_location = $VertexLocation
            router_queryspec_temperature = 0.0
            sql_generator_temperature = 0.1
            analysis_mode = "fast"
            accuracy_profile = "full_no_cache"
            seed = 42
            operating_system = [System.Environment]::OSVersion.VersionString
            processor_count = [System.Environment]::ProcessorCount
            processor = $CpuName
            memory_gb = $MemoryGb
            artifact_sha256 = $ArtifactHashes
        }
        $Manifest | ConvertTo-Json -Depth 5 | Set-Content `
            -LiteralPath "$OutputDir\experiment_manifest.json" `
            -Encoding UTF8
        & $Python -m pip freeze | Set-Content `
            -LiteralPath "$OutputDir\requirements_frozen.txt" `
            -Encoding UTF8
        $SnapshotDir = Join-Path $OutputDir "evaluation_source_snapshot"
        New-Item -ItemType Directory -Force -Path $SnapshotDir | Out-Null
        $SourceSnapshot = Join-Path $SnapshotDir "src"
        $TestSnapshot = Join-Path $SnapshotDir "test"
        New-Item -ItemType Directory -Force -Path $SourceSnapshot | Out-Null
        Copy-Item -Path "src\*" -Destination $SourceSnapshot -Recurse -Force
        New-Item -ItemType Directory -Force -Path $TestSnapshot | Out-Null
        @(
            "test\evaluate_v2.py",
            "test\evaluation_metrics.py",
            "test\run_ablation.py",
            "test\evaluate_consistency.py",
            "test\evaluate_cache.py",
            "test\export_error_audit.py",
            "test\cache_cases.json"
        ) | ForEach-Object {
            Copy-Item -LiteralPath $_ -Destination $TestSnapshot -Force
        }
        Copy-Item -LiteralPath "run_chapter4_measurements.ps1" -Destination $SnapshotDir -Force
    }
}

if ($LASTEXITCODE -and $LASTEXITCODE -ne 0) {
    throw "Phase '$Phase' failed with exit code $LASTEXITCODE"
}

Write-Host "Completed phase '$Phase'. Output: $OutputDir"
