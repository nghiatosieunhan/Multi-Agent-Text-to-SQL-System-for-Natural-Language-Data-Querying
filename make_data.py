import pandas as pd
import numpy as np
from faker import Faker
import random
import sys
import io
import os
from datetime import datetime, timedelta

if os.name == "nt":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

fake = Faker('vi_VN')

def generate_complex_data():
    print("🚀 Bắt đầu sinh hệ thống dữ liệu 7 bảng (Độ khó Spider/Chinook)...")
    
    # 1. NHÂN VIÊN (Có Self-Join Quản lý)
    print(" -> Sinh dữ liệu Nhân Viên...")
    nhan_viens = []
    nhan_viens.append({"MaNV": "NV001", "TenNV": fake.name(), "ChucVu": "Giám Đốc", "MaNQL": None, "NgayVaoLam": "2020-01-01", "Luong": "50,000,000"})
    nhan_viens.append({"MaNV": "NV002", "TenNV": fake.name(), "ChucVu": "Trưởng Phòng Sale", "MaNQL": "NV001", "NgayVaoLam": "2020-05-15", "Luong": "30,000,000"})
    for i in range(3, 21):
        ngay_vao = fake.date_between(start_date='-3y', end_date='today')
        ngay_str = ngay_vao.strftime("%Y/%m/%d") if random.random() < 0.2 else ngay_vao.strftime("%Y-%m-%d")
        luong = random.randint(8, 20) * 1000000
        luong_str = f"{luong} VNĐ" if random.random() < 0.2 else str(luong)
        
        nhan_viens.append({
            "MaNV": f"NV{i:03d}", "TenNV": fake.name(), "ChucVu": "Nhân viên Sale",
            "MaNQL": "NV002", "NgayVaoLam": ngay_str, "Luong": luong_str
        })
    df_nv = pd.DataFrame(nhan_viens)

    # 2. CỬA HÀNG
    print(" -> Sinh dữ liệu Cửa Hàng...")
    cua_hangs = [
        {"MaCH": "CH01", "TenCH": "Chi nhánh Hà Nội", "KhuVuc": "Miền Bắc"},
        {"MaCH": "CH02", "TenCH": "Chi nhánh Đà Nẵng", "KhuVuc": "Miền Trung"},
        {"MaCH": "CH03", "TenCH": "Chi nhánh TP.HCM", "KhuVuc": " Miền Nam "},
        {"MaCH": "CH04", "TenCH": "Chi nhánh Cần Thơ", "KhuVuc": "Miền Nam"}
    ]
    df_ch = pd.DataFrame(cua_hangs)

    # 3. KHÁCH HÀNG
    print(" -> Sinh dữ liệu Khách Hàng...")
    khach_hangs = []
    nv_sales = [nv["MaNV"] for nv in nhan_viens if nv["ChucVu"] == "Nhân viên Sale"]
    for i in range(1, 301):
        phone = fake.phone_number()
        if random.random() < 0.1: phone = phone.replace(" ", ".")
        elif random.random() < 0.05: phone = None
            
        khach_hangs.append({
            "MaKH": f"KH{i:04d}",
            "TenKH": fake.name() + " " if random.random() < 0.1 else fake.name(),
            "SoDienThoai": phone,
            "Email": fake.ascii_free_email(),
            "LoaiThe": random.choice(["VIP", "Thành viên", "Vãng lai", None]),
            "MaNV_PhuTrach": random.choice(nv_sales)
        })
    df_kh = pd.DataFrame(khach_hangs)

    # 4. DANH MỤC
    print(" -> Sinh dữ liệu Danh Mục...")
    danh_mucs = [
        {"MaDM": "DM1", "TenDM": "Điện thoại"},
        {"MaDM": "DM2", "TenDM": "Laptop"},
        {"MaDM": "DM3", "TenDM": "Phụ kiện"},
        {"MaDM": "DM4", "TenDM": "Gia dụng"}
    ]
    df_dm = pd.DataFrame(danh_mucs)

    # 5. SẢN PHẨM
    print(" -> Sinh dữ liệu Sản Phẩm...")
    san_phams = []
    sp_names = [
        ("iPhone 15 Pro Max", "DM1", 30000000), ("Samsung Galaxy S24", "DM1", 25000000),
        ("MacBook Air M2", "DM2", 22000000), ("Dell XPS 15", "DM2", 40000000),
        ("AirPods Pro", "DM3", 5000000), ("Chuột Logitech MX Master", "DM3", 2500000),
        ("Nồi chiên không dầu", "DM4", 1500000), ("Robot hút bụi", "DM4", 8000000)
    ]
    for i, (ten, madm, gia) in enumerate(sp_names):
        gia_str = f"{gia:,} VNĐ" if random.random() < 0.2 else gia
        san_phams.append({"MaSP": f"SP{i+1:03d}", "TenSP": ten, "MaDM": madm, "DonGia": gia_str})
    df_sp = pd.DataFrame(san_phams)

    # 6. ĐƠN HÀNG & 7. CHI TIẾT ĐƠN HÀNG
    print(" -> Sinh dữ liệu Đơn Hàng & Chi Tiết...")
    don_hangs = []
    chi_tiets = []
    
    for i in range(1, 1001):
        ngay_mua = fake.date_between(start_date='-2y', end_date='today')
        ngay_str = ngay_mua.strftime("%d/%m/%Y") if random.random() < 0.2 else ngay_mua.strftime("%Y-%m-%d")
        ma_dh = f"DH{i:05d}"
        don_hangs.append({
            "MaDH": ma_dh,
            "MaKH": random.choice(df_kh["MaKH"].tolist()),
            "MaCH": random.choice(df_ch["MaCH"].tolist()),
            "MaNV": random.choice(nv_sales),
            "NgayMua": ngay_str,
            "TrangThai": random.choice(["Hoàn thành", "Đang giao", "Đã hủy"])
        })
        
        num_items = random.randint(1, 3)
        sps = random.sample(san_phams, num_items)
        for sp in sps:
            sl = random.randint(1, 5)
            if random.random() < 0.05: sl = -sl 
            elif random.random() < 0.02: sl = 999 
            giam_gia = random.choice([0, 0, 0, 0.05, 0.1, 0.2])
            chi_tiets.append({
                "MaDH": ma_dh,
                "MaSP": sp["MaSP"],
                "SoLuong": sl,
                "MucGiamGia": giam_gia
            })
            
    df_dh = pd.DataFrame(don_hangs)
    df_ct = pd.DataFrame(chi_tiets)
    
    # Tạo duplicate lỗi cho Đơn hàng
    df_dh = pd.concat([df_dh, df_dh.sample(n=30)], ignore_index=True)

    # LƯU RA CSV
    PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
    RAW_DIR = os.path.join(PROJECT_ROOT, "data", "raw_spider")
    os.makedirs(RAW_DIR, exist_ok=True)
    
    df_nv.to_csv(os.path.join(RAW_DIR, "raw_nhanvien.csv"), index=False, encoding='utf-8-sig')
    df_kh.to_csv(os.path.join(RAW_DIR, "raw_khachhang.csv"), index=False, encoding='utf-8-sig')
    df_ch.to_csv(os.path.join(RAW_DIR, "raw_cuahang.csv"), index=False, encoding='utf-8-sig')
    df_dm.to_csv(os.path.join(RAW_DIR, "raw_danhmuc.csv"), index=False, encoding='utf-8-sig')
    df_sp.to_csv(os.path.join(RAW_DIR, "raw_sanpham.csv"), index=False, encoding='utf-8-sig')
    df_dh.to_csv(os.path.join(RAW_DIR, "raw_donhang.csv"), index=False, encoding='utf-8-sig')
    df_ct.to_csv(os.path.join(RAW_DIR, "raw_chitietdonhang.csv"), index=False, encoding='utf-8-sig')

    print(f"🎉 Hoàn tất! Đã xuất 7 file CSV bẩn ra thư mục: {RAW_DIR}")

if __name__ == "__main__":
    generate_complex_data()