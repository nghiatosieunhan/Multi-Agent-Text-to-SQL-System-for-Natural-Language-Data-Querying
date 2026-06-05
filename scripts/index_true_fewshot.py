import json
import argparse
from src.rag.few_shot_retriever import FewShotRetriever
from langchain_core.documents import Document

def main():
    parser = argparse.ArgumentParser(description="Index True Few-Shot from JSON to FAISS")
    parser.add_argument("--data", required=True, type=str, help="Path to JSON file with questions and gold_sql (e.g., data/data_vn.json)")
    parser.add_argument("--dataset-type", required=True, type=str, help="Dataset type tag for FAISS metadata (e.g., chinook_vn)")
    parser.add_argument("--num", type=int, default=50, help="Number of questions to take from the file (default: 50)")
    
    args = parser.parse_args()

    retriever = FewShotRetriever()
    retriever._get_db()

    # Xóa dữ liệu rác cũ (nếu có) của dataset này
    docstore = retriever.vector_db.docstore._dict
    index_to_docstore_id = retriever.vector_db.index_to_docstore_id
    ids_to_delete = [doc_id for idx, doc_id in index_to_docstore_id.items() if docstore.get(doc_id) and docstore.get(doc_id).metadata.get('dataset') == args.dataset_type]
    
    if ids_to_delete:
        print(f"Đang xóa {len(ids_to_delete)} câu rác cũ của dataset '{args.dataset_type}'...")
        retriever.vector_db.delete(ids_to_delete)

    # Đọc dữ liệu từ file
    with open(args.data, 'r', encoding='utf-8') as f:
        data = json.load(f)

    docs = []
    # Lấy N câu
    for q in data.get('questions', [])[:args.num]:
        doc = Document(
            page_content=q['question'],
            metadata={
                "sql": q.get('gold_sql', ''),
                "db_id": args.dataset_type,
                "hint": q.get('note', ''),
                "dataset": args.dataset_type
            }
        )
        docs.append(doc)

    if docs:
        retriever.vector_db.add_documents(docs)
        retriever.vector_db.save_local(retriever.persist_directory)
        print(f"Đã nạp thành công {len(docs)} câu chuẩn xác từ {args.data} vào FAISS dưới nhãn '{args.dataset_type}'!")
    else:
        print("Không tìm thấy câu hỏi nào trong file JSON.")

if __name__ == "__main__":
    main()
