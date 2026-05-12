"""
DB CLI Utilities — multi-database management cho CLI.
"""
import sys
import os
from pathlib import Path

# Force UTF-8 on Windows
if os.name == "nt":
    sys.stdout = __import__("io").TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace"
    )
    sys.stderr = __import__("io").TextIOWrapper(
        sys.stderr.buffer, encoding="utf-8", errors="replace"
    )

import argparse


def onboard_db_cmd(args):
    """Onboard a new database."""
    from src.agents.onboard import onboard_db
    onboard_db(args.path)


def list_cmd(args):
    """List all onboarded databases."""
    from src.agents.onboard import list_databases
    list_databases()


def switch_cmd(args):
    """Switch active database (update .env or just print info)."""
    path = Path(args.path).resolve()
    if not path.exists():
        print(f"  ❌ File not found: {path}")
        return

    # Update .env
    env_path = Path(__file__).parent.parent.parent / ".env"
    if env_path.exists():
        with open(env_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    else:
        lines = []

    # Replace DB_PATH line
    new_lines = []
    replaced = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("DB_PATH=") or stripped.startswith('DB_PATH="'):
            new_lines.append(f'DB_PATH="{path}"\n')
            replaced = True
        else:
            new_lines.append(line)

    if not replaced:
        new_lines.append(f'DB_PATH="{path}"\n')

    with open(env_path, "w", encoding="utf-8") as f:
        f.writelines(new_lines)

    print(f"  ✅ Đã đổi DB_PATH → {path}")
    print(f"     (Sửa trong {env_path})")
    print()
    print(f"  Tiếp theo chạy onboarding để tạo semantic cache:")
    print(f"    python -c \"from src.agents.onboard import onboard_db; onboard_db('{path}')\"")


def main():
    parser = argparse.ArgumentParser(description="Multi-DB CLI utilities")
    sub = parser.add_subparsers(dest="cmd")

    p_onboard = sub.add_parser("onboard", help="Onboard a new SQLite database")
    p_onboard.add_argument("path", help="Path to SQLite file")
    p_onboard.set_defaults(fn=onboard_db_cmd)

    p_list = sub.add_parser("list", help="List all onboarded databases")
    p_list.set_defaults(fn=list_cmd)

    p_switch = sub.add_parser("switch", help="Switch active database")
    p_switch.add_argument("path", help="Path to SQLite file")
    p_switch.set_defaults(fn=switch_cmd)

    args = parser.parse_args()

    if args.cmd is None:
        parser.print_help()
        return

    args.fn(args)


if __name__ == "__main__":
    main()
