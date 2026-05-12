import pandas as pd
import sqlite3
import os
import sys
import io

# Đảm bảo hiển thị Tiếng Việt trên Windows console
if os.name == "nt":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

def clean_spider_data():
    PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    RAW_DIR = os.path.join(PROJECT_ROOT, "data", "raw_spider")
    
    if not os.path.exists(RAW_DIR):
        print(f"❌ Không tìm thấy thư mục {RAW_DIR}. Hãy chạy python make_data.py trước!")
        return

    print("⏳ Đang đọc 7 file CSV thô...")
    df_nv = pd.read_csv(os.path.join(RAW_DIR, "raw_nhanvien.csv"))
    df_kh = pd.read_csv(os.path.join(RAW_DIR, "raw_khachhang.csv"))
    df_ch = pd.read_csv(os.path.join(RAW_DIR, "raw_cuahang.csv"))
    df_dm = pd.read_csv(os.path.join(RAW_DIR, "raw_danhmuc.csv"))
    df_sp = pd.read_csv(os.path.join(RAW_DIR, "raw_sanpham.csv"))
    df_dh = pd.read_csv(os.path.join(RAW_DIR, "raw_donhang.csv"))
    df_ct = pd.read_csv(os.path.join(RAW_DIR, "raw_chitietdonhang.csv"))

    print("⏳ Đang tiến hành làm sạch (Data Cleaning) & Chuẩn hóa...")
    
    # 1. CLEAN NHANVIEN
    df_nv["Luong"] = df_nv["Luong"].astype(str).str.replace(r'[^\d]', '', regex=True).astype(int)
    df_nv["NgayVaoLam"] = pd.to_datetime(df_nv["NgayVaoLam"], format="mixed").dt.strftime('%Y-%m-%d')
    df_nv["TenNV"] = df_nv["TenNV"].str.strip()

    # 2. CLEAN KHACHHANG
    df_kh["TenKH"] = df_kh["TenKH"].str.strip()
    df_kh["SoDienThoai"] = df_kh["SoDienThoai"].astype(str).str.replace(r'\D', '', regex=True)
    df_kh["SoDienThoai"] = df_kh["SoDienThoai"].replace("None", "0000000000").replace("nan", "0000000000")
    df_kh["LoaiThe"] = df_kh["LoaiThe"].fillna("Normal")

    # 3. CLEAN CUAHANG & DANHMUC (Xóa khoảng trắng thừa)
    df_ch["KhuVuc"] = df_ch["KhuVuc"].str.strip()
    
    # 4. CLEAN SANPHAM
    df_sp["DonGia"] = df_sp["DonGia"].astype(str).str.replace(r'[^\d]', '', regex=True).astype(int)

    # 5. CLEAN DONHANG (Xóa lặp, sửa ngày)
    df_dh = df_dh.drop_duplicates()
    df_dh["NgayMua"] = pd.to_datetime(df_dh["NgayMua"], format="mixed").dt.strftime('%Y-%m-%d')

    # 6. CLEAN CHITIETDONHANG (Sửa lỗi âm, outlier 999)
    df_ct["SoLuong"] = df_ct["SoLuong"].apply(lambda x: abs(x) if x < 100 else 1).astype(int)
    df_ct["MucGiamGia"] = df_ct["MucGiamGia"].fillna(0.0)

    # LƯU VÀO SQLITE
    OUTPUT_DIR = os.path.join(PROJECT_ROOT, "data", "business")
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    DB_PATH = os.path.join(OUTPUT_DIR, "spider_sales_vn.sqlite")
    
    print(f"⏳ Đang ghi vào cơ sở dữ liệu SQLite: {DB_PATH} ...")
    conn = sqlite3.connect(DB_PATH)
    
    df_nv.to_sql("NhanVien", conn, if_exists="replace", index=False)
    df_kh.to_sql("KhachHang", conn, if_exists="replace", index=False)
    df_ch.to_sql("CuaHang", conn, if_exists="replace", index=False)
    df_dm.to_sql("DanhMuc", conn, if_exists="replace", index=False)
    df_sp.to_sql("SanPham", conn, if_exists="replace", index=False)
    df_dh.to_sql("DonHang", conn, if_exists="replace", index=False)
    df_ct.to_sql("ChiTietDonHang", conn, if_exists="replace", index=False)
    
    conn.close()
    print("✅ HOÀN TẤT! Hệ CSDL cấp độ Spider (7 bảng chằng chịt) đã sẵn sàng để thử thách AI.")

if __name__ == "__main__":
    print("="*60)
    print("🧹 KHỞI ĐỘNG CÔNG CỤ LÀM SẠCH VÀ CHUẨN HÓA DỮ LIỆU SPIDER")
    print("="*60)
    clean_spider_data()
