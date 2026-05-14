"""
Spider Evaluation (Refactored) — Đánh giá hệ thống Text-to-SQL trên Spider dataset.
Sử dụng dữ liệu Local (không cần Hugging Face).
Trích xuất trực tiếp Schema từ file SQLite.
"""
import sys
import io
import os
import time
import sqlite3
import argparse
import json
import hashlib
from pathlib import Path
from typing import Optional
import pandas as pd

# Ép kiểu UTF-8 trên Windows để tránh lỗi in text
if os.name == "nt":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).parent))

# Import logic Agent của bạn
from src.graph import run_query
from src.memory import get_semantic_cache

# ── Checkpoint Manager ──────────────────────────────────────────────────────
class CheckpointManager:
    def __init__(self, checkpoint_dir: str = "test/spider_checkpoints"):
        self.checkpoint_dir = Path(checkpoint_dir)
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)
        self._path: Optional[Path] = None

    def resolve_path(self, dataset_name: str, limit: Optional[int]) -> Path:
        fingerprint = f"{dataset_name}_limit_{limit}" if limit else dataset_name
        self._path = self.checkpoint_dir / f"spider_eval_{fingerprint}.json"
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

# ── Utils & Schema Extraction ──────────────────────────────────────────────
def extract_schema_from_sqlite(db_path: str) -> str:
    """Đọc trực tiếp cấu trúc DB từ file SQLite để cung cấp ngữ cảnh cho Agent."""
    if not os.path.exists(db_path):
        return ""
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = cursor.fetchall()
        
        schema_text = []
        for table in tables:
            table_name = table[0]
            cursor.execute(f"PRAGMA table_info('{table_name}');")
            columns = [f"{col[1]} ({col[2]})" for col in cursor.fetchall()]
            schema_text.append(f"Table: {table_name}\nColumns: {', '.join(columns)}")
            
            cursor.execute(f"PRAGMA foreign_key_list('{table_name}');")
            fks = cursor.fetchall()
            for fk in fks:
                schema_text.append(f"Foreign Key: {table_name}.{fk[3]} -> {fk[2]}.{fk[4]}")
        
        conn.close()
        return "\n".join(schema_text)
    except Exception as e:
        print(f"Error extracting schema from {db_path}: {e}")
        return ""

def execution_match(gold_sql: str, gen_sql: str, db_path: str) -> bool:
    try:
        gold_norm = gold_sql.strip().rstrip(";").strip()
        gen_norm = gen_sql.strip().rstrip(";").strip()
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        
        def sort_key(row):
            return tuple(str(v) if v is not None else "" for v in row)
            
        cur.execute(gold_norm)
        gold = sorted((tuple(r) for r in cur.fetchall()), key=sort_key)
        cur.execute(gen_norm)
        gen = sorted((tuple(r) for r in cur.fetchall()), key=sort_key)
        conn.close()
        return gold == gen
    except Exception as e:
        print(f"Exec Match Error: {e}")
        return False

def print_progress(current: int, total: int, status: str, latency_ms: float):
    bar_len = 30
    filled = int(bar_len * current / max(1, total))
    bar = "=" * filled + "-" * (bar_len - filled)
    pct = current / max(1, total) * 100
    print(f"\r[{bar}] {pct:.0f}% ({current}/{total}) {status} {latency_ms:.0f}ms", end="", flush=True)

