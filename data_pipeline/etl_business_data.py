import pandas as pd
import numpy as np
import sqlite3
import os
import sys
import io

if os.name == "nt":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# Đường dẫn thư mục (tự động trỏ về thư mục data/business bên trong dự án)
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT_DIR = os.path.join(PROJECT_ROOT, "data", "business")
os.makedirs(OUTPUT_DIR, exist_ok=True)
DB_PATH = f"{OUTPUT_DIR}/sales_vn.sqlite"
RAW_CSV_PATH = f"{OUTPUT_DIR}/raw_messy_sales.csv"

def step1_generate_messy_data():
    """
    Bước 1: Giả lập quá trình Thu thập dữ liệu (Crawling/Extract).
    Tạo ra một tập CSV "bẩn" với các lỗi phổ biến trong thực tế.
    """
    print("[Buoc 1] Dang gia lap viec thu thap Raw Data...")
    
    data = {
        "Customer_Name": [" Nguyễn Văn A ", "Trần Thị B", " Lê Văn C", "Nguyễn Văn a", "Hoàng Thị D", np.nan],
        "Phone": ["0901234567", "098-765-4321", "O912345678", "0901234567", np.nan, "0933333333"],
        "Product": ["Laptop Dell", "Chuột Logitech ", "Bàn phím cơ", "Laptop Dell", "Màn hình LG", "Tai nghe Sony"],
        "Price": [" 15,000,000 ", "500.000", "1.500.000", "15000000", "3,500,000", "2000000"],
        "Quantity": [1, 2, np.nan, 1, -5, 3], # Lỗi số lượng âm và missing
        "Order_Date": ["2023/10/01", "02-10-2023", "2023-10-03", "2023/10/01", "2023-10-05", "10/06/2023"] # Lỗi định dạng ngày
    }
    
    df_raw = pd.DataFrame(data)
    df_raw.to_csv(RAW_CSV_PATH, index=False, encoding="utf-8-sig")
    print(f"Da luu Raw Data ban tai: {RAW_CSV_PATH}")
    return RAW_CSV_PATH

def step2_clean_data(csv_path):
    """
    Bước 2: Tiền xử lý và Làm sạch (Clean & Transform).
    """
    print("\n[Buoc 2] Bat dau qua trinh Data Cleaning...")
    df = pd.read_csv(csv_path)
    
    # 1. Xử lý Missing Values (NaN)
    print("   -> Đang xử lý Missing Values...")
    df.dropna(subset=["Customer_Name"], inplace=True) # Xóa dòng không có tên khách
    df["Quantity"] = df["Quantity"].fillna(1) # Thiếu số lượng thì mặc định là 1
    
    # 2. Xóa khoảng trắng thừa và chuẩn hóa chuỗi
    print("   -> Đang chuẩn hóa Chuỗi (Trim & Title)...")
    df["Customer_Name"] = df["Customer_Name"].str.strip().str.title()
    df["Product"] = df["Product"].str.strip()
    
    # 3. Làm sạch số điện thoại (Chỉ giữ lại số)
    print("   -> Đang làm sạch Số điện thoại...")
    df["Phone"] = df["Phone"].astype(str).str.replace(r'\D', '', regex=True)
    df["Phone"] = df["Phone"].replace("nan", "0000000000")
    
    # 4. Chuẩn hóa Cột Tiền tệ (Price) thành Integer
    print("   -> Đang chuyển đổi định dạng Tiền tệ...")
    df["Price"] = df["Price"].astype(str).str.replace(r'[,.]', '', regex=True).astype(int)
    
    # 5. Xử lý Logic (Số lượng không được âm)
    print("   -> Đang sửa lỗi Logic nghiệp vụ...")
    df["Quantity"] = df["Quantity"].apply(lambda x: abs(x) if x < 0 else x).astype(int)
    
    # 6. Chuẩn hóa Định dạng Ngày tháng (ISO 8601: YYYY-MM-DD)
    print("   -> Đang chuẩn hóa Định dạng Ngày tháng...")
    df["Order_Date"] = pd.to_datetime(df["Order_Date"], format="mixed").dt.strftime('%Y-%m-%d')
    
    # 7. Xóa dữ liệu trùng lặp (Duplicates)
    df.drop_duplicates(inplace=True)
    
    # 8. Kỹ thuật đặc trưng (Feature Engineering): Thêm cột Doanh thu (Revenue)
    df["Revenue"] = df["Price"] * df["Quantity"]
    
    print("Da lam sach du lieu thanh cong!")
    return df

def step3_load_to_database(df_clean):
    """
    Bước 3: Load (Lưu vào SQLite) dưới dạng Lược đồ chuẩn hóa (Relational Schema).
    """
    print("\n[Buoc 3] Dang chuan hoa Data Modeling va xuat ra SQLite...")
    
    # Tách bảng Customers (Bảng 1)
    df_customers = df_clean[['Customer_Name', 'Phone']].drop_duplicates().reset_index(drop=True)
    df_customers.index += 1 
    df_customers.index.name = 'CustomerID'
    df_customers = df_customers.reset_index()

    # Tách bảng Products (Bảng 2)
    df_products = df_clean[['Product', 'Price']].drop_duplicates().reset_index(drop=True)
    df_products.index += 1
    df_products.index.name = 'ProductID'
    df_products = df_products.reset_index()

    # Tạo bảng Orders (Bảng 3 - Transaction)
    df_orders = df_clean.merge(df_customers, on=['Customer_Name', 'Phone'])
    df_orders = df_orders.merge(df_products, on=['Product', 'Price'])
    df_orders = df_orders[['CustomerID', 'ProductID', 'Quantity', 'Revenue', 'Order_Date']]
    df_orders.index += 1
    df_orders.index.name = 'OrderID'
    df_orders = df_orders.reset_index()

    # Lưu vào SQLite
    conn = sqlite3.connect(DB_PATH)
    df_customers.to_sql("Customers", conn, if_exists="replace", index=False)
    df_products.to_sql("Products", conn, if_exists="replace", index=False)
    df_orders.to_sql("Orders", conn, if_exists="replace", index=False)
    conn.close()
    
    print(f"Da tao Database quan he chuan 3NF tai: {DB_PATH}")

if __name__ == "__main__":
    print("="*50)
    print("KHOI DONG DATA ENGINEERING (ETL) PIPELINE")
    print("="*50)
    raw_path = step1_generate_messy_data()
    clean_data = step2_clean_data(raw_path)
    step3_load_to_database(clean_data)
    print("\nETL PIPELINE HOAN TAT!")
