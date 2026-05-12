"""
Data Pipeline — Crawling, Cleaning, Transformation cho Text-to-SQL.
Hỗ trợ: CSV, JSON, web scraping → SQLite tables.
"""
import re
import json
import time
from pathlib import Path
from typing import Optional, Any

import requests
import pandas as pd
import structlog
from bs4 import BeautifulSoup

log = structlog.get_logger("data_pipeline")

# ── Data Cleaner ─────────────────────────────────────────────────────────────
class DataCleaner:
    """Clean và transform raw data thành structured format."""

    @staticmethod
    def clean_column_names(df: pd.DataFrame) -> pd.DataFrame:
        """Chuẩn hóa tên cột: lowercase, underscore, không dấu."""
        def _normalize(col: str) -> str:
            col = str(col).strip()
            col = col.lower()
            # Thay khoảng trắng / dấu → underscore
            col = re.sub(r"[\s\-\.\/\\]", "_", col)
            col = re.sub(r"[^a-z0-9_]", "", col)
            col = re.sub(r"_+", "_", col)
            col = col.strip("_")
            return col[:64]  # SQLite limit

        df.columns = [_normalize(c) for c in df.columns]
        return df

    @staticmethod
    def handle_missing_values(df: pd.DataFrame, strategy: str = "drop") -> pd.DataFrame:
        """Xử lý missing values."""
        if strategy == "drop":
            return df.dropna()
        elif strategy == "fill_none":
            return df.fillna("N/A")
        elif strategy == "fill_zero":
            return df.fillna(0)
        return df

    @staticmethod
    def infer_sqlite_types(df: pd.DataFrame) -> dict[str, str]:
        """Infer SQLite type cho mỗi cột."""
        type_map = {}
        for col in df.columns:
            dtype = df[col].dtype
            if pd.api.types.is_integer_dtype(dtype):
                type_map[col] = "INTEGER"
            elif pd.api.types.is_float_dtype(dtype):
                type_map[col] = "REAL"
            elif pd.api.types.is_bool_dtype(dtype):
                type_map[col] = "INTEGER"
            else:
                type_map[col] = "TEXT"
        return type_map

    def clean(self, df: pd.DataFrame, drop_na: bool = True) -> pd.DataFrame:
        """Full cleaning pipeline."""
        df = self.clean_column_names(df)
        if drop_na:
            df = self.handle_missing_values(df, "drop")
        log.info("cleaning_done", rows=len(df), cols=list(df.columns))
        return df


# ── Data Transformer ─────────────────────────────────────────────────────────
class DataTransformer:
    """Transform data: anonymous, aggregate, reshape."""

    @staticmethod
    def anonymize(df: pd.DataFrame, sensitive_cols: list[str]) -> pd.DataFrame:
        """Anonymize sensitive columns (hash hoặc category)."""
        import hashlib
        df = df.copy()
        for col in sensitive_cols:
            if col in df.columns:
                # Hash để anonymize
                df[col] = df[col].apply(
                    lambda x: hashlib.sha256(str(x).encode()).hexdigest()[:12]
                    if pd.notna(x) else "N/A"
                )
        log.info("anonymized", cols=sensitive_cols)
        return df

    @staticmethod
    def add_derived_columns(df: pd.DataFrame) -> pd.DataFrame:
        """Thêm các computed columns."""
        df = df.copy()

        # Tự động tạo created_at / updated_at nếu chưa có
        if "created_at" not in df.columns:
            df["created_at"] = pd.Timestamp.now()

        return df

    @staticmethod
    def aggregate(df: pd.DataFrame, group_by: str, agg_funcs: dict[str, str]) -> pd.DataFrame:
        """Tạo bảng aggregate từ df."""
        if group_by not in df.columns:
            raise ValueError(f"Column {group_by} not found")

        grouped = df.groupby(group_by).agg(agg_funcs).reset_index()
        log.info("aggregated", original_rows=len(df), aggregated_rows=len(grouped))
        return grouped


