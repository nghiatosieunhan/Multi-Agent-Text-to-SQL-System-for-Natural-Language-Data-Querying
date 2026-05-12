import dis
import marshal
import os

files = ["auto_fewshot", "column_pruner", "executor", "onboard", "table_selector", "route_node"]
os.makedirs("scratch", exist_ok=True)

def dump_code(code, out, level=0):
    indent = "  " * level
    out.write(f"\n{indent}--- Code: {getattr(code, 'co_name', 'unknown')} ---\n")
    dis.dis(code, file=out)
    out.write(f"\n{indent}CONSTS:\n")
    for c in code.co_consts:
        out.write(f"{indent}{repr(c)}\n")
        if hasattr(c, 'co_code'):
            dump_code(c, out, level+1)

for f in files:
    try:
        with open(f"src/agents/__pycache__/{f}.cpython-311.pyc", "rb") as pyc:
            pyc.read(16) # Skip magic and timestamp
            code = marshal.load(pyc)
            with open(f"scratch/{f}_dis.txt", "w", encoding="utf-8") as out:
                dump_code(code, out)
    except Exception as e:
        print(f"Failed {f}: {e}")
