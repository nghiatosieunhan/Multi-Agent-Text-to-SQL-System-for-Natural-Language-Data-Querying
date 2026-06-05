import sqlite3
import os
import random
from datetime import datetime, timedelta
from pathlib import Path

# Đảm bảo thư mục tồn tại
db_dir = Path("data/business_data")
db_dir.mkdir(parents=True, exist_ok=True)
db_path = db_dir / "business.sqlite"

# Xóa DB cũ nếu có để tạo lại từ đầu
if db_path.exists():
    os.remove(db_path)

conn = sqlite3.connect(db_path)
cursor = conn.cursor()

# 1. Tạo bảng Khách hàng
cursor.execute('''
CREATE TABLE khach_hang (
    ma_khach_hang INTEGER PRIMARY KEY AUTOINCREMENT,
    ten_khach_hang TEXT NOT NULL,
    thanh_pho TEXT,
    phan_loai TEXT -- VIP, Standard, Tiem nang
)
''')

# 2. Tạo bảng Nhân viên
cursor.execute('''
CREATE TABLE nhan_vien (
    ma_nhan_vien INTEGER PRIMARY KEY AUTOINCREMENT,
    ten_nhan_vien TEXT NOT NULL,
    phong_ban TEXT,
    ngay_vao_lam DATE
)
''')

# 3. Tạo bảng Sản phẩm
cursor.execute('''
CREATE TABLE san_pham (
    ma_san_pham INTEGER PRIMARY KEY AUTOINCREMENT,
    ten_san_pham TEXT NOT NULL,
    danh_muc TEXT,
    gia_ban REAL NOT NULL
)
''')

# 4. Tạo bảng Đơn hàng
cursor.execute('''
CREATE TABLE don_hang (
    ma_don_hang INTEGER PRIMARY KEY AUTOINCREMENT,
    ma_khach_hang INTEGER,
    ma_nhan_vien INTEGER,
    ngay_mua DATE NOT NULL,
    trang_thai TEXT,
    FOREIGN KEY(ma_khach_hang) REFERENCES khach_hang(ma_khach_hang),
    FOREIGN KEY(ma_nhan_vien) REFERENCES nhan_vien(ma_nhan_vien)
)
''')

# 5. Tạo bảng Chi tiết đơn hàng
cursor.execute('''
CREATE TABLE chi_tiet_don_hang (
    ma_don_hang INTEGER,
    ma_san_pham INTEGER,
    so_luong INTEGER NOT NULL,
    gia_ban_thuc_te REAL NOT NULL,
    FOREIGN KEY(ma_don_hang) REFERENCES don_hang(ma_don_hang),
    FOREIGN KEY(ma_san_pham) REFERENCES san_pham(ma_san_pham),
    PRIMARY KEY (ma_don_hang, ma_san_pham)
)
''')

# === INSERT DỮ LIỆU MẪU (MOCK DATA) ===

# Khách hàng
khach_hangs = [
    ("Công ty TNHH Hưng Phát", "Hà Nội", "VIP"),
    ("Cửa hàng Tiện lợi 24h", "TP.HCM", "Standard"),
    ("Tập đoàn ABC", "Đà Nẵng", "VIP"),
    ("Đại lý Bán lẻ Nam Dũng", "Hà Nội", "Standard"),
    ("Siêu thị Mini VinX", "Hải Phòng", "Tiem nang")
]
cursor.executemany("INSERT INTO khach_hang (ten_khach_hang, thanh_pho, phan_loai) VALUES (?, ?, ?)", khach_hangs)

# Nhân viên
nhan_viens = [
    ("Nguyễn Văn A", "Kinh doanh", "2020-05-10"),
    ("Trần Thị B", "Kinh doanh", "2021-02-15"),
    ("Lê Hoàng C", "Kinh doanh", "2022-08-20")
]
cursor.executemany("INSERT INTO nhan_vien (ten_nhan_vien, phong_ban, ngay_vao_lam) VALUES (?, ?, ?)", nhan_viens)

# Sản phẩm
san_phams = [
    ("Phần mềm Quản lý Nhân sự", "Phần mềm", 50000000),
    ("Phần mềm Kế toán", "Phần mềm", 35000000),
    ("Gói Bảo trì Hệ thống", "Dịch vụ", 15000000),
    ("Tư vấn Chuyển đổi số", "Dịch vụ", 100000000),
    ("Server Dell PowerEdge", "Phần cứng", 80000000)
]
cursor.executemany("INSERT INTO san_pham (ten_san_pham, danh_muc, gia_ban) VALUES (?, ?, ?)", san_phams)

# Đơn hàng & Chi tiết (Tạo ngẫu nhiên khoảng 50 đơn hàng trong năm 2023-2024)
start_date = datetime(2023, 1, 1)
statuses = ["Hoàn thành", "Hoàn thành", "Hoàn thành", "Đang xử lý", "Đã hủy"]

for _ in range(50):
    ma_kh = random.randint(1, len(khach_hangs))
    ma_nv = random.randint(1, len(nhan_viens))
    # Random ngày trong 500 ngày qua
    random_days = random.randint(0, 500)
    ngay_mua = (start_date + timedelta(days=random_days)).strftime("%Y-%m-%d")
    trang_thai = random.choice(statuses)
    
    cursor.execute("INSERT INTO don_hang (ma_khach_hang, ma_nhan_vien, ngay_mua, trang_thai) VALUES (?, ?, ?, ?)", 
                   (ma_kh, ma_nv, ngay_mua, trang_thai))
    ma_dh = cursor.lastrowid
    
    # Random 1 đến 3 sản phẩm cho mỗi đơn hàng
    num_products = random.randint(1, 3)
    sp_ids = random.sample(range(1, len(san_phams) + 1), num_products)
    
    for ma_sp in sp_ids:
        so_luong = random.randint(1, 5)
        # Lấy giá bán gốc
        cursor.execute("SELECT gia_ban FROM san_pham WHERE ma_san_pham = ?", (ma_sp,))
        gia_goc = cursor.fetchone()[0]
        # Random giảm giá 0-10%
        gia_thuc_te = gia_goc * (1 - random.uniform(0, 0.1))
        
        cursor.execute("INSERT INTO chi_tiet_don_hang (ma_don_hang, ma_san_pham, so_luong, gia_ban_thuc_te) VALUES (?, ?, ?, ?)",
                       (ma_dh, ma_sp, so_luong, gia_thuc_te))

conn.commit()
conn.close()

print(f"✅ Đã tạo thành công Database Doanh nghiệp ảo tại: {db_path}")
print("Bao gồm: khach_hang, nhan_vien, san_pham, don_hang, chi_tiet_don_hang.")
