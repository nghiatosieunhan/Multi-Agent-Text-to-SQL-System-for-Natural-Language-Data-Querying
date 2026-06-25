# HƯỚNG DẪN ĐÁNH GIÁ LẠI HỆ THỐNG TEXT-TO-SQL

**Phiên bản điều chỉnh cho hệ thống hiện tại**  
**Mục tiêu:** tạo một bộ kết quả sạch, có thể dùng trực tiếp trong thesis, sau các thay đổi mới của pipeline: QuerySpec chặt hơn, FAISS few-shot split isolation, soft semantic validator, structured validation report, executor safety guard, cache SQL đã execute, router clarification và cấu hình benchmark/demo tách biệt.

---

## 0. Nguyên tắc chung

Lần đánh giá này phải được xem là **một benchmark snapshot mới**. Không trộn:

- checkpoint cũ;
- output directory cũ;
- evaluator cũ;
- prompt cũ;
- model/provider khác;
- FAISS index cũ thiếu metadata `split`;
- Northwind artifact trước khi audit;
- kết quả Fast mode với Deep mode;
- kết quả cache với benchmark accuracy.

Các số đo metric chính sẽ được điền sau khi chạy benchmark. Tài liệu này chỉ chuẩn hóa quy trình chạy.

---

# 1. Phạm vi đánh giá

## 1.1. Dataset Northwind

Dataset Northwind đã được kiểm toán lại:

```text
File:   data/northwind_massive_100.json
SHA256: 65CB6FF6F6997F98462AAFFDE954ADA519B1CA4E8D1533479CCF5E1E10C42162
DB:     data/northwind/northwind.sqlite
Rows:   95 questions, IDs 1-95
```

Lưu ý:

- Bản mới vẫn giữ 95 ID và phân bố intent.
- 48/95 câu đã thay đổi question, literal hoặc gold SQL.
- Mọi artifact Northwind trước lần audit này là legacy artifact.
- Không dùng kết quả Northwind cũ trong các bảng R2-R5, R7 hoặc R8 của thesis.
- Nếu SHA256 thay đổi, phải coi đó là benchmark version mới.

Run audit cũ `test/evaluation_runs/20260619_2153` không phải final. Không sao chép kết quả của run đó vào thesis.

## 1.2. Dataset Chinook

Chinook VN 300 nên được chạy lại nếu có bất kỳ thay đổi nào trong:

- code pipeline;
- prompt;
- evaluator;
- model/provider;
- QuerySpec;
- validator;
- SQL generator;
- executor;
- few-shot retrieval;
- evaluation profile.

Với hệ thống hiện tại, đã có nhiều thay đổi ở QuerySpec, validator, generator, executor và few-shot retrieval. Vì vậy, để bảng cross-database trong thesis sạch hơn, khuyến nghị:

```text
Chạy lại cả Northwind 95 và Chinook VN 300 trong cùng một RunTag.
```

---

# 2. Metric chính và metric diagnostic

## 2.1. Sáu metric chính cho Main Results

Chỉ dùng sáu metric sau trong bảng Main Results:

1. **Strict Execution Accuracy (Strict EX) + CI 95%**  
   Đây là metric correctness chính.

2. **Exec_OK**  
   Tỷ lệ generated SQL thực thi thành công. Exec_OK không đồng nghĩa với đúng logic.

3. **Mean latency và p95 latency**  
   Dùng để báo hiệu năng trung bình và tail latency.

4. **Mean total tokens/query**  
   Dùng để báo chi phí token trung bình.

5. **Mean LLM calls/query**  
   Dùng để báo overhead của multi-agent pipeline.

6. **Retry rate**  
   Tỷ lệ câu hỏi kích hoạt ít nhất một lần retry.

## 2.2. Metric diagnostic

Các metric sau chỉ dùng cho diagnostic hoặc appendix:

- `label_exact_match`;
- Relaxed EX;
- AST structure match/score;
- p50 latency;
- p95 tokens;
- generation attempts;
- error category;
- semantic warning count;
- validation error count;
- route distribution;
- QuerySpec failure count;
- clarification count;
- SQL exact consistency;
- AST consistency.

