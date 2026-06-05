from src.rag.few_shot_retriever import FewShotRetriever

print("Loading FAISS DB...")
retriever = FewShotRetriever()
retriever._get_db()

docstore = retriever.vector_db.docstore._dict
index_to_docstore_id = retriever.vector_db.index_to_docstore_id

ids_to_delete = []
for idx, doc_id in index_to_docstore_id.items():
    doc = docstore.get(doc_id)
    if doc and doc.metadata.get('dataset') == 'chinook_vn':
        ids_to_delete.append(doc_id)

print(f"Found {len(ids_to_delete)} examples for 'chinook_vn'. Deleting...")

if ids_to_delete:
    retriever.vector_db.delete(ids_to_delete)
    retriever.vector_db.save_local(retriever.persist_directory)
    print("Deleted successfully and saved FAISS DB.")
else:
    print("No 'chinook_vn' examples found.")

