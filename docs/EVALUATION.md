# Hướng dẫn đánh giá lại hệ thống Text-to-SQL

Tài liệu này là protocol chính thức cho lần đánh giá lại sau khi sửa pipeline. Mục tiêu là tạo một bộ kết quả có thể dùng trực tiếp trong thesis, không trộn checkpoint, evaluator hoặc cấu hình của các lần chạy cũ.

## 0. Phạm vi lần đánh giá lại Northwind

`data/northwind_massive_100.json` đã được kiểm toán lại để khớp với `data/northwind/northwind.sqlite` ngày 2026-06-19. Bản mới vẫn giữ 95 ID và phân bố intent, nhưng 48/95 câu đã thay đổi question, literal hoặc gold SQL. Vì vậy, mọi artifact Northwind tạo trước lần sửa này chỉ là kết quả legacy và không được dùng trong các bảng R2-R5, R7 hoặc R8 của thesis.

Định danh dataset đã kiểm toán:

```text
File:   data/northwind_massive_100.json
SHA256: 65CB6FF6F6997F98462AAFFDE954ADA519B1CA4E8D1533479CCF5E1E10C42162
DB:     data/northwind/northwind.sqlite
Rows:   95 questions, IDs 1-95
```

Nếu hash thay đổi, phải coi đó là phiên bản benchmark mới, ghi lại hash mới và không resume checkpoint cũ.

Run `test/evaluation_runs/20260619_2153` là artifact audit, không phải final: Strict EX 35/95 (36,84%), Exec_OK 97,89%, 72/95 generated SQL chứa `LIMIT 20` và hai record bị `system_error: __end__`. Replay chỉ bỏ trailing `LIMIT 20` khôi phục 11 câu. Không sao chép số liệu của run này vào thesis.

### Kế hoạch chạy theo thứ tự

1. **Freeze**: chốt code, prompt, evaluator, model/provider, temperature và dataset hash.
2. **Preflight**: chạy unit test và smoke test 5 câu; kiểm tra cache tắt, không fallback tùy tiện và telemetry đầy đủ.
3. **Northwind final**: chạy đủ 95 câu bằng `full_no_cache`, Deep mode, seed 42.
4. **Kiểm toán final**: đối chiếu JSON/CSV, ID, Strict EX, Exec_OK, latency, token, LLM calls, retry và error category.
5. **Module experiments**: chạy lại ablation 80 câu, Dynamic Bypass 30 câu paired và consistency 30 x 3 vì đều lấy mẫu từ Northwind.
6. **Thesis update**: chỉ thay các placeholder Northwind sau khi toàn bộ artifact của cùng một run tag đã qua kiểm tra.

Chinook không cần chạy lại chỉ vì file Northwind thay đổi. Tuy nhiên, nếu code/prompt/model/evaluator dùng cho Northwind khác snapshot của run Chinook hiện tại, phải chạy lại Chinook để bảng cross-database dùng cùng cấu hình.

## 1. Bộ metric chính

Chỉ dùng sáu metric sau trong bảng Main Results:

1. **Strict Execution Accuracy (Strict EX) và CI 95%**: metric correctness chính.
2. **Exec_OK**: tỷ lệ generated SQL thực thi thành công; không đồng nghĩa với đúng logic.
3. **Mean và p95 latency**: hiệu năng trung bình và tail latency.
4. **Mean total tokens/query**: mức tiêu thụ token trung bình.
5. **Mean LLM calls/query**: overhead của pipeline/multi-agent.
6. **Retry rate**: tỷ lệ query kích hoạt ít nhất một retry.

Các metric sau vẫn được evaluator ghi lại nhưng chỉ dùng làm diagnostic hoặc appendix: `label_exact_match`, Relaxed EX, AST structure, p50 latency, p95 tokens, generation attempts và error categories.

Các thí nghiệm module-specific:

- Ablation tái sử dụng Strict EX, Exec_OK, latency, token và LLM calls; `Ablation Strict EX` không phải metric mới.
- Consistency ưu tiên `strict_outcome_consistency`; SQL exact và AST consistency là diagnostics.
- Semantic cache ưu tiên false-hit rate và semantic recall; không gộp cache vào accuracy benchmark.

