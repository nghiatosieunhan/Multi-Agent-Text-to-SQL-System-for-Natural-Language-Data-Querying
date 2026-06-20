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

    def index_dataset(self, data_path: str, dataset_type: str = "spider", start_offset: int = 0):
        """
        Thêm một dataset vào Vector DB.
        dataset_type: 'spider', 'bird', hoặc 'custom'
        """
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

        for item in train_data:
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

            metadatas.append({
                "sql": sql, 
                "db_id": item.get("db_id", "unknown"),
                "hint": hint,
                "dataset": dataset_type.lower() # Lưu tên chuẩn
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

    def retrieve(self, question: str, hint: str = "", dataset_type: str = None, k: int = 3) -> List[Dict[str, Any]]:
        """Tìm K ví dụ giống nhất với Bộ lọc Dataset (Filter)"""
        db = self._get_db()
        if db is None:
            return []
            
        # Tạo text tìm kiếm giống hệt lúc Index
        search_text = question
        if hint:
            search_text += f"\nHint/Evidence: {hint}"

        # BÍ QUYẾT 2: Filter để cô lập không gian tìm kiếm
        filter_dict = None
        if dataset_type:
            filter_dict = {"dataset": dataset_type.lower()}
        
        try:
            # FAISS hỗ trợ filter metadata tương tự
            # CẦN THÊM fetch_k RẤT LỚN VÌ FAISS LẤY fetch_k TRƯỚC RỒI MỚI LỌC FILTER!
            docs = db.similarity_search(search_text, k=k, filter=filter_dict, fetch_k=100000)
        except Exception as e:
            print(f"Retrieve Error: {e}")
            return []

        examples = []
        for d in docs:
            examples.append({
                "question": d.page_content,
                "sql": d.metadata.get("sql", ""),
                "hint": d.metadata.get("hint", ""),
                "dataset": d.metadata.get("dataset", "")
            })
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