import json
import argparse
from src.rag.few_shot_retriever import FewShotRetriever

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", required=True, type=str, help="Path to JSON file with questions and gold_sql (e.g., data/data_vn.json)")
    parser.add_argument("--dataset-type", required=True, type=str, help="Dataset type tag for FAISS metadata (e.g., chinook_vn)")
    parser.add_argument("--split", type=str, default="fewshot", help="Split tag for FAISS metadata (default: fewshot)")
    parser.add_argument("--db-id", type=str, default="", help="DB ID for FAISS metadata (default: dataset-type)")
    parser.add_argument("--num", type=int, default=5000, help="Number of questions to take from the file")
    
    args = parser.parse_args()
    db_id_val = args.db_id if args.db_id else args.dataset_type

    retriever = FewShotRetriever()
    retriever._get_db()

    # Delete old data
    if retriever.vector_db is not None:
        docstore = retriever.vector_db.docstore._dict
        index_to_docstore_id = retriever.vector_db.index_to_docstore_id
        
        # We delete by dataset AND split so we don't accidentally wipe train or test
        ids_to_delete = []
        for idx, doc_id in index_to_docstore_id.items():
            doc = docstore.get(doc_id)
            if doc and doc.metadata.get('dataset') == args.dataset_type and doc.metadata.get('split') == args.split:
                ids_to_delete.append(doc_id)
        
        if ids_to_delete:
            print(f"Đang xóa {len(ids_to_delete)} câu rác cũ của dataset '{args.dataset_type}' (split='{args.split}')...")
            retriever.vector_db.delete(ids_to_delete)

    print(f"Bắt đầu nhúng dữ liệu mới từ {args.data}...")
    
    # We call the main index_dataset function
    # Wait, the retriever's index_dataset function might not let us pass db_id override easily 
    # if it reads from JSON. But we updated index_dataset to use item.get("db_id", "unknown").
    # We will just pass split and dataset_type.
    retriever.index_dataset(args.data, dataset_type=args.dataset_type, split=args.split)
    
if __name__ == "__main__":
    main()