## 2. Điều kiện bắt buộc trước khi chạy

Không chạy benchmark final cho đến khi hoàn thành các điều kiện sau:

- Bỏ rule bắt buộc thêm `LIMIT 20` vào mọi SQL. Row/display limit phải nằm ở UI hoặc execution policy, không được làm thay đổi result của benchmark.
- Không trả arbitrary fallback SQL như `SELECT * FROM "EmployeeTerritories" LIMIT 5` khi generator thất bại. Hãy báo failure hoặc dùng correction path có schema/question đầy đủ.
- Cache tắt trong benchmark accuracy (`full_no_cache`).
- Không bật `--llm-judge` cho kết quả chính.
- Chốt model/provider, prompt, temperature, evaluator và code trước khi chạy full set.
- Không tiếp tục tinh chỉnh trên Chinook 300 hoặc Northwind 95 sau khi đã coi chúng là final test. Mọi tinh chỉnh bổ sung phải dùng dev/smoke subset riêng.

Kiểm tra nhanh các regression đã biết:

```powershell
rg -n "append LIMIT 20|SELECT \*.*LIMIT [0-9]+|sql_gen_fallback_used" src\agents\sql_generator.py
```

Kết quả mong đợi: không còn rule bắt buộc `LIMIT 20` và không còn fallback phụ thuộc một bảng cụ thể. Các câu `LIMIT` phục vụ đúng yêu cầu người dùng hoặc sample-row introspection không phải regression.

## 3. Chuẩn bị môi trường và thư mục run

Chạy từ thư mục gốc repository:

```powershell
venv\Scripts\python.exe -m pip install -r requirements.txt
venv\Scripts\python.exe -m pytest tests\test_evaluation_framework.py tests\test_semantic_cache.py -q
```

Ghi lại trạng thái code và tạo thư mục mới cho lần chạy. Không ghi đè `test/evaluation_runs/` cũ:

```powershell
git rev-parse HEAD
git status --short

$RunTag = Get-Date -Format "yyyyMMdd_HHmm"
$OutputDir = "test\evaluation_runs\$RunTag"
New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null

Write-Host "Run tag: $RunTag"
Write-Host "Output:  $OutputDir"
```

Lưu `RunTag`, commit hash, model/version thực tế, hệ điều hành, phần cứng, thời điểm chạy và pricing assumptions vào nhật ký thesis. Nếu worktree đang dirty, phải lưu lại diff hoặc commit/snapshot dùng cho benchmark.

Không dùng `run_all_evals.ps1` cũ cho lần final này vì runner đó không tạo output directory riêng theo run tag.

## 4. Smoke test trước khi chạy full

Chạy 5 câu Northwind để kiểm tra credentials, output, telemetry và fallback:

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

Chỉ tiếp tục khi:

- JSON và CSV được tạo đầy đủ.
- `metadata.profile = full_no_cache` và `cache_hits = 0`.
- Generated SQL không chứa arbitrary fallback.
- Telemetry có latency, token và LLM calls.
- Không có lỗi credentials/quota lặp lại.

Nếu smoke test lỗi, sửa bằng dev subset rồi tạo `RunTag` mới. Không resume một run đã chạy bằng code khác.

## 5. Final benchmark

Final dùng Deep mode để đo end-to-end, gồm Result Formatter. Northwind audited là run bắt buộc. Chinook chỉ chạy lại khi code/prompt/model/evaluator khác snapshot của artifact Chinook đang giữ; mọi so sánh cross-database phải dùng cùng cấu hình.

### 5.1 Northwind 95 audited

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

### 5.2 Chinook VN 300 (conditional rerun)

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

Nếu API bị gián đoạn, chỉ dùng `--resume` khi code, prompt, model và configuration chưa thay đổi:

```powershell
# Thêm --resume vào đúng lệnh và giữ nguyên --name, --output-dir.
```

Không dùng đồng thời `--resume` và `--clear-checkpoint`.

Nếu cần estimated cost, chỉ thêm hai tham số sau khi đã xác minh giá model tại ngày chạy:

