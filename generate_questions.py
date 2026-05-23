"""
Script sinh hàng loạt câu hỏi cho Northwind DB (hoặc DB bất kỳ) bằng Gemini.
Chạy: python generate_questions.py --db data/northwind/northwind.sqlite --output data/northwind_massive_100.json --count 100
"""
import json
import sys
import os
import time
import re
import argparse
from pathlib import Path

# Force UTF-8
if os.name == "nt":
    sys.stdout = __import__("io").TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace"
    )
    sys.stderr = __import__("io").TextIOWrapper(
        sys.stderr.buffer, encoding="utf-8", errors="replace"
    )

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

from dotenv import load_dotenv
load_dotenv(BASE_DIR / ".env")

from src.agents.gemini_llm import invoke
from src.config import config
from src.db import DatabaseManager

def generate_batch(db_path: str, existing_questions: list[str], batch_num: int, batch_target: int = 25) -> list[dict]:
    """Gọi Gemini sinh 1 batch câu hỏi."""
    print(f"\n  Batch {batch_num}: gọi Gemini...")

    db = DatabaseManager(db_path=db_path)
    schema = db.get_schema()
    schema_text = "\n".join([f"Table {t.table_name}: " + ", ".join([c['name'] for c in t.columns]) for t in schema.tables])
    fk_text = "\n".join([f"{fk['from_table']}.{fk['from_column']} -> {fk['to_table']}.{fk['to_column']}" for fk in schema.relationships])

    # Đưa existing vào prompt để Gemini tránh trùng
    existing_str = "\n".join(f"- {q}" for q in existing_questions[-100:])

    system_prompt = f"""You are a SQL expert. Generate diverse, realistic business questions for an ERP database.
DATABASE SCHEMA:
{schema_text}
RELATIONSHIPS:
{fk_text}

Output: strict JSON only, no markdown.
{{"questions": [
  {{"question": "...", "intent": "simple|aggregate|join|complex", "sql_keywords": ["Orders", "Customers"], "gold_sql": "SELECT ... FROM ...;"}}
]}}

RULES:
1. Generate UNIQUE questions — completely different from any previously generated ones.
2. Cover under-used areas: self-join, subqueries, CTEs, complex math (revenue calculation).
3. Mix intents: simple, aggregate, join, complex.
4. Each must have correct gold_sql in valid SQLite syntax.
5. Questions: natural Vietnamese, varied phrasing, NO duplicates.
6. gold_sql must use table aliases and end with semicolon.
7. CRITICAL: Wrap table names with spaces in double quotes: "Order Details"
"""

    user_prompt = f"""Generate {batch_target} diverse business questions based on the schema provided.
IMPORTANT: Do NOT duplicate any of these existing questions:
{existing_str}

Return only JSON: {{"questions": [ {{"question": "...", "intent": "...", "sql_keywords": [...], "gold_sql": "..."}} ] }}

If the database is Northwind, you MUST cover:
- Employee self-join (ReportsTo hierarchy)
- Sales performance: Revenue = SUM(UnitPrice * Quantity * (1 - Discount)) from "Order Details"
- Top N analysis (products, customers, employees)
- Cross-table JOINs (Orders + Order Details + Products + Categories)
- Date functions: sales by year/month (OrderDate)
"""

    for attempt in range(3):
        try:
            raw = invoke(
                prompt=user_prompt,
                model=config.LLM_MODEL,
                temperature=0.8,
                max_tokens=8192,
                system_prompt=system_prompt,
            )

            raw = raw.strip()
            raw = re.sub(r'^```(?:json)?\s*', '', raw, flags=re.MULTILINE)
            raw = re.sub(r'\s*```$', '', raw, flags=re.MULTILINE)

            data = json.loads(raw)
            questions = data.get("questions", [])
            print(f"  Batch {batch_num}: ✓ nhận {len(questions)} câu")
            return questions

        except (json.JSONDecodeError, KeyError, TypeError) as e:
            print(f"  ⚠️  Batch {batch_num}: parse fail lần {attempt+1}: {e}")
            time.sleep(2)
        except Exception as e:
            print(f"  ⚠️  Batch {batch_num}: error lần {attempt+1}: {e}")
            time.sleep(3)

    return []


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=str, default="data/northwind/northwind.sqlite")
    parser.add_argument("--output", type=str, default="data/northwind_massive_100.json")
    parser.add_argument("--count", type=int, default=100)
    args = parser.parse_args()

    print("=" * 70)
    print(f"  GENERATE {args.count} NEW QUESTIONS")
    print(f"  DB: {args.db}")
    print("=" * 70)

    # Load existing if any
    existing_qs = []
    if Path(args.output).exists():
        try:
            with open(args.output, encoding="utf-8") as f:
                data = json.load(f)
                existing_qs = [q["question"].lower() for q in data.get("questions", [])]
        except Exception:
            pass

    print(f"\n  Đã load {len(existing_qs)} câu hỏi hiện có từ {args.output}")

    all_new: list[dict] = []
    seen_texts = set(existing_qs)
    
    batches = (args.count + 24) // 25  # Calculate number of batches of 25

    for batch_num in range(1, batches + 1):
        target = 25 if len(all_new) + 25 <= args.count else (args.count - len(all_new))
        # Add buffer since some might be duplicated
        buffer_target = min(target + 5, 30) 
        
        batch = generate_batch(args.db, list(seen_texts), batch_num, buffer_target)

        for q in batch:
            q_text = q.get("question", "").lower().strip()
            if (q_text
                    and q_text not in seen_texts
                    and not any(q_text[:40] in s or s[:40] in q_text for s in seen_texts)
                    and len(all_new) < args.count):
                all_new.append(q)
                seen_texts.add(q_text)

        print(f"  Tổng sau batch {batch_num}: {len(all_new)} câu")
        if len(all_new) >= args.count:
            break
        time.sleep(2)

    print(f"\n  Tổng: {len(all_new)} câu mới")

    # Combine with existing ones if you want to append, or just save new.
    # Here we overwrite with the 100 new ones, but you could merge.
    
    for i, q in enumerate(all_new):
        q["id"] = i + 1

    output = {
        "metadata": {
            "dataset": "Dynamic LLM Generated",
            "total_questions": len(all_new),
            "generated_by": "Gemini",
            "source": "generate_questions.py",
            "db_target": args.db,
            "categories": {
                "simple": sum(1 for q in all_new if q.get("intent") == "simple"),
                "aggregate": sum(1 for q in all_new if q.get("intent") == "aggregate"),
                "join": sum(1 for q in all_new if q.get("intent") == "join"),
                "complex": sum(1 for q in all_new if q.get("intent") == "complex"),
            }
        },
        "questions": all_new,
    }

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n  ✅ Đã lưu thành công: {args.output}")
    print(f"\n  Preview 5 câu đầu:")
    for q in all_new[:5]:
        print(f"    [{q['id']}] {q['question'][:65]}")

if __name__ == "__main__":
    main()
