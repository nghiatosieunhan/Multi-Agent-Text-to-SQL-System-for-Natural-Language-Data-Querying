from src.rag.few_shot_retriever import FewShotRetriever
from collections import Counter

retriever = FewShotRetriever()
retriever._get_db()

docstore = retriever.vector_db.docstore._dict
index_to_docstore_id = retriever.vector_db.index_to_docstore_id

datasets = []
for idx, doc_id in index_to_docstore_id.items():
    doc = docstore.get(doc_id)
    if doc:
        ds = doc.metadata.get('dataset', 'UNKNOWN')
        datasets.append(ds)

print("Datasets in FAISS:")
for ds, count in Counter(datasets).most_common():
    print(f" - {ds}: {count} examples")
