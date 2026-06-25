$ErrorActionPreference = "Stop"

Write-Host "=========================================="
Write-Host "Text-to-SQL Final Evaluation - Current System"
Write-Host "=========================================="

# 0. Run tag and output directory
$RunTag = Get-Date -Format "yyyyMMdd_HHmm"
$OutputDir = "test\evaluation_runs\$RunTag"
New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null

Write-Host "Run tag: $RunTag"
Write-Host "Output:  $OutputDir"

Write-Host "`n[0] Code snapshot"
git rev-parse HEAD
git status --short

Write-Host "`n[1] Unit tests / preflight"
venv\Scripts\python.exe -m pytest tests\test_evaluation_framework.py tests\test_semantic_cache.py -q

Write-Host "`n[2] Smoke test Northwind 5"
venv\Scripts\python.exe test\evaluate_v2.py `
  --data data\northwind_massive_100.json `
  --db data\northwind\northwind.sqlite `
  --dataset-type northwind `
  --profile full_no_cache `
  --analysis-mode fast `
  --limit 5 `
  --seed 42 `
  --clear-checkpoint `
  --output-dir $OutputDir `
  --name "smoke_northwind_5_$RunTag"

Write-Host "`n[3] Northwind 95 audited final"
venv\Scripts\python.exe test\evaluate_v2.py `
  --data data\northwind_massive_100.json `
  --db data\northwind\northwind.sqlite `
  --dataset-type northwind `
  --profile full_no_cache `
  --analysis-mode fast `
  --seed 42 `
  --clear-checkpoint `
  --output-dir $OutputDir `
  --name "northwind_95_audited_final_$RunTag"

Write-Host "`n[4] Chinook VN 300 final"
venv\Scripts\python.exe test\evaluate_v2.py `
  --data data\data_vn.json `
  --db data\chinook\Chinook_VN.sqlite `
  --dataset-type chinook_vn `
  --profile full_no_cache `
  --analysis-mode fast `
  --seed 42 `
  --clear-checkpoint `
  --output-dir $OutputDir `
  --name "chinook_vn_300_final_$RunTag"

Write-Host "`n[5] Ablation 80 - dry run"
venv\Scripts\python.exe test\run_ablation.py `
  --data data\northwind_massive_100.json `
  --db data\northwind\northwind.sqlite `
  --dataset-type northwind `
  --questions 80 `
  --seed 42 `
  --output-dir $OutputDir `
  --dry-run

Write-Host "`n[6] Ablation 80 - run"
venv\Scripts\python.exe test\run_ablation.py `
  --data data\northwind_massive_100.json `
  --db data\northwind\northwind.sqlite `
  --dataset-type northwind `
  --questions 80 `
  --seed 42 `
  --output-dir $OutputDir

Write-Host "`n[7] Summarize ablation"
venv\Scripts\python.exe test\summarize_runs.py `
  --glob "$OutputDir\ablation_*.json" `
  --output "$OutputDir\ablation_summary.csv"

Write-Host "`n[8] Dynamic Bypass 30"
venv\Scripts\python.exe test\run_dynamic_bypass.py `
  --data data\northwind_massive_100.json `
  --db data\northwind\northwind.sqlite `
  --dataset-type northwind `
  --questions 30 `
  --seed 42 `
  --output-dir $OutputDir

Write-Host "`n[9] Consistency 30x3"
venv\Scripts\python.exe test\evaluate_consistency.py `
  --data data\northwind_massive_100.json `
  --db data\northwind\northwind.sqlite `
  --dataset-type northwind `
  --profile full_no_cache `
  --analysis-mode fast `
  --questions 30 `
  --repeats 3 `
  --seed 42 `
  --output-dir $OutputDir `
  --name "consistency_northwind_30x3_$RunTag"

Write-Host "`n[10] Semantic cache threshold + E2E"
venv\Scripts\python.exe test\evaluate_cache.py `
  --cases test\cache_cases.json `
  --e2e `
  --db data\northwind\northwind.sqlite `
  --dataset-type northwind `
  --e2e-limit 10 `
  --output-dir $OutputDir

Write-Host "`n[11] Print final summary"
$FinalFiles = @(
  "$OutputDir\northwind_95_audited_final_$RunTag.json",
  "$OutputDir\chinook_vn_300_final_$RunTag.json"
)

foreach ($File in $FinalFiles) {
  if (-not (Test-Path -LiteralPath $File)) { continue }
  $Run = Get-Content -Raw -Encoding UTF8 -LiteralPath $File | ConvertFrom-Json
  [pscustomobject]@{
    Run             = $Run.metadata.name
    Questions       = $Run.summary.total
    Strict_EX       = $Run.summary.strict_ex
    CI95            = ($Run.summary.strict_ex_ci95 -join " - ")
    Exec_OK         = $Run.summary.exec_ok
    Mean_Latency_ms = $Run.summary.latency_ms.mean
    P95_Latency_ms  = $Run.summary.latency_ms.p95
    Mean_Tokens     = $Run.summary.tokens.mean_per_query
    Mean_LLM_Calls  = $Run.summary.llm_calls.mean_per_query
    Retry_Rate      = $Run.summary.retry_rate
    Cache_Hits      = $Run.summary.cache_hits
  } | Format-List
}

Write-Host "`nAll runs completed. Output in $OutputDir"
