import sys
import asyncio
sys.path.append('.')
from src.graph import arun_query

async def main():
    try:
        res = await arun_query("Vui lòng cung cấp báo cáo tổng doanh thu theo từng danh mục sản phẩm.",
            db_path="data/northwind/northwind.sqlite",
            dataset_type="northwind",
            analysis_mode="deep",
            evaluation_profile="full_no_cache"
        )
        print("Success:", res)
    except Exception as e:
        import traceback
        traceback.print_exc()

asyncio.run(main())
