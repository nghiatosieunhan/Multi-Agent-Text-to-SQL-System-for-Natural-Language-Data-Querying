import json
import sys
import io
import os
from src.rag.few_shot_retriever import FewShotRetriever

if os.name == "nt":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

import argparse

DEFAULT_DATA_PATH = "data/"
DEFAULT_DATASET_TYPE = "chinook"

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=str, default=DEFAULT_DATA_PATH, help="Path to JSON dataset")
    parser.add_argument("--dataset-type", type=str, default=DEFAULT_DATASET_TYPE, help="Dataset type for filtering (e.g., spider, chinook)")
    args = parser.parse_args()

    retriever = FewShotRetriever()
    
    print(f"🚀 Bắt đầu tạo Vector DB từ: {args.data}")
    retriever.index_dataset(args.data, dataset_type=args.dataset_type)
    print("🎉 Hoàn tất!")
