from src.rag.few_shot_retriever import FewShotRetriever
from collections import Counter

try:
    retriever = FewShotRetriever()
    retriever._get_db() # Ensure DB is loaded
    
    if not retriever.vector_db:
        print("FAISS database is empty or not initialized.")
        exit(0)

    # Access the underlying docstore (Langchain FAISS implementation)
    docstore = retriever.vector_db.docstore._dict
    
    dataset_counts = Counter()
    for doc_id, doc in docstore.items():
        dataset_name = doc.metadata.get('dataset', 'Unknown')
        dataset_counts[dataset_name] += 1

    print("\n=== FAISS DATABASE CONTENTS ===")
    print(f"Total documents: {len(docstore)}")
    print("-" * 30)
    for dataset, count in dataset_counts.items():
        print(f" - {dataset}: {count} examples")
    print("===============================\n")

except Exception as e:
    print(f"Error inspecting FAISS: {e}")