Không dùng semantic warning count làm metric accuracy chính. Warning chỉ là tín hiệu chẩn đoán.

---

# 3. Cấu hình benchmark hiện tại

## 3.1. Final benchmark profile khuyến nghị

Trong final benchmark, nên dùng cấu hình tương đương:

```python
{
    "cache_enabled": False,
    "few_shot_enabled": True,
    "few_shot_split": "train",
    "few_shot_threshold": 0.75,
    "query_spec_enabled": True,
    "validator_enabled": True,
    "semantic_validation_enabled": True,
    "semantic_warning_repair_enabled": False,
    "semantic_warning_hard_fail": False,
    "self_correction_enabled": True,
    "zero_row_correction_enabled": False,
}
```

Giải thích:

- `cache_enabled=False`: tránh reuse result trong accuracy benchmark.
- `few_shot_split="train"`: tránh data leakage từ validation/test.
- `semantic_warning_repair_enabled=False`: tránh false repair do semantic warning dựa trên heuristic/regex.
- `semantic_warning_hard_fail=False`: semantic warning không được làm SQL bị reject nếu không có safety/schema error.
- `zero_row_correction_enabled=False`: tránh làm lệch benchmark nếu gold result thật sự rỗng.

## 3.2. Demo/development profile khuyến nghị

Trong demo hoặc development có thể dùng:

```python
{
    "cache_enabled": True,
    "few_shot_enabled": True,
    "few_shot_split": "train",
    "query_spec_enabled": True,
    "validator_enabled": True,
    "semantic_validation_enabled": True,
    "semantic_warning_repair_enabled": True,
    "semantic_warning_hard_fail": False,
    "self_correction_enabled": True,
    "zero_row_correction_enabled": True,
}
```

Demo có thể bật semantic warning repair và zero-row correction vì UX quan trọng hơn tính bất biến của benchmark.

---

# 4. Điều kiện bắt buộc trước khi chạy final

Không chạy final benchmark cho đến khi hoàn thành các điều kiện sau.

## 4.1. Code và prompt

- Code/prompt/model/provider/temperature/evaluator đã freeze.
- Không tiếp tục tinh chỉnh trên Chinook 300 hoặc Northwind 95 sau khi coi chúng là final test.
- Nếu cần sửa tiếp, dùng dev/smoke subset riêng và tạo RunTag mới.

## 4.2. SQL generation regression

Phải đảm bảo:

- Không còn rule bắt buộc thêm `LIMIT 20` vào mọi SQL.
- Row/display limit nằm ở UI hoặc execution policy, không làm thay đổi benchmark result.
- Không có arbitrary fallback SQL kiểu `SELECT * FROM "EmployeeTerritories" LIMIT 5`.
- Nếu generator fail, ghi failure rõ ràng thay vì trả SQL phụ thuộc một bảng cụ thể.

Lệnh kiểm tra nhanh:

```powershell
rg -n "append LIMIT 20|SELECT \*.*LIMIT [0-9]+|sql_gen_fallback_used" src\agents\sql_generator.py
```

Kết quả mong đợi:

```text
Không còn rule bắt buộc LIMIT 20.
Không còn fallback phụ thuộc bảng cụ thể.
```

## 4.3. Few-shot retrieval

- FAISS index đã được rebuild sau khi thêm metadata `split`, `example_id`, `source`.
- Benchmark chỉ retrieve few-shot từ `split="train"`.
- Không index validation/test examples vào FAISS dùng cho final run.
- Auto-learned examples phải được đánh dấu riêng, ví dụ `split="auto_learned"`, và không được dùng trong final benchmark.

## 4.4. Validator và validation report

- `validation_report` có đủ:
  - `valid`;
  - `errors`;
  - `warnings`;
  - `risk_score`;
  - `repairable`.
- Safety/schema errors được xem là hard error hoặc repairable error.
- Semantic warnings không tự động làm `valid=False`.
- Nếu chỉ có semantic warnings và `semantic_warning_repair_enabled=False`, SQL vẫn đi tiếp sang executor.
- Semantic warning repair không được bật trong final benchmark.

## 4.5. Executor

