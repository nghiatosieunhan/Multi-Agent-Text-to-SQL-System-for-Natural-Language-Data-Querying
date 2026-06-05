from src.rag.few_shot_retriever import FewShotRetriever

r = FewShotRetriever()
db = r._get_db()
docs = db.similarity_search("Metal", k=3, filter={"dataset": "chinook_vn"})
print("With filter:", len(docs))

docs2 = db.similarity_search("Metal", k=3)
print("Without filter:", len(docs2))
