import sys
import os
import argparse
from src.agents.auto_fewshot import auto_generate_and_index_fewshot

def main():
    parser = argparse.ArgumentParser(description="Auto Few-Shot Generation Tool")
    parser.add_argument("--db", required=True, type=str, help="Path to SQLite database (e.g., data/mydb.sqlite)")
    parser.add_argument("--dataset-type", required=True, type=str, help="Dataset type tag (e.g., mydb_type)")
    parser.add_argument("--num", type=int, default=50, help="Number of questions to generate (default: 50)")
    
    args = parser.parse_args()
    
    print(f"Starting Auto Few-Shot Generation for {args.dataset_type}...")
    print(f"Database: {args.db}")
    print(f"Generating {args.num} questions...")
    
    auto_generate_and_index_fewshot(args.db, args.dataset_type, args.num)
    
    print("\nFinished Auto Few-Shot indexing!")

if __name__ == "__main__":
    main()