# ── Main Evaluation Logic ──────────────────────────────────────────────────
def run_spider_evaluation(data_file: str, limit: int = None, db_dir: str = "data/spider/spider_data/database", output_path: str = "test/spider_results.json", clear_checkpoint: bool = False):
    
    print(f"[1/3] Loading Spider dataset from {data_file}...")
    
    try:
        with open(data_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
            
        # Nắm bắt dạng cấu trúc JSON (Spider gốc là danh sách thẳng, custom có "questions")
        if isinstance(data, dict) and "questions" in data:
            data = data["questions"]
            
    except FileNotFoundError:
        print(f"\nLỗi: Không tìm thấy file {data_file}.")
        print("Hãy chắc chắn bạn đang chạy lệnh từ thư mục gốc của project.")
        return

    # Chuyển đổi JSON thành DataFrame để dễ thao tác
    df = pd.DataFrame(data)
    
    if limit:
        df = df.head(limit)
    total_q = len(df)
    
    ckpt = CheckpointManager()
    ckpt_path = ckpt.resolve_path("spider_local_dev", limit)
    
    if clear_checkpoint:
        ckpt.clear(ckpt_path)
        print(f"  [checkpoint] Cleared: {ckpt_path.name}")
        
    checkpoint_data = ckpt.load(ckpt_path)
    skipped = len(checkpoint_data)
    if skipped:
        print(f"  [checkpoint] Resuming: {skipped}/{total_q} questions already done")

    print(f"\n[2/3] Bắt đầu đánh giá {total_q} câu hỏi...")
    results = list(checkpoint_data.values())

    for i, row in df.iterrows():
        qid = f"{row['db_id']}_{i}"
        question = row.get("question", "")
        gold_sql = row.get("gold_sql") or row.get("query", "")
        db_id = row.get("db_id", "")

        if qid in checkpoint_data:
            eval_res = checkpoint_data[qid]
            print_progress(i + 1, total_q, "CACHED", eval_res.get("latency_ms", 0))
            continue

        sqlite_path = Path(db_dir) / db_id / f"{db_id}.sqlite"
        db_path_str = str(sqlite_path) if sqlite_path.exists() else ""
        
        schema_ctx = extract_schema_from_sqlite(db_path_str) if db_path_str else None

        start = time.time()
        try:
            agent_result = run_query(
                question,
                db_path=db_path_str,
                override_schema_context=schema_ctx,
            )
            elapsed_ms = (time.time() - start) * 1000

            gen_sql = agent_result.generated_sql or ""
            has_exec = agent_result.execution_error is None and agent_result.query_result is not None and db_path_str
            row_count = agent_result.query_result["row_count"] if has_exec else 0

            match = False
            if gold_sql and gen_sql:
                if db_path_str and has_exec:
                    match = execution_match(gold_sql, gen_sql, db_path_str)
                elif not db_path_str:
                    match = gold_sql.strip().lower().rstrip(';') == gen_sql.strip().lower().rstrip(';')

            eval_res = {
                "id": qid,
                "db_id": db_id,
                "question": question,
                "gold_sql": gold_sql,
                "generated_sql": gen_sql,
                "execution_match": match,
                "execution_success": bool(has_exec),
                "has_schema": bool(schema_ctx),
                "row_count": row_count,
                "error": agent_result.execution_error or agent_result.error,
                "latency_ms": round(elapsed_ms, 0),
                "cache_hit": getattr(agent_result, 'cache_hit', False)
            }
            results.append(eval_res)
            ckpt.save(qid, eval_res, checkpoint_data)

            status = "PASS" if (match and has_exec) else "FAIL"
            print_progress(i + 1, total_q, f"{status} (SCH: {'Y' if schema_ctx else 'N'})", elapsed_ms)

        except Exception as e:
            elapsed_ms = (time.time() - start) * 1000
            eval_res = {
                "id": qid, "db_id": db_id, "question": question,
                "gold_sql": gold_sql, "generated_sql": "",
                "execution_match": False, "execution_success": False,
                "has_schema": bool(schema_ctx), "error": str(e),
                "latency_ms": round(elapsed_ms, 0)
            }
            results.append(eval_res)
            ckpt.save(qid, eval_res, checkpoint_data)
            print_progress(i + 1, total_q, "ERROR", elapsed_ms)

    # ── SUMMARY ─────────────────────────────────────────────────────────────
    print("\n\n" + "=" * 70)
    print("  SPIDER EVALUATION REPORT")
    print("=" * 70)

    if not results:
        print("Không có kết quả nào được ghi nhận.")
        return

    total = len(results)
    exec_match = sum(1 for r in results if r["execution_match"] and r["execution_success"])
    exec_ok = sum(1 for r in results if r["execution_success"])
    cache_hits = sum(1 for r in results if r.get("cache_hit"))
    total_latency = sum(r["latency_ms"] for r in results)
    no_db = sum(1 for r in results if not r["has_schema"])

    accuracy = exec_match / max(1, total) * 100
    exec_rate = exec_ok / max(1, total) * 100
    avg_latency = total_latency / max(1, total)

    print(f"  Total Questions:       {total}")
    print(f"  PASS (Exec Match):     {exec_match} ({accuracy:.1f}%)")
    print(f"  SQL Execution OK:      {exec_ok} ({exec_rate:.1f}%)")
    print(f"  Missing DB/Schema:     {no_db}")
    print(f"  Cache Hits:            {cache_hits}")
    print(f"  Avg Latency:           {avg_latency:.0f}ms\n")

    print("  ACCURACY BY DATABASE (Top 10)")
    print("  " + "─" * 60)
    db_stats = {}
    for r in results:
        db_id = r["db_id"]
        if db_id not in db_stats:
            db_stats[db_id] = {"total": 0, "correct": 0}
        db_stats[db_id]["total"] += 1
        if r["execution_match"] and r["execution_success"]:
            db_stats[db_id]["correct"] += 1

    sorted_dbs = sorted(db_stats.keys(), key=lambda x: db_stats[x]["correct"]/max(1, db_stats[x]["total"]), reverse=True)[:10]
    for db_id in sorted_dbs:
        data = db_stats[db_id]
        pct = data["correct"] / data["total"] * 100
        bar = "█" * int(pct / 5) + "░" * (20 - int(pct / 5))
        print(f"  {db_id:<20} {bar} {pct:5.1f}%  ({data['correct']}/{data['total']})")

    errors = [r for r in results if not r["execution_success"] or not r["execution_match"]]
    if errors:
        print("\n  LATEST FAIL DETAILS (CHI TIẾT LỖI)")
        print("  " + "═" * 80)
        for r in errors:
            print(f"  [Q] Database : {r['db_id']}")
            print(f"      Question : {r['question']}")
            
            # Nếu có lỗi thực thi (crash)
            if not r["execution_success"]:
                print(f"  [!] EXEC ERROR: {r.get('error')}")
            # Nếu chạy được nhưng sai logic/sai kết quả
            else:
                print("  [-] LOGIC ERROR: Chạy thành công nhưng sai kết quả so với mẫu.")
            
            print(f"      Gold SQL : {r.get('gold_sql')}")
            print(f"      Gen SQL  : {r.get('generated_sql')}")
            print("  " + "─" * 80)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Spider Evaluation (Local JSON)")
    parser.add_argument("--data", type=str, default="data/spider_custom_data.json", help="Path to Spider JSON file")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--db-dir", type=str, default="data/spider/spider_data/database", help="Path to Spider database directory")
    parser.add_argument("--output", type=str, default="test/spider_results.json")
    parser.add_argument("--clear-checkpoint", action="store_true")
    args = parser.parse_args()

    run_spider_evaluation(args.data, args.limit, args.db_dir, args.output, args.clear_checkpoint)