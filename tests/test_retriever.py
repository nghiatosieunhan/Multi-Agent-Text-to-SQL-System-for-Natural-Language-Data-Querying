from src.rag.few_shot_retriever import FewShotRetriever
r = FewShotRetriever()
examples = r.retrieve("Số lượng bài hát thể loại Metal", k=3, dataset_type="chinook_vn")
print("RETRIEVED EXAMPLES:")
for e in examples:
    print(e)
