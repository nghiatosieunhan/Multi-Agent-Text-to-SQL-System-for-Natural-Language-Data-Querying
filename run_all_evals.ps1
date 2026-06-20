$ErrorActionPreference = "Continue"

Write-Host "=========================================="
Write-Host "1. Testing framework & Cache"
Write-Host "=========================================="
venv\Scripts\python.exe -m pytest tests\test_evaluation_framework.py tests\test_semantic_cache.py -q

Write-Host "`n=========================================="
Write-Host "2. Running Northwind Massive Final (95)"
Write-Host "=========================================="
venv\Scripts\python.exe test\evaluate_v2.py --data data\northwind_massive_100.json --db data\northwind\northwind.sqlite --dataset-type northwind --profile full_no_cache --analysis-mode deep --name northwind_95_final

Write-Host "`n=========================================="
Write-Host "3. Running Chinook VN Final (300)"
Write-Host "=========================================="
venv\Scripts\python.exe test\evaluate_v2.py --data data\data_vn.json --db data\chinook\Chinook_VN.sqlite --dataset-type chinook_vn --profile full_no_cache --analysis-mode deep --name chinook_vn_300_final

Write-Host "`n=========================================="
Write-Host "4. Running Ablation Study (80)"
Write-Host "=========================================="
venv\Scripts\python.exe test\run_ablation.py --data data\northwind_massive_100.json --db data\northwind\northwind.sqlite --dataset-type northwind --questions 80 --seed 42

Write-Host "`n=========================================="
Write-Host "5. Summarizing Ablation Results"
Write-Host "=========================================="
venv\Scripts\python.exe test\summarize_runs.py

Write-Host "`n=========================================="
Write-Host "6. Running Dynamic Bypass (30)"
Write-Host "=========================================="
venv\Scripts\python.exe test\run_dynamic_bypass.py --data data\northwind_massive_100.json --db data\northwind\northwind.sqlite --dataset-type northwind --questions 30 --seed 42

Write-Host "`n=========================================="
Write-Host "7. Running Consistency (30x3)"
Write-Host "=========================================="
venv\Scripts\python.exe test\evaluate_consistency.py --data data\northwind_massive_100.json --db data\northwind\northwind.sqlite --dataset-type northwind --profile full_no_cache --questions 30 --repeats 3 --seed 42

Write-Host "`n=========================================="
Write-Host "8. Running Semantic Cache Tests"
Write-Host "=========================================="
venv\Scripts\python.exe test\evaluate_cache.py
venv\Scripts\python.exe test\evaluate_cache.py --e2e --db data\northwind\northwind.sqlite --dataset-type northwind --e2e-limit 10

Write-Host "`n=========================================="
Write-Host "ALL EVALUATIONS COMPLETED SUCCESSFULLY!"
Write-Host "=========================================="
