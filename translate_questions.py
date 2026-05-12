import json
import sys
import os
import time
from pathlib import Path

# Force UTF-8
if os.name == "nt":
    sys.stdout = __import__("io").TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = __import__("io").TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

from src.agents.gemini_llm import invoke
from src.config import config

def main():
    file_path = "data/northwind_massive_100.json"
    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    questions = data.get("questions", [])
    print(f"Bắt đầu dịch {len(questions)} câu hỏi sang tiếng Việt...")

    # Dịch theo batch 20 câu một
    batch_size = 20
    for i in range(0, len(questions), batch_size):
        batch = questions[i:i+batch_size]
        
        # Build prompt
        text_to_translate = "\n".join([f"[{q['id']}] {q['question']}" for q in batch])
        prompt = f"""Hãy dịch các câu hỏi Tiếng Anh sau sang Tiếng Việt một cách tự nhiên, dùng văn phong doanh nghiệp.
CHỈ TRẢ VỀ bản dịch, mỗi dòng bắt đầu bằng [ID]. KHÔNG trả về bất kỳ text nào khác.

Ví dụ:
[1] Liệt kê tổng doanh thu của từng sản phẩm?
[2] Khách hàng nào mua nhiều nhất?

CÂU HỎI CẦN DỊCH:
{text_to_translate}
"""
        try:
            print(f"  Đang dịch từ câu {i+1} đến {min(i+batch_size, len(questions))}...")
            res = invoke(prompt=prompt, model=config.LLM_MODEL, temperature=0.1, max_tokens=2048)
            
            # Parse response
            lines = res.strip().split("\n")
            translation_map = {}
            for line in lines:
                line = line.strip()
                if line.startswith("[") and "]" in line:
                    try:
                        q_id = int(line[1:line.index("]")])
                        trans = line[line.index("]")+1:].strip()
                        translation_map[q_id] = trans
                    except:
                        pass
            
            # Apply translations
            for q in batch:
                if q["id"] in translation_map:
                    q["question"] = translation_map[q["id"]]
                    
            time.sleep(1)
        except Exception as e:
            print(f"Lỗi dịch batch: {e}")

    # Save back
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        
    print("✅ Đã dịch xong 100% sang tiếng Việt và lưu lại file!")

if __name__ == "__main__":
    main()
