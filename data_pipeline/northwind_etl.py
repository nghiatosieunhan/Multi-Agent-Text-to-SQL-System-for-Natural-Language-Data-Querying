import os
import sqlite3
import pandas as pd
import numpy as np
from faker import Faker
import random
from pathlib import Path

# Cấu hình đường dẫn
BASE_DIR = Path(__file__).resolve().parent.parent
DB_SOURCE_PATH = BASE_DIR / "data" / "northwind" / "northwind.sqlite"
RAW_CSV_DIR = BASE_DIR / "data" / "northwind_raw_vn"
CLEAN_DB_DIR = BASE_DIR / "data" / "northwind_vn"

RAW_CSV_DIR.mkdir(parents=True, exist_ok=True)
CLEAN_DB_DIR.mkdir(parents=True, exist_ok=True)

fake = Faker('vi_VN')

def step1_obfuscate_and_export():
    """Bước 1: Trích xuất từ Northwind chuẩn, Việt hóa và Cố tình làm bẩn dữ liệu."""
    print("--- BƯỚC 1: TRÍCH XUẤT, VIỆT HÓA & LÀM BẨN DỮ LIỆU ---")
    conn = sqlite3.connect(DB_SOURCE_PATH)
    
    # 1. Bảng Customers
    df_customers = pd.read_sql_query("SELECT * FROM Customers", conn)
    print(f"Đang Việt hóa & làm bẩn {len(df_customers)} dòng Customers...")
    
    # Việt hóa
    df_customers['CompanyName'] = [fake.company() for _ in range(len(df_customers))]
    df_customers['ContactName'] = [fake.name() for _ in range(len(df_customers))]
    df_customers['Address'] = [fake.street_address() for _ in range(len(df_customers))]
    df_customers['City'] = [fake.city() for _ in range(len(df_customers))]
    df_customers['Country'] = 'Việt Nam'
    
    # Làm bẩn (Injecting Noise)
    # Tự động tạo 10% giá trị NaN cho số điện thoại
    mask = np.random.rand(len(df_customers)) < 0.10
    df_customers.loc[mask, 'Phone'] = np.nan
    # Chèn ký tự rác vào tên
    df_customers.loc[0, 'ContactName'] = "Nguyễn Văn A #$%" 
    
    df_customers.to_csv(RAW_CSV_DIR / "customers_raw.csv", index=False)
    
    # 2. Bảng Employees
    df_employees = pd.read_sql_query("SELECT * FROM Employees", conn)
    print(f"Đang Việt hóa & làm bẩn {len(df_employees)} dòng Employees...")
    
    df_employees['LastName'] = [fake.last_name() for _ in range(len(df_employees))]
    df_employees['FirstName'] = [fake.first_name() for _ in range(len(df_employees))]
    df_employees['City'] = [fake.city() for _ in range(len(df_employees))]
    df_employees['Country'] = 'Việt Nam'
    
    df_employees.to_csv(RAW_CSV_DIR / "employees_raw.csv", index=False)

    # 3. Bảng Orders
    df_orders = pd.read_sql_query("SELECT * FROM Orders", conn)
    print(f"Đang Việt hóa & làm bẩn {len(df_orders)} dòng Orders...")
    
    df_orders['ShipName'] = [fake.name() for _ in range(len(df_orders))]
    df_orders['ShipAddress'] = [fake.street_address() for _ in range(len(df_orders))]
    df_orders['ShipCity'] = [fake.city() for _ in range(len(df_orders))]
    df_orders['ShipCountry'] = 'Việt Nam'
    
    # Làm bẩn định dạng ngày tháng (Format corruption)
    def corrupt_date(d):
        if pd.isna(d): return d
        r = random.random()
        if r < 0.2:
            return "N/A" # 20% bị rác
        elif r < 0.5:
            # Sửa từ YYYY-MM-DD sang DD/MM/YYYY
            try:
                dt = pd.to_datetime(d)
                return dt.strftime("%d/%m/%Y")
            except:
                return d
        return d

    df_orders['OrderDate'] = df_orders['OrderDate'].apply(corrupt_date)
    
    df_orders.to_csv(RAW_CSV_DIR / "orders_raw.csv", index=False)
    conn.close()
    print(f"Đã xuất file CSV thô chứa lỗi ra thư mục: {RAW_CSV_DIR}\n")


def step2_clean_and_load():
    """Bước 2: Đọc file CSV bẩn, dùng Pandas làm sạch và ghi vào SQLite chuẩn."""
    print("--- BƯỚC 2: DATA CLEANING & TRANSFORMATION ---")
    
    # Tạo connection mới cho CSDL sạch
    clean_db_path = CLEAN_DB_DIR / "Northwind_VN.sqlite"
    # Xóa file cũ nếu tồn tại
    if clean_db_path.exists():
        clean_db_path.unlink()
        
    conn_clean = sqlite3.connect(clean_db_path)
    
    # 1. Clean Customers
    df_customers = pd.read_csv(RAW_CSV_DIR / "customers_raw.csv")
    print("Đang làm sạch Customers...")
    # Xử lý Missing values (NaN)
    df_customers['Phone'] = df_customers['Phone'].fillna("Chưa cập nhật")
    # Xử lý ký tự rác bằng Regex
    df_customers['ContactName'] = df_customers['ContactName'].str.replace(r'[^a-zA-ZÀ-ỹ\s]', '', regex=True)
    df_customers.to_sql("Customers", conn_clean, if_exists="replace", index=False)
    
    # 2. Clean Employees
    df_employees = pd.read_csv(RAW_CSV_DIR / "employees_raw.csv")
    print("Đang làm sạch Employees...")
    df_employees.to_sql("Employees", conn_clean, if_exists="replace", index=False)
    
    # 3. Clean Orders
    df_orders = pd.read_csv(RAW_CSV_DIR / "orders_raw.csv")
    print("Đang làm sạch Orders...")
    
    # Xử lý ngày tháng lộn xộn
    def fix_date(d):
        if str(d) == "N/A": return np.nan # Chuyển rác về Null chuẩn
        try:
            # Parse mixed format, dayfirst=True giúp giải quyết DD/MM/YYYY
            return pd.to_datetime(d, dayfirst=True, format="mixed").strftime("%Y-%m-%d %H:%M:%S")
        except:
            return np.nan
            
    df_orders['OrderDate'] = df_orders['OrderDate'].apply(fix_date)
    # Xóa các dòng mất OrderDate (Data Quality Drop)
    df_orders = df_orders.dropna(subset=['OrderDate'])
    
    df_orders.to_sql("Orders", conn_clean, if_exists="replace", index=False)
    
    conn_clean.close()
    print(f"\n✅ HOÀN TẤT! Dữ liệu đã được làm sạch và lưu tại: {clean_db_path}")

if __name__ == "__main__":
    step1_obfuscate_and_export()
    step2_clean_and_load()
