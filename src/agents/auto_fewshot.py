import json
import tempfile
from pathlib import Path
import structlog
from src.agents.onboard import get_current_db_schema
from generate_questions import generate_batch
from src.rag.few_shot_retriever import FewShotRetriever

log = structlog.get_logger("auto_fewshot")

def auto_generate_and_index_fewshot(db_path: str, dataset_type: str, count: int = 15):
    """
    Tự động sinh kinh nghiệm ảo (Synthetic Few-shot) và nạp vào FAISS.
    Quy trình:
    1. Đọc schema của DB.
    2. Dùng Gemini sinh ra `count` câu hỏi và đáp án SQL.
    3. Lưu ra file JSON tạm.
    4. Gọi FAISS indexer nạp file JSON này.
    """
    log.info("Bắt đầu Auto-Few-Shot generation", db_path=db_path, count=count)
    
    questions = generate_batch(db_path=db_path, existing_questions=[], batch_num=1, batch_target=count)
    
    if not questions:
        log.warning("Không thể sinh câu hỏi tự động. Bỏ qua Auto-Few-Shot.")
        return False
        
    for i, q in enumerate(questions):
        q["id"] = i + 1
        
    output_data = {
        "metadata": {
            "dataset": f"Auto Synthetic - {dataset_type}",
            "generated_by": "System Auto Onboard"
        },
        "questions": questions
    }
    
    try:
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False, encoding='utf-8') as tmp:
            json.dump(output_data, tmp, ensure_ascii=False)
            tmp_path = tmp.name
    except Exception:
        pass
        
    try:
        retriever = FewShotRetriever()
        retriever.index_dataset(tmp_path, dataset_type=dataset_type)
        log.info("Nạp FAISS thành công", docs=len(questions), dataset_type=dataset_type)
        return True
    except Exception as e:
        log.error("Lỗi khi nạp FAISS", error=str(e))
        return False