- Executor có final safety guard.
- Chỉ chạy SQL bắt đầu bằng `SELECT` hoặc `WITH`.
- Chặn destructive keywords.
- Chặn multiple statements.
- Cache chỉ lưu SQL đã execute thành công, tức `sql_to_exec`.
- `SELECT TOP n` phải được chuyển thành `LIMIT n` mà không làm mất top-N.

## 4.6. Cache

- Accuracy benchmark phải dùng `full_no_cache`.
- `cache_hits = 0` trong mọi final accuracy run.
- Semantic cache chỉ được đánh giá riêng bằng `evaluate_cache.py`.

---

# 5. Chuẩn bị môi trường và RunTag

Chạy từ thư mục gốc repository.

```powershell
venv\Scripts\python.exe -m pip install -r requirements.txt
venv\Scripts\python.exe -m pytest tests\test_evaluation_framework.py tests\test_semantic_cache.py -q
```

Ghi lại trạng thái code:

```powershell
git rev-parse HEAD
git status --short
```

Tạo thư mục run mới:

```powershell
$RunTag = Get-Date -Format "yyyyMMdd_HHmm"
$OutputDir = "test\evaluation_runs\$RunTag"
New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null

Write-Host "Run tag: $RunTag"
Write-Host "Output:  $OutputDir"
```

Phải lưu lại:

- RunTag;
- commit hash;
- git diff nếu worktree dirty;
- model/provider/version;
- temperature;
- OS/hardware;
- thời điểm chạy;
- pricing assumptions nếu tính cost.

---

# 6. Smoke test trước khi full benchmark

## 6.1. Northwind smoke test 5 câu

```powershell
venv\Scripts\python.exe test\evaluate_v2.py `
  --data data\northwind_massive_100.json `
  --db data\northwind\northwind.sqlite `
  --dataset-type northwind `
  --profile full_no_cache `
  --analysis-mode deep `
  --limit 5 `
  --seed 42 `
  --clear-checkpoint `
  --output-dir $OutputDir `
  --name "smoke_northwind_5_$RunTag"
```

## 6.2. Acceptance checks cho smoke test

Chỉ tiếp tục nếu:

- JSON và CSV được tạo đầy đủ.
- `metadata.profile = full_no_cache`.
- `cache_hits = 0`.
- Generated SQL không có arbitrary fallback.
- Không có `LIMIT 20` bất thường.
- Telemetry có latency, token, LLM calls.
- `validation_report` xuất hiện trong JSON result.
- CSV/JSON có:
  - `semantic_warning_count`;
  - `validation_error_count`;
  - `selected_route`;
  - `route_reason`;
  - `query_spec_failed`;
  - `clarification_needed`.
- Nếu chỉ có semantic warnings, SQL vẫn được execute khi `semantic_warning_repair_enabled=False`.
- Nếu có safety/schema error, SQL không được execute trực tiếp.
- Không có lỗi credentials/quota lặp lại.

Nếu smoke test lỗi, sửa trên dev subset rồi tạo RunTag mới. Không resume run đã chạy bằng code khác.

---

# 7. Final benchmark

Final benchmark dùng **Deep mode** để đo end-to-end, bao gồm Result Formatter.

## 7.1. Northwind 95 audited

```powershell
venv\Scripts\python.exe test\evaluate_v2.py `
  --data data\northwind_massive_100.json `
  --db data\northwind\northwind.sqlite `
  --dataset-type northwind `
  --profile full_no_cache `
  --analysis-mode deep `
  --seed 42 `
  --clear-checkpoint `
  --output-dir $OutputDir `
  --name "northwind_95_audited_final_$RunTag"
```

## 7.2. Chinook VN 300

Vì hệ thống đã thay đổi nhiều, khuyến nghị chạy lại Chinook trong cùng RunTag:

```powershell
venv\Scripts\python.exe test\evaluate_v2.py `
  --data data\data_vn.json `
  --db data\chinook\Chinook_VN.sqlite `
  --dataset-type chinook_vn `
  --profile full_no_cache `
  --analysis-mode deep `
  --seed 42 `
  --clear-checkpoint `
  --output-dir $OutputDir `
  --name "chinook_vn_300_final_$RunTag"
```

## 7.3. Resume policy

Chỉ dùng `--resume` khi:

