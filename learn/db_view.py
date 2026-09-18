#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
学途 Lite —— 数据库只读查看器（给不会 SQL 的人用的）

【为什么需要它】
项目用的是 SQLite，数据库就是一个文件：
    data/processed/xuetu_lite.db
不像 MySQL 那样要先启动服务、输密码，用任何能读 SQLite 的工具都能直接打开。
这台机器上没装 DB Browser / sqlite3 命令行，所以写了这个脚本，双击或命令行都能看。

【重要：只读】
本脚本全程用 mode=ro（read-only）打开，SQLite 会拒绝一切写入，
你不用担心把项目正在用的数据改坏。

【怎么用】
    python learn/db_view.py                 # 总览：列出所有表和行数
    python learn/db_view.py schema video    # 看 video 表的字段结构
    python learn/db_view.py show video      # 预览 video 表前 20 行
    python learn/db_view.py show video 50   # 预览前 50 行
    python learn/db_view.py sql "SELECT * FROM student LIMIT 5"
    python learn/db_view.py csv course      # 导出 course 表到 CSV（桌面）

【路径说明】
数据库路径不是写死的常量，而是「由本文件位置往上推一层」算出来的，
和 backend/ 里 6 个模块的写法完全一致（见下方 ROOT / DB_PATH）。
所以整个项目目录随便搬到哪个盘都能跑。
"""

import csv
import os
import sqlite3
import sys
from pathlib import Path

# ---- 路径推导：和 backend/*.py 顶部完全一样的写法 ------------------------
# parents[0] = learn/   parents[1] = 项目根目录
ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "processed" / "xuetu_lite.db"


def connect_readonly() -> sqlite3.Connection:
    """以只读方式打开数据库。

    关键点：
    1. URI 形式 file:...?mode=ro —— SQLite 原生支持的只读开关，
       任何 INSERT/UPDATE/DELETE 都会被直接拒绝，比"我自己小心点"靠谱。
    2. 路径里的反斜杠要换成正斜杠，盘符前再加一个 /，
       否则 Windows 的 E:\\xxx 会被 SQLite 当成 URI 的参数分隔符解析错。
    3. row_factory = sqlite3.Row —— 让查询结果能按【列名】取值，
       而不是只能 row[0]、row[1] 这种靠位置取，可读性天差地别。
    """
    if not DB_PATH.exists():
        sys.exit(f"找不到数据库文件：{DB_PATH}\n请确认项目目录完整。")

    uri = "file:///" + str(DB_PATH).replace("\\", "/") + "?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    con.row_factory = sqlite3.Row  # 结果可以按列名取：row["title"]
    return con


def list_tables(con: sqlite3.Connection) -> None:
    """总览：每张表有多少行、多少列。"""
    print(f"\n数据库：{DB_PATH}")
    print(f"大小  ：{DB_PATH.stat().st_size / 1024:.1f} KB\n")

    tables = [
        r[0]
        for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    ]

    print(f"{'表名':<22}{'行数':>8}   字段")
    print("-" * 78)
    for t in tables:
        # 表名不能参数化（SQL 不支持 ? 做标识符），这里用双引号包起来防注入
        n = con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
        cols = [c[1] for c in con.execute(f'PRAGMA table_info("{t}")')]
        preview = ", ".join(cols[:5]) + ("..." if len(cols) > 5 else "")
        print(f"{t:<22}{n:>8}   {preview}")

    print("-" * 78)
    print(f"共 {len(tables)} 张表。想看哪张：python learn/db_view.py show 表名")


def show_schema(con: sqlite3.Connection, table: str) -> None:
    """看某张表的字段定义：字段名、类型、能否为空、默认值、是否主键。"""
    info = list(con.execute(f'PRAGMA table_info("{table}")'))
    if not info:
        sys.exit(f"没有这张表：{table}")

    print(f"\n【{table}】表结构　共 {len(info)} 个字段\n")
    print(f"{'#':<4}{'字段名':<24}{'类型':<12}{'非空':<6}{'主键':<6}默认值")
    print("-" * 74)
    for c in info:
        print(
            f"{c[0]:<4}{c[1]:<24}{str(c[2] or ''):<12}"
            f"{'是' if c[3] else '':<6}{'★' if c[5] else '':<6}{c[4] or ''}"
        )

    # 建表 SQL 原文，最能说明这张表到底是怎么建的
    row = con.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    if row and row["sql"]:
        print(f"\n建表语句原文：\n{row['sql']}")


def show_rows(con: sqlite3.Connection, table: str, limit: int = 20) -> None:
    """预览数据。用 LIMIT 限制条数，避免几万行刷屏。"""
    try:
        rows = con.execute(f'SELECT * FROM "{table}" LIMIT ?', (limit,)).fetchall()
    except sqlite3.Error as e:
        sys.exit(f"查询失败：{e}")

    if not rows:
        print(f"\n【{table}】表里没有数据。")
        return

    cols = rows[0].keys()
    # 每列按内容宽度自适应，太长的截断
    widths = []
    for c in cols:
        w = max(len(str(c)), *(len(str(r[c])) for r in rows))
        widths.append(min(w, 26))

    print(f"\n【{table}】前 {len(rows)} 行\n")
    print(" | ".join(str(c)[:26].ljust(w) for c, w in zip(cols, widths)))
    print("-" * (sum(widths) + 3 * (len(cols) - 1)))
    for r in rows:
        cells = []
        for c, w in zip(cols, widths):
            v = str(r[c])
            v = v if len(v) <= 26 else v[:23] + "..."
            cells.append(v.ljust(w))
        print(" | ".join(cells))


def run_sql(con: sqlite3.Connection, sql: str) -> None:
    """执行自定义 SQL。只读连接会自动拦掉写操作。"""
    try:
        rows = con.execute(sql).fetchall()
    except sqlite3.Error as e:
        # 只读库上做写操作会走到这里，给出人话提示
        if "readonly" in str(e).lower():
            sys.exit("这是只读连接，不能改数据。本工具只负责看。")
        sys.exit(f"SQL 报错：{e}")

    if not rows:
        print("\n（查询成功，但没有返回任何行）")
        return

    cols = rows[0].keys()
    print(f"\n返回 {len(rows)} 行：\n")
    print(" | ".join(cols))
    print("-" * 60)
    for r in rows:
        print(" | ".join(str(r[c]) if r[c] is not None else "NULL" for c in cols))


def export_csv(con: sqlite3.Connection, table: str) -> None:
    """导出成 CSV，可以用 Excel / WPS 直接打开看。"""
    desktop = Path.home() / "Desktop"
    desktop = desktop if desktop.exists() else Path.cwd()
    out = desktop / f"{table}.csv"

    rows = con.execute(f'SELECT * FROM "{table}"').fetchall()
    if not rows:
        sys.exit(f"【{table}】没有数据可导出。")

    with open(out, "w", newline="", encoding="utf-8-sig") as f:
        # utf-8-sig 带 BOM，Excel 打开中文才不乱码
        w = csv.DictWriter(f, fieldnames=rows[0].keys())
        w.writeheader()
        for r in rows:
            w.writerow(dict(r))

    print(f"已导出 {len(rows)} 行到：{out}")


def main() -> None:
    args = sys.argv[1:]
    con = connect_readonly()

    if not args:
        list_tables(con)
    elif args[0] == "schema" and len(args) >= 2:
        show_schema(con, args[1])
    elif args[0] == "show" and len(args) >= 2:
        limit = int(args[2]) if len(args) >= 3 else 20
        show_rows(con, args[1], limit)
    elif args[0] == "sql" and len(args) >= 2:
        run_sql(con, " ".join(args[1:]))
    elif args[0] == "csv" and len(args) >= 2:
        export_csv(con, args[1])
    else:
        print(__doc__)

    con.close()


if __name__ == "__main__":
    main()
