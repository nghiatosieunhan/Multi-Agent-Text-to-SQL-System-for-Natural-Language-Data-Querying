import json
import os
from typing import List, Dict, Any
from langchain_community.vectorstores import FAISS
from langchain_google_vertexai import VertexAIEmbeddings
from src.config import config
import structlog

log = structlog.get_logger("few_shot")

class FewShotRetriever:
    def __init__(self, persist_directory=None):
        self.embeddings = VertexAIEmbeddings(
            model_name=config.EMBEDDING_MODEL,
            project=config.GOOGLE_CLOUD_PROJECT,
            location=config.GOOGLE_CLOUD_LOCATION
        )
        self.persist_directory = persist_directory or str(config.FAISS_PERSIST_DIR)
        self.vector_db = None

    def _get_db(self):
        if not self.vector_db:
            if os.path.exists(self.persist_directory):
                # allow_dangerous_deserialization là cần thiết cho các bản langchain mới khi load FAISS
                self.vector_db = FAISS.load_local(self.persist_directory, self.embeddings, allow_dangerous_deserialization=True)
            else:
                self.vector_db = None
        return self.vector_db

    def index_dataset(self, data_path: str, dataset_type: str = "spider", start_offset: int = 0, split: str = "train"):
        """Index a dataset into FAISS."""
        if not os.path.exists(data_path):
            print(f"❌ Không tìm thấy file: {data_path}")
            return

        with open(data_path, "r", encoding="utf-8") as f:
            train_data = json.load(f)
            
        # Hỗ trợ format có chứa root key "questions" (như data_vn.json)
        if isinstance(train_data, dict) and "questions" in train_data:
            train_data = train_data["questions"]
            
        if start_offset > 0:
            train_data = train_data[start_offset:]
            print(f"Resuming from offset {start_offset}. Remaining items: {len(train_data)}")
        
        texts = []
        metadatas = []

        for i, item in enumerate(train_data):
            question = item.get("question", "")
            
            if dataset_type.lower() == "bird":
                sql = item.get("SQL", "")
                hint = item.get("evidence", "")
            else: 
                # Hỗ trợ key "gold_sql" trong data_vn.json hoặc "query" trong spider
                sql = item.get("gold_sql", item.get("query", ""))
                hint = item.get("hint", "")

            # BÍ QUYẾT 1: Nhúng cả Hint/Evidence vào Text để tìm kiếm chuẩn xác hơn
            search_text = question
            if hint:
                search_text += f"\nHint/Evidence: {hint}"
            texts.append(search_text)

            dataset_value = item.get("dataset", dataset_type).lower()
            split_value = item.get("split", split)
            db_value = item.get("db_id", "unknown")
            
            metadatas.append({
                "sql": sql,
                "db_id": db_value,
                "hint": hint,
                "dataset": dataset_value,
                "split": split_value,
                "example_id": str(item.get("id", i)),
                "intent": item.get("intent", ""),
                "pattern": item.get("pattern", ""),
                "tables": item.get("tables", []),
                "output_columns": item.get("output_columns", []),
                "verified": item.get("verified", False),
                "question": item.get("question", ""),
                "question_en": item.get("question_en", "")
            })
        
        # Load DB hiện tại nếu có
        self._get_db()
        
        import time
        batch_size = 100
        for i in range(0, len(texts), batch_size):
            batch_texts = texts[i:i+batch_size]
            batch_metadatas = metadatas[i:i+batch_size]
            
            success = False
            for attempt in range(10):
                try:
                    if self.vector_db is None:
                        self.vector_db = FAISS.from_texts(batch_texts, self.embeddings, metadatas=batch_metadatas)
                    else:
                        self.vector_db.add_texts(texts=batch_texts, metadatas=batch_metadatas)
                    success = True
                    break
                except Exception as e:
                    print(f"Lỗi RateLimit ở mốc {i}, chờ 30s rồi thử lại (Lần {attempt+1}/10)...")
                    time.sleep(30)
            
            if not success:
                print("Thất bại nạp dữ liệu do Rate Limit kéo dài. Bỏ qua các câu còn lại.")
                break
            
            print(f"✅ Đã index {min(i + batch_size, len(texts))} / {len(texts)} ...")
            # Cứ mỗi lần add xong thì lưu lại ổ cứng
            self.vector_db.save_local(self.persist_directory)
            time.sleep(3) # Delay nhỏ để tránh spam API liên tục
        
        print(f"✅ Đã index xong tập {dataset_type} từ {data_path} vào FAISS DB!")

    def retrieve(
        self,
        question: str,
        hint: str = "",
        dataset_type: str = None,
        k: int = 3,
        similarity_threshold: float = 0.55,
        split: str = None,
        db_id: str = None
    ) -> List[Dict[str, Any]]:
        """Retrieve up to K most-similar examples, filtering by similarity threshold.

        Args:
            question: User question.
            hint: Optional hint/evidence text (same as indexing).
            dataset_type: Dataset filter ('chinook_vn', 'northwind', etc.).
            k: Maximum number of examples to return.
            similarity_threshold: Cosine similarity minimum (0-1). Examples below
                this score are dropped rather than injected into the prompt.
            split: Expected split label ('train' or 'fewshot'). Future use for train/val/test isolation.
            db_id: Optional exact DB filter if required.
        Returns:
            List of {question, sql, hint, dataset, similarity} dicts.
            Returns [] if no example meets the threshold — generator runs zero-shot.
        """
        db = self._get_db()
        if db is None:
            return []

        # Build search text matching indexing format
        search_text = question
        if hint:
            search_text += f"\nHint/Evidence: {hint}"

        filter_dict = {}
        if dataset_type:
            filter_dict["dataset"] = dataset_type.lower()
        if split:
            filter_dict["split"] = split
        if db_id:
            filter_dict["db_id"] = db_id

        if not filter_dict:
            filter_dict = None

        try:
            # FAISS applies metadata filtering after vector candidate retrieval.
            # Spider/BIRD span many databases, so a tiny global candidate pool can
            # contain no examples from the requested db_id even when many exist.
            fetch_k = max(k * 500, 1000) if db_id else max(k * 20, 50)
            log.info("few_shot_search_start", filter_dict=filter_dict, k=k, fetch_k=fetch_k, threshold=similarity_threshold)
            docs_scores = db.similarity_search_with_score(
                search_text, k=k, filter=filter_dict, fetch_k=fetch_k
            )
            if db_id and not docs_scores:
                # Spider is cross-database by design: dev schemas are unseen in
                # train. Fall back to structural examples from other databases.
                fallback_filter = dict(filter_dict or {})
                fallback_filter.pop("db_id", None)
                log.info(
                    "few_shot_cross_db_fallback",
                    requested_db_id=db_id,
                    filter_dict=fallback_filter,
                )
                docs_scores = db.similarity_search_with_score(
                    search_text,
                    k=k,
                    filter=fallback_filter or None,
                    fetch_k=max(k * 20, 50),
                )
        except Exception as e:
            log.warning("few_shot_retrieve_error", error=str(e))
            return []

        examples = []
        example_ids = []
        for doc, score in docs_scores:
            similarity = 1.0 / (1.0 + float(score))
            
            log.info(
                "few_shot_candidate",
                raw_score=float(score),
                similarity=round(similarity, 3),
                example_id=doc.metadata.get("example_id"),
                dataset=doc.metadata.get("dataset"),
                split=doc.metadata.get("split"),
                db_id=doc.metadata.get("db_id"),
                intent=doc.metadata.get("intent")
            )
            
            if similarity < similarity_threshold:
                log.info(
                    "few_shot_dropped",
                    reason="below_threshold",
                    similarity=round(similarity, 3),
                    threshold=similarity_threshold,
                    question=doc.page_content[:60],
                )
                continue
                
            examples.append({
                "question": doc.page_content,
                "sql": doc.metadata.get("sql", ""),
                "hint": doc.metadata.get("hint", ""),
                "dataset": doc.metadata.get("dataset", ""),
                "split": doc.metadata.get("split", ""),
                "db_id": doc.metadata.get("db_id", ""),
                "cross_db": bool(db_id and doc.metadata.get("db_id") != db_id),
                "example_id": doc.metadata.get("example_id", ""),
                "intent": doc.metadata.get("intent", ""),
                "pattern": doc.metadata.get("pattern", ""),
                "tables": doc.metadata.get("tables", []),
                "output_columns": doc.metadata.get("output_columns", []),
                "similarity": round(similarity, 3),
                "raw_score": float(score),
            })
            if "example_id" in doc.metadata:
                example_ids.append(doc.metadata["example_id"])

        if not examples:
            log.info(
                "few_shot_zero_shot", 
                reason="no_example_above_threshold",
                filter_dict=filter_dict,
                candidates=len(docs_scores),
                threshold=similarity_threshold
            )
        else:
            log.info("few_shot_retrieved", 
                     fewshot_dataset_used=dataset_type,
                     fewshot_split_used=split,
                     fewshot_db_id_used=db_id,
                     fewshot_top_k=k,
                     fewshot_example_ids=example_ids)
        return examples

    def add_single_example(self, question: str, sql: str, hint: str = "", dataset_type: str = "custom"):
        """
        [BÍ QUYẾT TỪ VANNA AI]: Continuous Learning / Auto-Train
        Lưu một cặp Câu hỏi - SQL mới vào VectorDB. 
        Lần sau nếu User hỏi câu tương tự, nó sẽ được mang ra làm Few-Shot.
        """
        search_text = question
        if hint:
            search_text += f"\nHint/Evidence: {hint}"

        metadata = {
            "sql": sql,
            "db_id": "auto_learned",
            "hint": hint,
            "dataset": dataset_type.lower()
        }

        db = self._get_db()
        if db is None:
            self.vector_db = FAISS.from_texts([search_text], self.embeddings, metadatas=[metadata])
        else:
            db.add_texts([search_text], metadatas=[metadata])
        
        self.vector_db.save_local(self.persist_directory)
        print(f"✅ Đã đưa vào bộ nhớ RAG: {question}")