- code không đổi;
- prompt không đổi;
- model/provider không đổi;
- configuration không đổi;
- output-dir và name giữ nguyên.

Không dùng đồng thời:

```text
--resume
--clear-checkpoint
```

Nếu có code change giữa chừng, tạo RunTag mới và chạy lại từ đầu.

---

# 8. Kiểm tra kết quả final

Đọc sáu metric chính:

```powershell
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
```

## 8.1. Acceptance checks

- Northwind có đúng 95 result.
- Chinook có đúng 300 result nếu chạy lại.
- Metadata hoặc run log ghi đúng SHA256 Northwind.
- `cache_hits = 0` trong mọi accuracy run.
- Không resume từ checkpoint của run khác.
- JSON/CSV có cùng số record và cùng ID.
- Strict EX lấy từ evaluator v2, không lấy `judge_match`.
- Không trộn Fast-mode latency với Deep-mode latency.
- Không có `system_error` hoặc exception `__end__`.
- Generated SQL chỉ có LIMIT khi câu hỏi/gold yêu cầu top-N, giới hạn cụ thể hoặc superlative tương đương MIN/MAX.
- Không có arbitrary fallback.
- Semantic warnings không gây hard fail nếu không có safety/schema errors.
- `semantic_warning_repair_enabled=False` trong final benchmark.
- `zero_row_correction_enabled=False` trong final benchmark.
- `validation_report` được lưu trong JSON.
- `semantic_warning_count`, `validation_error_count`, route diagnostics được lưu trong CSV/JSON.

Nếu kết quả bất thường, giữ artifact để audit nhưng không đưa vào thesis. Phân tích trên dev subset, sửa code, tạo RunTag mới và chạy lại từ đầu.

---

# 9. Ablation sáu profile

Ablation dùng **Fast mode** và fixed stratified subset 80 câu từ Northwind audited.

Profiles:

```text
single_zero_shot
single_structured
full_no_cache
no_rag
no_planner
no_validator
```

## 9.1. Dry-run

```powershell
venv\Scripts\python.exe test\run_ablation.py `
  --data data\northwind_massive_100.json `
  --db data\northwind\northwind.sqlite `
  --dataset-type northwind `
  --questions 80 `
  --seed 42 `
  --output-dir $OutputDir `
  --dry-run
```

## 9.2. Run thật

```powershell
venv\Scripts\python.exe test\run_ablation.py `
  --data data\northwind_massive_100.json `
  --db data\northwind\northwind.sqlite `
  --dataset-type northwind `
  --questions 80 `
  --seed 42 `
  --output-dir $OutputDir

venv\Scripts\python.exe test\summarize_runs.py `
  --glob "$OutputDir\ablation_*.json" `
  --output "$OutputDir\ablation_summary.csv"
```

## 9.3. Acceptance checks

- Sáu profile dùng cùng 80 ID.
- Nếu đã sửa script, có file manifest `ablation_subset_*.json`.
- Không gọi 80 câu là power analysis.
- Không kết luận component tốt hơn nếu chênh lệch nhỏ và CI chồng lấn.
- Nếu profile `no_validator` tắt cả validator, executor safety guard vẫn phải bảo vệ DB.

---

# 10. Dynamic Bypass paired test

Dynamic Bypass dùng **Fast mode** và cùng 30 ID cho hai profile:

```text
auto_bypass
forced_pruning
```

## 10.1. Run

```powershell
venv\Scripts\python.exe test\run_dynamic_bypass.py `
  --data data\northwind_massive_100.json `
  --db data\northwind\northwind.sqlite `
  --dataset-type northwind `
  --questions 30 `
  --seed 42 `
  --output-dir $OutputDir
