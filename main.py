"""
Main CLI — Giao diện dòng lệnh cho Multi-Agent Text-to-SQL.
Hỗ trợ multi-database: --db-path để chỉ định SQLite file.
"""
import sys
import os
import time
import argparse
from pathlib import Path

# Force UTF-8 on Windows
if os.name == "nt":
    sys.stdout = __import__("io").TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace"
    )
    sys.stderr = __import__("io").TextIOWrapper(
        sys.stderr.buffer, encoding="utf-8", errors="replace"
    )

import structlog
from src.config import config
from src.db import DatabaseManager, get_db_manager
from src.rag import rebuild_schema_index
from src.graph import run_query
from src.memory import get_semantic_cache
from tools.visualizer import plot_chart, render_table_ascii
from src.utils.logger import setup_logger

log = setup_logger("main")


def init_system(db_path: str = "", force_rebuild: bool = False):
    """
    Khởi tạo toàn bộ hệ thống.
    Args:
        db_path: SQLite file path. Dùng config.DB_PATH nếu empty.
        force_rebuild: Force rebuild schema index.
    """
    resolved = db_path or config.DB_PATH
    db_name = Path(resolved).stem

    print("=" * 60)
    print("  MULTI-AGENT TEXT-TO-SQL SYSTEM")
    print(f"  Database: {db_name}")
    print("=" * 60)
    print()

    # 1. Init database
    print("[1/5] Connecting to database...")
    db = get_db_manager(resolved)
    print(f"     Database: {resolved}")

    # 2. Show schema
    schema = db.get_schema()
    print(f"[2/5] Schema loaded: {len(schema.tables)} tables")
    for t in schema.tables:
        print(f"     - {t.table_name}: {t.row_count} rows, {len(t.columns)} cols")

    # 3. Rebuild schema index (RAG)
    print("[3/5] Rebuilding schema index (RAG)...")
    try:
        rebuild_schema_index(db)
        print("     Schema indexed in ChromaDB")
    except Exception as e:
        print(f"     Warning: ChromaDB indexing failed: {e}")
        print("     System will continue without RAG context")

    # 4. Cache stats
    cache = get_semantic_cache()
    print(f"[4/5] Semantic cache ready (max {cache.max_size} entries)")

    # 5. Check API keys
    if not config.GEMINI_API_KEY:
        print("[5/5] WARNING: No Gemini API key found!")
        print("     Please add GEMINI_API_KEY to your .env file")
    else:
        print(f"[5/5] Gemini API ready ({config.LLM_MODEL})")

    print()
    print(f"System ready with: {db_name}")
    print()
    return resolved


def interactive_mode(db_path: str = ""):
    """Chế độ tương tác — chat liên tục."""
    print("Type 'exit' to quit, 'clear' to clear cache, 'stats' for cache info.")
    print("-" * 60)

    session_id = f"session_{int(time.time())}"
    while True:
        try:
            question = input("\nYou: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n\nGoodbye!")
            break

        if not question:
            continue

        cmd = question.lower()
        if cmd in ("exit", "quit", "q"):
            print("Goodbye!")
            break
        if cmd == "clear":
            cache = get_semantic_cache()
            cache.invalidate()
            print("Cache cleared.")
            continue
        if cmd == "stats":
            cache = get_semantic_cache()
            s = cache.stats()
            print(f"Cache: {s['hits']} hits, {s['misses']} misses, rate: {s['hit_rate']:.1%}")
            continue

        start = time.time()
        result = run_query(question, session_id=session_id, db_path=db_path)
        elapsed = time.time() - start

        _print_result(result, elapsed)