```text
--input-cost-per-million <GIÁ_INPUT> --output-cost-per-million <GIÁ_OUTPUT>
```

Nếu để mặc định bằng 0, không báo estimated cost trong thesis.

## 6. Kiểm tra kết quả final

Đọc sáu metric chính từ `summary`:

```powershell
$FinalFiles = @(
  "$OutputDir\northwind_95_audited_final_$RunTag.json"
)

# Chỉ thêm file dưới đây nếu đã chạy lại Chinook trong cùng RunTag.
$ChinookFile = "$OutputDir\chinook_vn_300_final_$RunTag.json"
if (Test-Path -LiteralPath $ChinookFile) { $FinalFiles += $ChinookFile }

foreach ($File in $FinalFiles) {
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

Acceptance checks:

- Northwind có đúng 95 result; nếu rerun Chinook thì Chinook có đúng 300 result.
- Metadata hoặc nhật ký run ghi đúng SHA256 của dataset Northwind đã kiểm toán.
- `cache_hits = 0` ở mọi accuracy run.
- Không có câu bị resume từ checkpoint của run khác.
- JSON/CSV có cùng số record và cùng ID.
- Strict EX được lấy từ evaluator v2, không lấy `judge_match`.
- Không trộn Fast-mode latency với Deep-mode latency.
- Không có `system_error` hoặc exception `__end__`.
- Generated SQL chỉ có `LIMIT` khi câu hỏi/gold yêu cầu top-N, giới hạn cụ thể hoặc superlative tương đương `MIN/MAX`.
- Không có arbitrary fallback; generator thất bại phải được ghi rõ là `empty_sql`/generation failure.

Nếu kết quả bất thường, giữ nguyên artifact để audit nhưng không đưa ngay vào thesis. Phân tích trên dev subset, sửa code, tạo run tag mới và chạy lại từ đầu.

## 7. Ablation sáu profile

Lần này bắt buộc chạy lại ablation vì tập Northwind nguồn đã thay đổi. Ablation dùng Fast mode và cùng stratified subset 80 câu; lưu danh sách 80 ID vào artifact để sáu profile dùng đúng cùng mẫu.

Xem trước lệnh, không gọi API:

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

Chạy thật:

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

Profiles: `single_zero_shot`, `single_structured`, `full_no_cache`, `no_rag`, `no_planner`, `no_validator`.

So sánh profile bằng Strict EX, Exec_OK, mean/p95 latency, mean tokens và mean LLM calls. Không gọi 80 câu là power analysis và không tuyên bố một component có hiệu quả chỉ dựa vào chênh lệch nhỏ với CI chồng lấn.

## 8. Dynamic Bypass paired test

Lần này bắt buộc chạy lại hai nhánh vì paired subset được lấy từ Northwind đã kiểm toán.

```powershell
venv\Scripts\python.exe test\run_dynamic_bypass.py `
  --data data\northwind_massive_100.json `
  --db data\northwind\northwind.sqlite `
  --dataset-type northwind `
  --questions 30 `
  --seed 42 `
  --output-dir $OutputDir
```

Chỉ kết luận trên đúng cùng 30 ID. Báo riêng:

- Strict EX và paired wins/losses.
- Mean LLM calls.
- Mean total tokens.
- Mean và p95 latency.

Không gọi ngưỡng 30 bảng là universal optimum. Nếu auto-bypass giảm latency/calls nhưng tăng token, phải trình bày đây là trade-off.

## 9. Consistency 30 × 3

Lần này bắt buộc chạy lại consistency vì 30 câu được lấy từ Northwind. Consistency dùng Fast mode để đo core Text-to-SQL và tránh chi phí Result Formatter:

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

Metric chính là `strict_outcome_consistency`. SQL exact consistency và AST consistency là diagnostics. Một câu sai cả ba lần vẫn outcome-consistent, vì vậy phải báo thêm số nhóm `all correct`, `all wrong` và `mixed` khi diễn giải.

Không gọi hệ thống deterministic nếu SQL exact consistency chưa đạt 100%.

## 10. Semantic cache

Không chạy cache benchmark cũ trước khi sửa năm collision trong `test/cache_cases.json`:

- C02 hard-negative trùng C01 base.
- C16 hard-negative trùng C17 base.
- C24 hard-negative trùng C15 base.
- C25 và C26 dùng hard-negative trùng base của nhau.

Sau khi sửa, xác nhận không hard-negative nào trùng bất kỳ base nào:

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

Chạy threshold sweep và cold/warm E2E trong một lệnh:

```powershell
venv\Scripts\python.exe test\evaluate_cache.py `
  --cases test\cache_cases.json `
  --e2e `
  --db data\northwind\northwind.sqlite `
  --dataset-type northwind `
  --e2e-limit 10 `
  --output-dir $OutputDir
