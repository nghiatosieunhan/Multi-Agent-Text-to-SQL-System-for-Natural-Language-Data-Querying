import os
import json
import glob
import shutil

history_dirs = [
    r"C:\Users\nghia\AppData\Roaming\Code\User\History",
    r"C:\Users\nghia\AppData\Roaming\Cursor\User\History"
]
target_dir = r"i:\AI\text_to_sql\src\agents"

files_to_restore = [
    "auto_fewshot.py", "column_pruner.py", "executor.py", "gemini_llm.py", 
    "onboard.py", "orchestrator.py", "query_planner.py", "result_formatter.py", 
    "route_node.py", "route_note.py", "table_selector.py", "__init__.py"
]

found_versions = {f: [] for f in files_to_restore}

for history_dir in history_dirs:
    for entry_file in glob.glob(os.path.join(history_dir, "*", "entries.json")):
        with open(entry_file, 'r', encoding='utf-8') as f:
            try:
                data = json.load(f)
                res = data.get("resource", "")
                if "src/agents" in res:
                    filename = res.split("/")[-1]
                    if filename in files_to_restore:
                        entries = data.get("entries", [])
                        if entries:
                            latest_entry = max(entries, key=lambda x: x.get("timestamp", 0))
                            latest_file_path = os.path.join(os.path.dirname(entry_file), latest_entry["id"])
                            found_versions[filename].append((latest_entry["timestamp"], latest_file_path))
            except Exception:
                pass

for fname, versions in found_versions.items():
    if versions:
        versions.sort(key=lambda x: x[0], reverse=True)
        best_file = versions[0][1]
        dest_fname = fname
        if fname == "route_note.py":
            dest_fname = "route_node.py"
        dest_path = os.path.join(target_dir, dest_fname)
        shutil.copy2(best_file, dest_path)
        print(f"Restored {dest_fname} from {best_file}")
    else:
        print(f"COULD NOT FIND BACKUP FOR {fname}")

for f in glob.glob(os.path.join(target_dir, "*.py")):
    if f.endswith("groq_llm.py") or f.endswith("gemini_llm.py"):
        continue
    with open(f, 'r', encoding='utf-8') as file:
        content = file.read()
    new_content = content.replace("gemini_llm", "groq_llm")
    if new_content != content:
        with open(f, 'w', encoding='utf-8') as file:
            file.write(new_content)
