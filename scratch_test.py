import sys
import io
import os
from src.graph import run_query

if os.name == "nt":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

os.environ["LLM_PROVIDER"] = "google"
os.environ["LLM_MODEL"] = "gemini-2.5-flash"

question = "Vui lòng cung cấp báo cáo tổng doanh thu theo từng danh mục sản phẩm."
db_path = "data/northwind/northwind.sqlite"

print("Running query...")
res = run_query(question, db_path=db_path)
print("SQL:", res.generated_sql)
print("Error:", res.error)
print("Exec Error:", res.execution_error)
print("Rows:", res.query_result)
