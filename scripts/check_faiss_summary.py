from src.rag.few_shot_retriever import FewShotRetriever
from collections import Counter

retriever = FewShotRetriever()
retriever._get_db()

docstore = retriever.vector_db.docstore._dict
index_to_docstore_id = retriever.vector_db.index_to_docstore_id

datasets = Counter()
db_ids = Counter()
for idx, doc_id in index_to_docstore_id.items():
    doc = docstore.get(doc_id)
    if doc:
        ds = doc.metadata.get('dataset', 'unknown')
        db = doc.metadata.get('db_id', 'unknown')
        datasets[ds] += 1
        db_ids[db] += 1

print("Datasets in FAISS:", datasets)
print("DB IDs in FAISS:", db_ids)
