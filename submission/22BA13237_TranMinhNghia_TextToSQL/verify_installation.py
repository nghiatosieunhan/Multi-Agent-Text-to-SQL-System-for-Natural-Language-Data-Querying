from pathlib import Path

from src.agents.validator import hard_validate
from src.db import get_db_manager


def main() -> None:
    db_path = Path(__file__).parent / "data" / "Chinook_VN.sqlite"
    if not db_path.is_file() or db_path.stat().st_size == 0:
        raise SystemExit(f"Sample database is missing or empty: {db_path}")

    db = get_db_manager(str(db_path))
    schema = db.get_schema()
    if len(schema.tables) != 11:
        raise SystemExit(f"Expected 11 tables, found {len(schema.tables)}")

    sql = "SELECT COUNT(*) AS ArtistCount FROM NgheSi"
    validation = hard_validate(sql, db)
    if not validation["valid"]:
        raise SystemExit(f"Validation failed: {validation['issues']}")

    result = db.execute_query(sql)
    if result.error or result.row_count != 1:
        raise SystemExit(f"Execution failed: {result.error}")

    print(f"Schema tables: {len(schema.tables)}")
    print(f"Read-only query result: {result.rows[0]}")
    print("INSTALLATION CHECK PASSED")


if __name__ == "__main__":
    main()
