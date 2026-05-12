"""
Script sinh 300 câu hỏi mới cho spider_sales_vn.sqlite bằng Gemini.
"""
import json
import sys
import os
import time
import re
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

OUTPUT_PATH = "data/business/data_vn.json"

SALES_SCHEMA = """
DATABASE: spider_sales_vn (Vietnamese Sales DB)
Tables & columns:
- NhanVien (MaNV PK, TenNV, ChucVu, MaNQL FK, NgayVaoLam, Luong)
- KhachHang (MaKH PK, TenKH, SoDienThoai, Email, LoaiThe, MaNV_PhuTrach FK)
- CuaHang (MaCH PK, TenCH, KhuVuc)
- DanhMuc (MaDM PK, TenDM)
- SanPham (MaSP PK, TenSP, MaDM FK, DonGia)
- DonHang (MaDH PK, MaKH FK, MaCH FK, MaNV FK, NgayMua, TrangThai)
- ChiTietDonHang (MaDH FK, MaSP FK, SoLuong, MucGiamGia)
"""

SYSTEM_PROMPT = f"""{SALES_SCHEMA}
You are a SQL expert. Generate diverse, realistic business intelligence questions about this Vietnamese sales database.
Output: strict JSON only, no markdown.

{{"questions": [
  {{"id": 1, "question": "...", "intent": "simple|aggregate|join|complex",
   "sql_keywords": ["SanPham", "DanhMuc"], "gold_sql": "SELECT ... FROM ...;", "hint": "..."}}
]}}

RULES:
1. Generate unique questions covering various SQL features.
2. Mix intents: simple, aggregate, join, complex.
3. Each must have correct gold_sql in valid SQLite syntax.
4. Questions: natural Vietnamese, varied phrasing, NO duplicates.
5. gold_sql must use table aliases if joining, and end with semicolon.
6. The query must match the logic perfectly.
"""

def generate_batch(existing_questions: list[str], batch_num: int, batch_target: int = 25) -> list[dict]:
    print(f"\n  Batch {batch_num}: gọi Gemini...")
    existing_str = "\n".join(f"- {q}" for q in existing_questions[:50])

    user_prompt = f"""Generate {batch_target} diverse business questions for the sales database.
IMPORTANT: Do NOT duplicate any of these existing questions:
{existing_str}

Return only JSON format: {{"questions": [...]}}

Must cover:
- Doanh thu (SoLuong * DonGia * (1 - MucGiamGia))
- Quản lý nhân viên (MaNQL)
- Khách hàng VIP, Khách hàng theo khu vực
- Số lượng đơn hàng, trạng thái đơn hàng (Đã giao, Đã hủy, Chờ xử lý)
- Top sản phẩm, Danh mục bán chạy nhất
- Tính lương, hiệu suất làm việc của nhân viên
- Thời gian (tháng, quý, năm)
"""

    for attempt in range(3):
        try:
            raw = invoke(
                prompt=user_prompt,
                model=config.LLM_MODEL,
                temperature=0.8,
                max_tokens=8192,
                system_prompt=SYSTEM_PROMPT,
            )

            raw = raw.strip()
            raw = re.sub(r'^```(?:json)?\s*', '', raw, flags=re.MULTILINE)
            raw = re.sub(r'\s*```$', '', raw, flags=re.MULTILINE)

            data = json.loads(raw)
            questions = data.get("questions", [])
            print(f"  Batch {batch_num}: ✓ nhận {len(questions)} câu")
            return questions

        except Exception as e:
            print(f"  ⚠️  Batch {batch_num}: error lần {attempt+1}: {e}")
            time.sleep(3)

    return []

def main():
    print("=" * 70)
    print("  GENERATE 300 QUESTIONS FOR SALES DB")
    print("=" * 70)

    try:
        with open(OUTPUT_PATH, "r", encoding="utf-8") as f:
            existing = json.load(f)["questions"]
    except:
        existing = []

    all_new = existing.copy()
    seen_texts = set(q["question"].lower().strip() for q in existing)

    target_total = 300
    batch_size = 25
    batches_needed = 10 # Force run 10 more batches to get to 300

    for batch_num in range(1, batches_needed + 1):
        if len(all_new) >= target_total:
            break

        batch = generate_batch(list(seen_texts), batch_num, batch_target=batch_size)

        for q in batch:
            q_text = q.get("question", "").lower().strip()
            if (q_text and q_text not in seen_texts
                    and not any(q_text[:40] in s or s[:40] in q_text for s in seen_texts)
                    and len(all_new) < target_total):
                all_new.append(q)
                seen_texts.add(q_text)

        print(f"  Tổng số câu hiện tại: {len(all_new)} / {target_total}")
        time.sleep(2)

    for i, q in enumerate(all_new):
        q["id"] = i + 1
        q.setdefault("tables", [])
        q.setdefault("note", "")
        q.setdefault("hint", "")

    output = {
        "metadata": {
            "dataset": "spider_sales_vn (Generated)",
            "total_questions": len(all_new),
            "generated_by": "Gemini",
        },
        "questions": all_new,
    }

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n  ✅ Đã lưu {len(all_new)} câu vào {OUTPUT_PATH}")

if __name__ == "__main__":
    main()