```

## 10.2. Acceptance checks

- Hai nhánh dùng đúng cùng 30 ID.
- Nếu đã sửa script, có file manifest `dynamic_bypass_subset_*.json`.
- Báo paired wins/losses nếu có thể.
- Báo Strict EX, LLM calls, total tokens, mean/p95 latency.
- Không gọi ngưỡng 30 bảng là universal optimum.
- Nếu auto-bypass giảm latency/calls nhưng tăng token, trình bày như trade-off.

---

# 11. Consistency 30 × 3

Consistency dùng **Fast mode** để đo core Text-to-SQL và tránh chi phí Result Formatter.

```powershell
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
```

Metric chính:

```text
strict_outcome_consistency
```

Diagnostic:

```text
sql_exact_consistency
structure_consistency
relaxed_outcome_consistency
all_correct
all_wrong
mixed
```

Lưu ý:

- Một câu sai cả 3 lần vẫn outcome-consistent.
- Vì vậy phải báo thêm all correct / all wrong / mixed.
- Không gọi hệ thống deterministic nếu SQL exact consistency chưa đạt 100%.

---

# 12. Semantic cache benchmark

Cache benchmark là thí nghiệm riêng. Không gộp vào accuracy benchmark.

## 12.1. Kiểm tra cache cases

Không chạy cache benchmark nếu `test/cache_cases.json` còn hard-negative collision.

Các collision từng được ghi nhận:

- C02 hard-negative trùng C01 base.
- C16 hard-negative trùng C17 base.
- C24 hard-negative trùng C15 base.
- C25 và C26 dùng hard-negative trùng base của nhau.

Kiểm tra bằng PowerShell:

```powershell
$Cases = Get-Content -Raw -Encoding UTF8 test\cache_cases.json | ConvertFrom-Json
$BaseMap = @{}
foreach ($Group in $Cases.groups) { $BaseMap[$Group.base] = $Group.id }
foreach ($Group in $Cases.groups) {
  if ($BaseMap.ContainsKey($Group.hard_negative)) {
    Write-Error "$($Group.id) hard-negative trùng base của $($BaseMap[$Group.hard_negative])"
  }
}
```

Nếu đã bổ sung check trực tiếp vào `evaluate_cache.py`, script phải dừng khi phát hiện collision.

## 12.2. Chạy threshold sweep và E2E

```powershell
venv\Scripts\python.exe test\evaluate_cache.py `
  --cases test\cache_cases.json `
  --e2e `
  --db data\northwind\northwind.sqlite `
  --dataset-type northwind `
  --e2e-limit 10 `
  --output-dir $OutputDir
