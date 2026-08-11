"""
Data Visualization — tạo charts từ query results.
Hỗ trợ: bar, line, pie, table (ASCII).
"""
import io
import base64
from typing import Optional

import structlog

log = structlog.get_logger("visualizer")

# Lazy import matplotlib
_mpl_available = False
try:
    import matplotlib
    matplotlib.use("Agg")  # Non-interactive backend
    import matplotlib.pyplot as plt
    import matplotlib.font_manager as fm
    import numpy as np
    _mpl_available = True
except ImportError:
    pass


def get_font_path() -> Optional[str]:
    """Tìm font hỗ trợ tiếng Việt."""
    if not _mpl_available:
        return None
    # Thử tìm các font phổ biến
    font_paths = [
        # Windows
        "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/times.ttf",
        # Linux
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    ]
    for fp in font_paths:
        try:
            return fp
        except Exception:
            pass
    return None


def suggest_chart_type(columns: list[str], rows: list[dict]) -> str:
    """Gợi ý loại chart dựa trên data."""
    if not rows:
        return "none"

    # Lấy 2 cột đầu tiên làm x/y
    if len(columns) == 1:
        return "bar"

    numeric_cols = []
    for col in columns:
        for row in rows[:3]:
            try:
                float(row.get(col, ""))
                numeric_cols.append(col)
                break
            except (ValueError, TypeError):
                continue

    if len(numeric_cols) >= 1:
        return "bar"
    return "table"


def plot_chart(
    columns: list[str],
    rows: list[dict],
    chart_type: str = "bar",
    title: str = "Data Visualization",
) -> Optional[str]:
    """
    Tạo chart và trả về base64 encoded image.
    Trả về None nếu matplotlib không khả dụng.
    """
    if not _mpl_available or not rows:
        return None

    try:
        import matplotlib.pyplot as plt

        # Prepare data
        if len(columns) >= 2:
            x_col = columns[0]
            y_col = columns[1]
        elif len(columns) == 1:
            x_col = columns[0]
            y_col = None
        else:
            return None

        x_values = [str(row.get(x_col, "")) for row in rows]
        y_values = None
        if y_col:
            y_values = []
            for row in rows:
                try:
                    y_values.append(float(row.get(y_col, 0)))
                except (ValueError, TypeError):
                    y_values.append(0)

        fig, ax = plt.subplots(figsize=(10, 5))

        if chart_type == "bar":
            x_pos = np.arange(len(x_values))
            bars = ax.bar(x_pos, y_values or [1] * len(x_values), color="steelblue", alpha=0.8)
            ax.set_xticks(x_pos)
            ax.set_xticklabels(x_values, rotation=45, ha="right", fontsize=8)
            if y_col:
                ax.set_ylabel(y_col)
            ax.set_title(title, fontsize=12, fontweight="bold")
            ax.grid(axis="y", alpha=0.3)

        elif chart_type == "line":
            x_pos = np.arange(len(x_values))
            ax.plot(x_pos, y_values or [1] * len(x_values), marker="o", color="steelblue", linewidth=2)
            ax.set_xticks(x_pos)
            ax.set_xticklabels(x_values, rotation=45, ha="right", fontsize=8)
            if y_col:
                ax.set_ylabel(y_col)
            ax.set_title(title, fontsize=12, fontweight="bold")
            ax.grid(alpha=0.3)

        elif chart_type == "pie":
            if y_values:
                ax.pie(y_values, labels=x_values, autopct="%1.1f%%", startangle=90)
                ax.set_title(title, fontsize=12, fontweight="bold")

        else:
            # Table as text
            ax.axis("off")
            ax.set_title(title, fontsize=12, fontweight="bold")
            return None

        plt.tight_layout()

        buf = io.BytesIO()
        plt.savefig(buf, format="png", dpi=100, bbox_inches="tight")
        plt.close(fig)
        buf.seek(0)

        return base64.b64encode(buf.read()).decode("utf-8")

    except Exception as e:
        log.warning("chart_creation_failed", error=str(e))
        return None


def render_table_ascii(columns: list[str], rows: list[dict], max_rows: int = 20) -> str:
    """Render bảng dưới dạng ASCII art."""
    if not columns or not rows:
        return "No data to display."

    display_rows = rows[:max_rows]

    # Calculate column widths
    col_widths = {col: len(str(col)) for col in columns}
    for row in display_rows:
        for col in columns:
            val_len = len(str(row.get(col, "")))
            col_widths[col] = max(col_widths[col], val_len)

    # Cap width
    for col in col_widths:
        col_widths[col] = min(col_widths[col], 30)

    # Header
    header = " | ".join(str(col)[:col_widths[col]].ljust(col_widths[col]) for col in columns)
    separator = "-+-".join("-" * col_widths[col] for col in columns)

    # Rows
    lines = [header, separator]
    for row in display_rows:
        line = " | ".join(
            str(row.get(col, ""))[:col_widths[col]].ljust(col_widths[col])
            for col in columns
        )
        lines.append(line)

    if len(rows) > max_rows:
        lines.append(f"... ({len(rows) - max_rows} more rows)")

    return "\n".join(lines)
