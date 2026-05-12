"""
Evaluation Script for Chinook_VN dataset
Combines exact execution match evaluation and beautiful text reporting.
"""

import sys
import io
import os
import time
import sqlite3
import argparse
import json
from pathlib import Path
from typing import Optional

# Ép kiểu UTF-8 trên Windows
if os.name == "nt":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).parent))

from src.graph import run_query
from src.memory import get_semantic_cache

DEFAULT_DATA_PATH = "data/northwind_massive_100.json"
DEFAULT_DB_PATH = "data/northwind/northwind.sqlite"

# ── Checkpoint Manager ───────────────────────────────────────────────────────
class CheckpointManager:
    def __init__(self, checkpoint_dir: str = "test/eval_checkpoints"):
        self.checkpoint_dir = Path(checkpoint_dir)
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)
        self._path: Optional[Path] = None

    def resolve_path(self, dataset_name: str, limit: Optional[int]) -> Path:
        fingerprint = f"{dataset_name}_limit_{limit}" if limit else dataset_name
        self._path = self.checkpoint_dir / f"eval_{fingerprint}.json"
        return self._path

    def load(self, path: Path) -> dict:
        if not path.exists():
            return {}
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {k: v for k, v in data.items()}

    def save(self, qid: str, result_dict: dict, checkpoint: dict):
        checkpoint[qid] = result_dict
        if self._path:
            tmp = self._path.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(checkpoint, f, ensure_ascii=False, indent=2)
            tmp.replace(self._path)

    def clear(self, path: Path):
        if path.exists():
            path.unlink()

# ── Utils ──────────────────────────────────────────────────────────────────
def execution_match(gold_sql: str, gen_sql: str, db_path: str) -> bool:
    try:
        gold_norm = gold_sql.strip().rstrip(";").strip()
        gen_norm = gen_sql.strip().rstrip(";").strip()
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute(gold_norm)
        gold = sorted(tuple(r) for r in cur.fetchall())
        cur.execute(gen_norm)
        gen = sorted(tuple(r) for r in cur.fetchall())
        conn.close()
        return gold == gen
    except Exception:
        return False

def print_progress(current: int, total: int, status: str, latency_ms: float):
    bar_len = 30
    filled = int(bar_len * current / max(1, total))
    bar = "=" * filled + "-" * (bar_len - filled)
    pct = current / max(1, total) * 100
    print(f"\r[{bar}] {pct:.0f}% ({current}/{total}) {status} {latency_ms:.0f}ms", end="", flush=True)