# ── Web Crawler ──────────────────────────────────────────────────────────────
class WebCrawler:
    """Crawl bảng từ web pages (CSV, JSON, HTML tables)."""

    def __init__(self, timeout: int = 10):
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (compatible; Text2SQLBot/1.0)"
        })

    def fetch_url(self, url: str) -> str:
        """Fetch HTML content từ URL."""
        resp = self.session.get(url, timeout=self.timeout)
        resp.raise_for_status()
        return resp.text

    def extract_tables_from_html(self, html: str) -> list[pd.DataFrame]:
        """Trích xuất tất cả HTML tables thành DataFrames."""
        soup = BeautifulSoup(html, "lxml")
        tables = []
        for i, table_el in enumerate(soup.find_all("table")):
            try:
                df = pd.read_html(str(table_el))[0]
                tables.append(df)
                log.info("table_extracted", index=i, rows=len(df), cols=list(df.columns[:5]))
            except Exception as e:
                log.warning("table_extract_failed", index=i, error=str(e))
        return tables

    def fetch_csv(self, url: str) -> pd.DataFrame:
        """Fetch và parse CSV."""
        resp = self.session.get(url, timeout=self.timeout)
        resp.raise_for_status()
        from io import StringIO
        df = pd.read_csv(StringIO(resp.text))
        log.info("csv_fetched", url=url, rows=len(df), cols=list(df.columns[:5]))
        return df

    def fetch_json(self, url: str, path: str = None) -> pd.DataFrame:
        """Fetch JSON API và convert thành DataFrame."""
        resp = self.session.get(url, timeout=self.timeout)
        resp.raise_for_status()
        data = resp.json()

        if isinstance(data, list):
            records = data
        elif isinstance(data, dict):
            records = data.get(path, []) if path else list(data.values())
        else:
            records = [data]

        df = pd.DataFrame(records)
        log.info("json_fetched", url=url, rows=len(df))
        return df