```

Quy tắc chọn threshold:

1. Loại mọi cấu hình có false hit trên test set sạch.
2. Trong nhóm còn lại, ưu tiên precision.
3. Sau đó mới tối ưu semantic recall và lookup latency.
4. Nếu không có cấu hình an toàn, tắt semantic hit và chỉ giữ exact cache.

Không mặc định Jaccard `0.92`. `0.92` hiện là cosine threshold trong config; Jaccard phải được chọn từ benchmark sạch.

## 11. File dùng cho thesis

Trong mỗi `$OutputDir`:

- Final JSON/CSV: bảng Main Results, result by intent, error analysis và node timings.
- `ablation_summary.csv`: bảng sáu profile.
- `dynamic_bypass_*_summary.csv`: paired bypass comparison.
- `consistency_*.json/.csv`: reliability/consistency.
- `semantic_cache_thresholds.csv` và `semantic_cache_evaluation.json`: cache section.

Không trộn số liệu giữa các run tag. Không lấy accuracy từ các file legacy `results_*.txt/json` làm Strict EX hiện tại. Nếu cần nhắc kết quả cũ, ghi rõ đó là `legacy judge-assisted match` và đặt ngoài bảng Main Results.

Ánh xạ số liệu phải thay sau lần chạy Northwind audited:

- **R1**: run tag, model, concurrency, runtime/hardware và ngày chạy.
- **R2a**: Strict EX/CI 95%, label match, Relaxed EX, Exec_OK, structure match và retry rate.
- **R2b**: mean/p50/p95 latency, mean/p95 tokens, LLM calls và token totals.
- **R3**: toàn bộ kết quả Northwind theo năm intent.
- **R4**: sáu profile ablation từ cùng fixed subset 80 ID.
- **R5**: hai nhánh Dynamic Bypass và paired wins/losses.
- **R7**: consistency 30 x 3 và reliability diagnostics từ final run.
- **R8**: error-category counts và tỷ lệ tính từ CSV/JSON mới.
- **R6a/R6b**: không bị vô hiệu chỉ bởi thay dataset Northwind; chỉ thay nếu cache cases, cache code hoặc E2E input thay đổi.

Không tính chênh lệch với Northwind cũ như mức cải thiện của mô hình, vì benchmark đã thay đổi. Có thể ghi chênh lệch trong audit log nhưng phải gắn nhãn `không so sánh trực tiếp`.

## 12. Checklist trước khi chốt thesis

- [ ] Code/prompt/model đã freeze trước final run.
- [ ] Dataset Northwind đúng SHA256 đã ghi ở mục 0.
- [ ] Tests pass.
- [ ] Smoke test pass, không arbitrary fallback.
- [ ] Final Northwind đủ 95 câu và Chinook đủ 300 câu.
- [ ] Cache hit bằng 0 trong accuracy runs.
- [ ] Main Results chỉ dùng sáu metric lõi.
- [ ] Fast và Deep mode được báo riêng.
- [ ] Ablation dùng cùng 80 ID và seed 42.
- [ ] Dynamic Bypass dùng đúng cùng 30 ID ở hai nhánh.
- [ ] Consistency dùng cùng 30 câu × 3 lần.
- [ ] Cache cases không còn collision; E2E đã chạy.
- [ ] Pricing bằng 0 thì không báo estimated cost.
- [ ] Mọi bảng ghi run tag, model/version, profile, mode và ngày chạy.
- [ ] Không còn số liệu Northwind pre-audit trong R2-R5, R7 và R8.
