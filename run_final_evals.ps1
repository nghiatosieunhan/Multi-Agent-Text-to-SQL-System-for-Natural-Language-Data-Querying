$ErrorActionPreference = "Continue"

$RunTag = Get-Date -Format "yyyyMMdd_HHmm"
$OutputDir = "test\evaluation_runs\$RunTag"
New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null

Write-Host "Run tag: $RunTag"
Write-Host "Output:  $OutputDir"

Write-Host "`n[1] Northwind 95 audited final..."
venv\Scripts\python.exe test\evaluate_v2.py --data data\northwind_massive_100.json --db data\northwind\northwind.sqlite --dataset-type northwind --profile full_no_cache --analysis-mode deep --seed 42 --clear-checkpoint --output-dir $OutputDir --name "northwind_95_audited_final_$RunTag"

Write-Host "`n[2] Module experiments: Ablation 80"
venv\Scripts\python.exe test\run_ablation.py --data data\northwind_massive_100.json --db data\northwind\northwind.sqlite --dataset-type northwind --questions 80 --seed 42 --output-dir $OutputDir

Write-Host "`n[3] Module experiments: Summarizing Ablation Results"
venv\Scripts\python.exe test\summarize_runs.py --glob "$OutputDir\ablation_*.json" --output "$OutputDir\ablation_summary.csv"

Write-Host "`n[4] Module experiments: Dynamic Bypass 30"
venv\Scripts\python.exe test\run_dynamic_bypass.py --data data\northwind_massive_100.json --db data\northwind\northwind.sqlite --dataset-type northwind --questions 30 --seed 42 --output-dir $OutputDir

Write-Host "`n[5] Module experiments: Consistency 30x3"
venv\Scripts\python.exe test\evaluate_consistency.py --data data\northwind_massive_100.json --db data\northwind\northwind.sqlite --dataset-type northwind --profile full_no_cache --questions 30 --repeats 3 --seed 42 --output-dir $OutputDir

Write-Host "`n[6] Semantic Cache Tests"
venv\Scripts\python.exe test\evaluate_cache.py --output-dir $OutputDir
venv\Scripts\python.exe test\evaluate_cache.py --e2e --db data\northwind\northwind.sqlite --dataset-type northwind --e2e-limit 10 --output-dir $OutputDir

Write-Host "`nAll runs completed. Output in $OutputDir"