def _print_result(result, elapsed: float):
    """In kết quả query đẹp."""
    print()
    print("-" * 60)

    if result.cache_hit:
        print("[Served from cache]")
    else:
        print(f"Executed in {elapsed:.1f}s — {result.execution_time_ms:.0f}ms SQL")

    if result.generated_sql:
        sql = result.generated_sql
        print(f"\nSQL:\n   {sql[:200]}")
        if len(sql) > 200:
            print(f"   ... ({len(sql) - 200} more chars)")

    if result.formatted_answer:
        fa = result.formatted_answer
        print(f"\nAnswer:\n   {fa.get('summary', 'No summary.')}")
        if fa.get("detailed_answer"):
            for line in fa["detailed_answer"].split("\n"):
                if line.strip():
                    print(f"   {line}")

        if fa.get("insights"):
            print(f"\nKey Insights:")
            for insight in fa["insights"]:
                print(f"   - {insight}")

        viz = fa.get("visualization", {})
        if viz.get("recommended") and result.query_result:
            chart_type = viz.get("chart_type", "bar")
            chart_b64 = plot_chart(
                columns=result.query_result.get("columns", []),
                rows=result.query_result.get("rows", []),
                chart_type=chart_type,
                title=f"Query Result: {result.user_question[:40]}",
            )
            if chart_b64:
                print(f"\nVisualization ({chart_type} chart)")
            else:
                table = render_table_ascii(
                    result.query_result.get("columns", []),
                    result.query_result.get("rows", []),
                )
                print(f"\nTable:")
                print(table)
        elif result.query_result and not viz.get("recommended"):
            table = render_table_ascii(
                result.query_result.get("columns", []),
                result.query_result.get("rows", []),
                max_rows=15,
            )
            print(f"\nResult ({result.query_result['row_count']} rows):")
            print(table)

    if result.error:
        print(f"\nERROR: {result.error}")

    print("-" * 60)


def batch_mode(questions: list[str], db_path: str = ""):
    """Chạy nhiều câu hỏi cùng lúc."""
    print(f"Running {len(questions)} queries in batch mode...")
    session_id = f"batch_{int(time.time())}"

    results = []
    for i, q in enumerate(questions, 1):
        print(f"\n[{i}/{len(questions)}] Q: {q[:80]}")
        start = time.time()
        result = run_query(q, session_id=session_id, db_path=db_path)
        elapsed = time.time() - start
        _print_result(result, elapsed)
        results.append((q, result, elapsed))
        time.sleep(1)

    print("\n" + "=" * 60)
    print("BATCH SUMMARY")
    print("=" * 60)
    cache = get_semantic_cache()
    s = cache.stats()
    total_time = sum(r[2] for r in results)
    cache_hits = sum(1 for r in results if r[1].cache_hit)
    errors = sum(1 for r in results if r[1].error)

    print(f"Total queries: {len(results)}")
    print(f"Successful: {len(results) - errors}")
    print(f"Errors: {errors}")
    print(f"Cache hits: {cache_hits}")
    print(f"Cache hit rate: {s['hit_rate']:.1%}")
    print(f"Total time: {total_time:.1f}s")
    print(f"Avg time per query: {total_time/len(results):.1f}s")


def onboard_cmd(path: str):
    """CLI command: onboard a new database."""
    from src.agents.onboard import onboard_db
    onboard_db(path)


def list_dbs_cmd():
    """CLI command: list all onboarded databases."""
    from src.agents.onboard import list_databases
    list_databases()


def main():
    parser = argparse.ArgumentParser(
        description="Multi-Agent Text-to-SQL System (Multi-DB)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--db-path", type=str, default="",
                        help="Path to SQLite database file")
    parser.add_argument("--init", action="store_true",
                        help="Initialize system")
    parser.add_argument("--query", "-q", type=str,
                        help="Single query to run")
    parser.add_argument("--batch", "-b", type=str,
                        help="File containing queries (one per line)")
    parser.add_argument("--force-rebuild", action="store_true",
                        help="Force rebuild schema index")
    parser.add_argument("--onboard", type=str, metavar="DB_PATH",
                        help="Onboard a new SQLite database")
    parser.add_argument("--list-dbs", action="store_true",
                        help="List all onboarded databases")
    args = parser.parse_args()

    db_path = args.db_path

    if args.list_dbs:
        list_dbs_cmd()
        return

    if args.onboard:
        onboard_cmd(args.onboard)
        return

    if args.batch:
        path = Path(args.batch)
        if not path.exists():
            print(f"Error: File not found: {path}")
            sys.exit(1)
        questions = [
            line.strip() for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        init_system(db_path=db_path, force_rebuild=args.force_rebuild)
        batch_mode(questions, db_path=db_path)
        return

    if args.query:
        init_system(db_path=db_path, force_rebuild=args.force_rebuild)
        result = run_query(args.query, db_path=db_path)
        _print_result(result, 0)
        return

    if args.init:
        init_system(db_path=db_path, force_rebuild=True)
        return

    # Default: interactive mode
    init_system(db_path=db_path, force_rebuild=True)
    interactive_mode(db_path=db_path)


if __name__ == "__main__":
    main()
