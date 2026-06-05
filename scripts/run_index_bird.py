import sys
import os
import io

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from src.rag.few_shot_retriever import FewShotRetriever

print("Starting to index BIRD dataset into FAISS...")

# Path to BIRD train.json
bird_train_path = "data/bird-sql/mini_dev/train/train/train.json"

if not os.path.exists(bird_train_path):
    print(f"Error: Could not find {bird_train_path}")
    sys.exit(1)

retriever = FewShotRetriever()
retriever.index_dataset(bird_train_path, dataset_type="bird", start_offset=2000)

print("\nFinished indexing BIRD!")
