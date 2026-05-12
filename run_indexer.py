import json
import sys
import io
import os
from src.rag.few_shot_retriever import FewShotRetriever

if os.name == "nt":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

if __name__ == "__main__":
    retriever = FewShotRetriever()
    
    # Đường dẫn chính xác
    train_path = "data/northwind_data.json"
    
    print(f"🚀 Bắt đầu tạo Vector DB từ: {train_path}")
    retriever.index_dataset(train_path, dataset_type="northwind")
    print("🎉 Hoàn tất!")