# ── Data Pipeline ─────────────────────────────────────────────────────────────
class DataPipeline:
    """
    End-to-end data pipeline:
    Crawl → Clean → Transform → Load (SQLite)
    """

    def __init__(self, db_manager):
        self.db = db_manager
        self.cleaner = DataCleaner()
        self.transformer = DataTransformer()
        self.crawler = WebCrawler()

    def ingest_csv(
        self,
        file_path: str,
        table_name: str,
        drop_na: bool = True,
        anonymize: Optional[list[str]] = None,
    ) -> pd.DataFrame:
        """Ingest CSV file vào SQLite."""
        log.info("ingesting_csv", file=file_path, table=table_name)
        df = pd.read_csv(file_path)
        df = self.cleaner.clean(df, drop_na=drop_na)

        if anonymize:
            df = self.transformer.anonymize(df, anonymize)

        self.db.insert_dataframe(df, table_name)
        log.info("csv_ingested", table=table_name, rows=len(df))
        return df

    def ingest_json(
        self,
        file_path: str,
        table_name: str,
        anonymize: Optional[list[str]] = None,
    ) -> pd.DataFrame:
        """Ingest JSON file vào SQLite."""
        log.info("ingesting_json", file=file_path, table=table_name)
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        if isinstance(data, dict):
            # Lấy first array
            for v in data.values():
                if isinstance(v, list):
                    records = v
                    break
            else:
                records = [data]
        else:
            records = data

        df = pd.DataFrame(records)
        df = self.cleaner.clean(df)

        if anonymize:
            df = self.transformer.anonymize(df, anonymize)

        self.db.insert_dataframe(df, table_name)
        log.info("json_ingested", table=table_name, rows=len(df))
        return df

    def ingest_sample_data(self):
        """
        Tạo sample data để demo hệ thống.
        Tạo các bảng kinh doanh thực tế.
        """
        import pandas as pd

        # Bảng 1: Sản phẩm
        products = pd.DataFrame({
            "product_id": range(1, 21),
            "product_name": [
                "Laptop Dell XPS", "MacBook Pro", "iPhone 15", "Samsung Galaxy S24",
                "iPad Air", "AirPods Pro", "Apple Watch", "Dell Monitor",
                "Keyboard Mechanical", "Mouse Wireless", "Webcam HD", "USB-C Hub",
                "External SSD", "RAM 16GB", "Graphics Card", "Power Bank",
                "Monitor Stand", "Laptop Bag", "Screen Protector", "HDMI Cable"
            ],
            "category": ["Laptop", "Laptop", "Phone", "Phone",
                         "Tablet", "Accessory", "Watch", "Monitor",
                         "Accessory", "Accessory", "Accessory", "Accessory",
                         "Storage", "Component", "Component", "Accessory",
                         "Accessory", "Accessory", "Accessory", "Accessory"],
            "price": [
                25000000, 35000000, 22000000, 20000000,
                15000000, 5500000, 10000000, 8000000,
                2500000, 800000, 1500000, 1200000,
                3000000, 1500000, 12000000, 800000,
                1000000, 500000, 200000, 300000
            ],
            "stock": [15, 8, 25, 30, 12, 50, 20, 10, 40, 60, 35, 45, 20, 30, 5, 55, 25, 40, 100, 80],
            "supplier_id": [1, 2, 2, 3, 2, 2, 2, 1, 4, 4, 5, 4, 6, 7, 8, 4, 4, 9, 9, 4],
        })

        # Bảng 2: Khách hàng
        customers = pd.DataFrame({
            "customer_id": range(1, 16),
            "customer_name": [
                "Nguyễn Văn A", "Trần Thị B", "Lê Văn C", "Phạm Thị D", "Hoàng Văn E",
                "Đặng Thị F", "Bùi Văn G", "Đỗ Thị H", "Ngô Văn I", "Trương Văn J",
                "Vũ Thị K", "Phan Văn L", "Đinh Thị M", "Hà Văn N", "Chu Thị O"
            ],
            "region": ["Hà Nội", "TP.HCM", "Đà Nẵng", "Hà Nội", "TP.HCM",
                       "Hải Phòng", "Cần Thơ", "Huế", "Nha Trang", "Quảng Ninh",
                       "Hà Nội", "TP.HCM", "Bình Dương", "Vinh", "Cần Thơ"],
            "customer_type": ["VIP", "Regular", "VIP", "Regular", "VIP",
                             "Regular", "Regular", "VIP", "Regular", "Regular",
                             "VIP", "Regular", "Regular", "VIP", "Regular"],
            "registration_date": pd.date_range("2022-01-01", periods=15, freq="ME").strftime("%Y-%m-%d").tolist(),
        })

        # Bảng 3: Đơn hàng
        import random
        random.seed(42)
        orders = []
        for i in range(1, 51):
            orders.append({
                "order_id": i,
                "customer_id": random.randint(1, 15),
                "product_id": random.randint(1, 20),
                "quantity": random.randint(1, 5),
                "order_date": pd.Timestamp("2024-01-01") + pd.Timedelta(days=random.randint(0, 365)),
                "status": random.choice(["completed", "completed", "completed", "pending", "cancelled"]),
            })
        orders_df = pd.DataFrame(orders)
        orders_df["order_date"] = orders_df["order_date"].dt.strftime("%Y-%m-%d")

        # Bảng 4: Nhà cung cấp
        suppliers = pd.DataFrame({
            "supplier_id": range(1, 10),
            "supplier_name": [
                "TechCorp VN", "Apple Authorized", "Samsung VN", "TechWorld",
                "CamStore", "StoragePro", "RAMMaster", "GPUExperts", "BagPlus"
            ],
            "contact_email": [f"contact{i}@supplier.com" for i in range(1, 10)],
            "rating": [4.5, 5.0, 4.8, 4.2, 3.9, 4.6, 4.3, 4.7, 3.8],
        })

        # Bảng 5: Review sản phẩm
        reviews = []
        for i in range(1, 31):
            reviews.append({
                "review_id": i,
                "product_id": random.randint(1, 20),
                "customer_id": random.randint(1, 15),
                "rating": random.randint(1, 5),
                "comment": random.choice([
                    "Sản phẩm tốt, đáng mua", "Chất lượng tuyệt vời", "Giao hàng nhanh",
                    "Đóng gói cẩn thận", "Hàng chính hãng", "Giá hợp lý",
                    "Ổn trong tầm giá", "Nên mua", "Tạm được", "Rất hài lòng"
                ]),
                "review_date": pd.Timestamp("2024-01-01") + pd.Timedelta(days=random.randint(0, 365)),
            })
        reviews_df = pd.DataFrame(reviews)
        reviews_df["review_date"] = reviews_df["review_date"].dt.strftime("%Y-%m-%d")

        # Insert all
        self.db.insert_dataframe(products, "products")
        self.db.insert_dataframe(customers, "customers")
        self.db.insert_dataframe(orders_df, "orders")
        self.db.insert_dataframe(suppliers, "suppliers")
        self.db.insert_dataframe(reviews_df, "reviews")

        log.info("sample_data_loaded",
                 tables=["products", "customers", "orders", "suppliers", "reviews"],
                 total_rows=len(products) + len(customers) + len(orders_df) + len(suppliers) + len(reviews_df))
