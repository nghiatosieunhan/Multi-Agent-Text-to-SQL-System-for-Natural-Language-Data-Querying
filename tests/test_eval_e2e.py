from src.graph import run_query

q = "Số lượng bài hát thể loại Metal"
res = run_query(q, db_path="data/chinook/Chinook_VN.sqlite", dataset_type="chinook_vn")
print("SQL:", res.generated_sql)
print("ERROR:", res.execution_error or res.error)
print("ROW COUNT:", res.query_result.get("row_count") if res.query_result else 0)