```

## 12.3. Quy tắc chọn threshold

1. Loại mọi cấu hình có false hit trên test set sạch.
2. Trong nhóm còn lại, ưu tiên precision.
3. Sau đó mới tối ưu semantic recall và lookup latency.
4. Nếu không có cấu hình an toàn, tắt semantic hit và chỉ giữ exact cache.

Không mặc định Jaccard `0.92`. `0.92` hiện là cosine threshold trong config; Jaccard phải được chọn từ benchmark sạch.

---

# 13. Script chạy đánh giá

## 13.1. Không nên dùng `run_all_evals.ps1` cũ cho final

`run_all_evals.ps1` cũ không phù hợp cho final benchmark hiện tại vì:

- không tạo `$RunTag` và `$OutputDir` riêng;
- không dùng `--clear-checkpoint`;
- không truyền `--output-dir` cho mọi run;
- tên output có thể đè hoặc trộn với artifact cũ;
- cache được chạy thành nhiều lệnh rời, dễ ghi đè file;
- không có smoke test và acceptance checks.

Có thể giữ file này làm legacy/dev script, nhưng không dùng cho thesis final.

## 13.2. `run_final_evals.ps1` gần đúng nhưng cần chỉnh

Script `run_final_evals.ps1` đã tốt hơn vì có `$RunTag` và `$OutputDir`. Tuy nhiên nên chỉnh thêm:

- thêm pytest/preflight trước khi chạy;
- thêm smoke test 5 câu trước full run;
- chạy lại Chinook VN 300 trong cùng RunTag;
- truyền `--analysis-mode fast` rõ ràng cho ablation nếu script `run_ablation.py` chưa set;
- truyền `--analysis-mode fast` rõ ràng cho consistency;
- chạy `evaluate_cache.py` một lần với `--e2e` nếu không muốn file threshold bị ghi đè;
- thêm bước in summary sáu metric chính sau final;
- thêm ghi chú không dùng script nếu worktree dirty mà chưa snapshot.

Một script đã điều chỉnh được cung cấp kèm theo file này dưới tên:

```text
run_final_evals_current.ps1
```

---

# 14. File output dùng cho thesis

Trong mỗi `$OutputDir`, nên có:

```text
northwind_95_audited_final_<RunTag>.json
northwind_95_audited_final_<RunTag>.csv
chinook_vn_300_final_<RunTag>.json
chinook_vn_300_final_<RunTag>.csv
ablation_*.json
ablation_summary.csv
ablation_subset_*.json
dynamic_bypass_*.json
dynamic_bypass_*_summary.csv
dynamic_bypass_subset_*.json
consistency_*.json
consistency_*.csv
semantic_cache_thresholds.csv
semantic_cache_evaluation.json
```

Ánh xạ vào thesis:

| Thesis table | Nguồn dữ liệu |
|---|---|
| R1 | metadata của final JSON + run log |
| R2a | `summary.strict_ex`, CI95, Exec_OK, retry rate |
| R2b | latency, tokens, LLM calls |
| R3 | `summary.by_intent` |
| R4 | `ablation_summary.csv` |
| R5 | `dynamic_bypass_*_summary.csv` |
| R6a/R6b | `semantic_cache_thresholds.csv`, `semantic_cache_evaluation.json` |
| R7 | consistency JSON/CSV + final reliability diagnostics |
| R8 | final CSV/JSON error categories |

Không trộn số liệu giữa các RunTag.

---

# 15. Checklist trước khi chốt thesis

- [ ] Code/prompt/model/provider/evaluator đã freeze.
- [ ] Dataset Northwind đúng SHA256.
- [ ] FAISS index đã rebuild với metadata split.
- [ ] Benchmark dùng `few_shot_split="train"`.
- [ ] Tests pass.
- [ ] Smoke test pass.
- [ ] Không arbitrary fallback SQL.
- [ ] Không LIMIT 20 bắt buộc.
- [ ] `semantic_warning_repair_enabled=False` trong final benchmark.
- [ ] `semantic_warning_hard_fail=False` trong final benchmark.
- [ ] `zero_row_correction_enabled=False` trong final benchmark.
- [ ] `validation_report/errors/warnings` được ghi vào JSON.
- [ ] `semantic_warning_count` và `validation_error_count` được ghi vào CSV/JSON.
- [ ] Route diagnostics được ghi vào CSV/JSON.
- [ ] Final Northwind đủ 95 câu.
- [ ] Final Chinook đủ 300 câu nếu chạy lại.
- [ ] Cache hit bằng 0 trong accuracy runs.
- [ ] Main Results chỉ dùng sáu metric lõi.
- [ ] Fast và Deep mode được báo riêng.
- [ ] Ablation dùng cùng 80 ID và seed 42.
- [ ] Dynamic Bypass dùng cùng 30 ID ở hai nhánh.
- [ ] Consistency dùng cùng 30 câu × 3 lần.
- [ ] Consistency có all correct / all wrong / mixed.
- [ ] Cache cases không còn collision.
- [ ] Cache E2E đã chạy nếu viết phần cache cold/warm.
- [ ] Pricing bằng 0 thì không báo estimated cost.
- [ ] Mọi bảng ghi RunTag, model/version, profile, mode và ngày chạy.
- [ ] Không dùng Northwind pre-audit trong R2-R5, R7, R8.

---

# 16. Kết luận

File hướng dẫn cũ đã khá chuẩn về tinh thần benchmark sạch: freeze code, tách RunTag, tắt cache, dùng Strict EX làm metric chính và không trộn artifact cũ. Tuy nhiên, hệ thống hiện tại đã có thêm QuerySpec chặt hơn, soft semantic validator, few-shot split isolation, executor safety guard và benchmark-safe options. Vì vậy protocol cần được điều chỉnh để ghi nhận và kiểm soát các thành phần mới này.

Quy trình hiện tại nên là:

```text
Freeze → Preflight → Smoke test → Northwind final → Chinook final → Audit final → Ablation → Dynamic Bypass → Consistency → Cache benchmark → Thesis tables
```

Với quy trình này, bộ kết quả tạo ra sẽ sạch hơn, dễ audit hơn và phù hợp hơn để đưa vào thesis.
