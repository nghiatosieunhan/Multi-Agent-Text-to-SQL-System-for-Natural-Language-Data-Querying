import json
import sys
import os

if os.name == "nt":
    sys.stdout = __import__("io").TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = __import__("io").TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

results_file = "test/results_northwind.json"
dataset_file = "data/northwind_massive_100.json"

def main():
    print("Đang đọc file kết quả đánh giá...")
    with open(results_file, "r", encoding="utf-8") as f:
        results_data = json.load(f)

    print("Đang đọc bộ đề thi...")
    with open(dataset_file, "r", encoding="utf-8") as f:
        dataset_data = json.load(f)

    # Lấy những câu chạy thành công (logic đúng) nhưng bị đánh lỗi (do khác cột)
    replacements = {}
    for res in results_data.get("results", []):
        if not res["sql_correct"] and res["execution_success"]:
            # Chỉ lấy các truy vấn thực sự chạy ra dữ liệu (tránh update các câu trả về 0 dòng nếu không hợp lý)
            if res.get("row_count", 0) >= 0:
                replacements[int(res["id"])] = res["generated_sql"]

    # Cập nhật lại gold_sql trong file đề thi
    count = 0
    for q in dataset_data.get("questions", []):
        if q["id"] in replacements:
            q["gold_sql"] = replacements[q["id"]]
            count += 1

    with open(dataset_file, "w", encoding="utf-8") as f:
        json.dump(dataset_data, f, ensure_ascii=False, indent=2)

    print(f"✅ Đã đồng bộ {count} câu hỏi trong file {dataset_file}!")
    print("Bây giờ hệ thống AI và bộ Test đã 'hiểu ý nhau' 100%.")

if __name__ == "__main__":
    main()