# ── Main Evaluation ────────────────────────────────────────────────────────
def run_evaluation(limit: int = None, clear_checkpoint: bool = False, dataset_type: str = "chinook_vn"):
    
    print(f"[1/3] Loading dataset from {DEFAULT_DATA_PATH}...")
    
    try:
        with open(DEFAULT_DATA_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except FileNotFoundError:
        print(f"\nLỗi: Không tìm thấy file {DEFAULT_DATA_PATH}.")
        return

    questions = data.get("questions", [])
    if limit:
        questions = questions[:limit]
    total_q = len(questions)
    
    ckpt = CheckpointManager()
    ckpt_path = ckpt.resolve_path(dataset_type, limit)
    
    if clear_checkpoint:
        ckpt.clear(ckpt_path)
        print(f"  [checkpoint] Cleared: {ckpt_path.name}")
        
    checkpoint_data = ckpt.load(ckpt_path)
    skipped = len(checkpoint_data)
    if skipped:
        print(f"  [checkpoint] Resuming: {skipped}/{total_q} questions already done")

    print(f"\n[2/3] Bắt đầu đánh giá {total_q} câu hỏi trên DB {DEFAULT_DB_PATH}...")
    results = list(checkpoint_data.values())

    db_path_str = DEFAULT_DB_PATH

    for i, row in enumerate(questions):
        qid = str(row.get("id", i))
        question = row["question"]
        gold_sql = row.get("gold_sql", "")
        intent = row.get("intent", "unknown")

        if qid in checkpoint_data:
            eval_res = checkpoint_data[qid]
            print_progress(i + 1, total_q, "CACHED", eval_res.get("latency_ms", 0))
            continue

        start = time.time()
        try:
            agent_result = run_query(
                question,
                db_path=db_path_str,
                dataset_type=dataset_type
            )
            elapsed_ms = (time.time() - start) * 1000

            gen_sql = agent_result.generated_sql or ""
            has_exec = agent_result.execution_error is None and agent_result.query_result is not None
            row_count = agent_result.query_result["row_count"] if has_exec else 0

            match = False
            if gold_sql and gen_sql and has_exec:
                match = execution_match(gold_sql, gen_sql, db_path_str)

            eval_res = {
                "id": qid,
                "intent": intent,
                "question": question,
                "gold_sql": gold_sql,
                "generated_sql": gen_sql,
                "execution_match": match,
                "execution_success": bool(has_exec),
                "row_count": row_count,
                "error": agent_result.execution_error or agent_result.error,
                "latency_ms": round(elapsed_ms, 0),
                "cache_hit": getattr(agent_result, 'cache_hit', False)
            }
            results.append(eval_res)
            ckpt.save(qid, eval_res, checkpoint_data)

            status = "PASS" if match else "FAIL"
            print_progress(i + 1, total_q, f"{status}", elapsed_ms)

        except Exception as e:
            elapsed_ms = (time.time() - start) * 1000
            eval_res = {
                "id": qid, "intent": intent, "question": question,
                "gold_sql": gold_sql, "generated_sql": "",
                "execution_match": False, "execution_success": False,
                "error": str(e),
                "latency_ms": round(elapsed_ms, 0)
            }
            results.append(eval_res)
            ckpt.save(qid, eval_res, checkpoint_data)
            print_progress(i + 1, total_q, "ERROR", elapsed_ms)

    # ── SUMMARY ──────────────────────────────────────────────────────────────
    report_lines = []
    def log_print(msg):
        print(msg)
        report_lines.append(msg)

    log_print("\n\n" + "=" * 70)
    log_print(f"  {dataset_type.upper()} EVALUATION REPORT")
    log_print("=" * 70)

    if not results:
        log_print("Không có kết quả nào được ghi nhận.")
        return

    total = len(results)
    exec_match = sum(1 for r in results if r["execution_match"])
    exec_ok = sum(1 for r in results if r["execution_success"])
    cache_hits = sum(1 for r in results if r.get("cache_hit"))
    total_latency = sum(r["latency_ms"] for r in results)

    accuracy = exec_match / max(1, total) * 100
    exec_rate = exec_ok / max(1, total) * 100
    avg_latency = total_latency / max(1, total)

    log_print(f"  Total Questions:       {total}")
    log_print(f"  PASS (Exec Match):     {exec_match} ({accuracy:.1f}%)")
    log_print(f"  SQL Execution OK:      {exec_ok} ({exec_rate:.1f}%)")
    log_print(f"  Avg Latency:           {avg_latency:.0f}ms\n")

    log_print("  ACCURACY BY INTENT")
    log_print("  " + "─" * 60)
    intent_stats = {}
    for r in results:
        intent = r.get("intent", "unknown")
        if intent not in intent_stats:
            intent_stats[intent] = {"total": 0, "correct": 0}
        intent_stats[intent]["total"] += 1
        if r.get("execution_match"):
            intent_stats[intent]["correct"] += 1

    sorted_intents = sorted(intent_stats.keys(), key=lambda x: intent_stats[x]["correct"]/max(1, intent_stats[x]["total"]), reverse=True)
    for intent in sorted_intents:
        data = intent_stats[intent]
        pct = data["correct"] / data["total"] * 100
        bar = "█" * int(pct / 5) + "░" * (20 - int(pct / 5))
        log_print(f"  {intent:<15} {bar} {pct:5.1f}%  ({data['correct']}/{data['total']})")

    errors = [r for r in results if not r.get("execution_match")]
    if errors:
        log_print("\n  ALL FAIL DETAILS (CHI TIẾT LỖI)")
        log_print("  " + "═" * 80)
        for r in errors:
            log_print(f"  [Q] Question : {r['question']}")
            
            if not r.get("execution_success"):
                log_print(f"  [!] EXEC ERROR: {r.get('error')}")
            else:
                log_print("  [-] LOGIC ERROR: Chạy thành công nhưng sai kết quả so với mẫu.")
            
            log_print(f"      Gold SQL : {r.get('gold_sql')}")
            log_print(f"      Gen SQL  : {r.get('generated_sql')}")
            log_print("  " + "─" * 80)

    # LƯU KẾT QUẢ VÀO FILE test/results_vn.txt
    output_dir = Path("test")
    output_dir.mkdir(exist_ok=True)
    report_path = output_dir / f"results_{dataset_type}.txt"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(report_lines))
    print(f"\n✅ Đã lưu toàn bộ báo cáo chi tiết vào file text: {report_path}")

    # LƯU JSON THEO CHUẨN results_vn.json
    import datetime
    formatted_results = []
    for r in results:
        formatted_results.append({
            "id": r.get("id"),
            "intent": r.get("intent"),
            "question": r.get("question"),
            "generated_sql": r.get("generated_sql"),
            "gold_sql": r.get("gold_sql"),
            "sql_correct": bool(r.get("execution_match", False)),
            "execution_success": bool(r.get("execution_success", False)),
            "row_count": r.get("row_count", 0),
            "error": r.get("error"),
            "latency_ms": r.get("latency_ms", 0),
            "cache_hit": bool(r.get("cache_hit", False)),
            "retry_count": 0
        })

    final_report = {
        "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "data_path": DEFAULT_DATA_PATH,
        "db_path": DEFAULT_DB_PATH,
        "total": total,
        "correct": exec_match,
        "accuracy": accuracy,
        "exec_ok": exec_ok,
        "exec_rate": exec_rate,
        "cache_hits": cache_hits,
        "avg_latency_ms": avg_latency,
        "results": formatted_results
    }

    report_json_path = output_dir / f"results_{dataset_type}.json"
    with open(report_json_path, "w", encoding="utf-8") as f:
        json.dump(final_report, f, ensure_ascii=False, indent=2)
    print(f"✅ Đã lưu data JSON theo chuẩn mới vào file: {report_json_path}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Evaluation for Chinook VN")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--clear-checkpoint", action="store_true")
    parser.add_argument("--data", type=str, default=DEFAULT_DATA_PATH, help="Path to JSON dataset")
    parser.add_argument("--db", type=str, default=DEFAULT_DB_PATH, help="Path to SQLite DB")
    parser.add_argument("--dataset-type", type=str, default="northwind", help="Type of dataset (used for checkpointing and RAG filtering)")
    args = parser.parse_args()

    # Cập nhật đường dẫn mặc định theo tham số truyền vào
    DEFAULT_DATA_PATH = args.data
    DEFAULT_DB_PATH = args.db

    run_evaluation(args.limit, args.clear_checkpoint, args.dataset_type)
