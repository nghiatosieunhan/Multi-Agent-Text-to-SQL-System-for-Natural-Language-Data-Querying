from src.agents.table_selector import select_tables_for_query
from src.db import get_db_manager
from src.rag.schema_indexer import _get_semantic_descriptions

db = get_db_manager("data/chinook/Chinook_VN.sqlite")
semantic = _get_semantic_descriptions("data/chinook/Chinook_VN.sqlite")
tables = select_tables_for_query("Số lượng bài hát thể loại Metal", db, semantic)
print("SELECTED TABLES:")
print(tables)
