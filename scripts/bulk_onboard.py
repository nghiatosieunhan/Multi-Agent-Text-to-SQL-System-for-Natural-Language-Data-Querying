import os
import argparse
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import structlog
from tqdm import tqdm

from src.agents.onboard import onboard_db
from src.agents.auto_fewshot import auto_generate_and_index_fewshot

log = structlog.get_logger("bulk_onboard")

def process_single_db(db_path: Path):
    db_id = db_path.stem
    try:
        # 1. Quét Schema và sinh Semantic Cache bằng LLM
        onboard_db(str(db_path), db_id=db_id, description=f"Auto-onboarded DB: {db_id}")
        
        # 2. Tự động sinh Few-shot ảo và nhúng vào FAISS
        auto_generate_and_index_fewshot(str(db_path), dataset_type=db_id, count=10)
        
        return db_id, True, None
    except Exception as e:
        return db_id, False, str(e)

def main():
    parser = argparse.ArgumentParser(description="Tự động Onboard hàng loạt Database trong 1 thư mục")
    parser.add_argument("--dir", type=str, required=True, help="Đường dẫn đến thư mục chứa các file .sqlite")
    parser.add_argument("--workers", type=int, default=4, help="Số luồng chạy song song (mặc định: 4)")
    args = parser.parse_args()

    target_dir = Path(args.dir)
    if not target_dir.exists() or not target_dir.is_dir():
        print(f"❌ Thư mục không tồn tại: {target_dir}")
        return

    # Tìm tất cả file .sqlite hoặc .db
    db_files = list(target_dir.rglob("*.sqlite")) + list(target_dir.rglob("*.db"))
    
    if not db_files:
        print(f"⚠️ Không tìm thấy file Database nào trong {target_dir}")
        return

    print(f"🚀 Tìm thấy {len(db_files)} databases. Bắt đầu Bulk Onboarding...")
    
    success_count = 0
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(process_single_db, db_path): db_path for db_path in db_files}
        
        for future in tqdm(as_completed(futures), total=len(futures), desc="Tiến độ Onboarding"):
            db_id, success, error_msg = future.result()
            if success:
                success_count += 1
                log.info("Thành công", db=db_id)
            else:
                log.error("Thất bại", db=db_id, error=error_msg)

    print(f"\n✅ Hoàn tất! Đã onboard thành công {success_count}/{len(db_files)} databases.")

if __name__ == "__main__":
    main()
