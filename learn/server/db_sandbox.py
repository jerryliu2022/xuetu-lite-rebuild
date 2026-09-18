# -*- coding: utf-8 -*-
"""
数据库沙箱：给学习者准备一份结构与正式库 1:1 相同、但可以随便折腾的练习库。

为什么要用副本？
    正式库 data/processed/xuetu_lite.db 是正在运行的项目的数据源。
    如果直接在上面练习 INSERT / UPDATE / DELETE，会把演示数据搞乱。
    所以这里用 SQLite 官方的 backup API 完整复制一份到 learn/data/learn_test.db，
    表结构、索引、数据全都一样，学习者可以在副本上放心破坏，随时点「重置」再复制回来。

安全边界：
    - 只允许数据操作类语句（SELECT / INSERT / UPDATE / DELETE / REPLACE）
    - 屏蔽 DROP / ALTER / ATTACH 等会破坏结构或跨库访问的语句
    - PRAGMA 只允许查询元信息的几个子命令
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

# ---------------------------------------------------------------- 路径常量
# learn/server/db_sandbox.py -> parents[0]=server, parents[1]=learn, parents[2]=项目根
PROJECT_ROOT = Path(__file__).resolve().parents[2]
LEARN_ROOT = PROJECT_ROOT / "learn"

# 正式库：只读，绝不写入
MAIN_DB = PROJECT_ROOT / "data" / "processed" / "xuetu_lite.db"
# 练习库：可读写，随时可以重置
TEST_DB = LEARN_ROOT / "data" / "learn_test.db"

# 允许在练习库上执行的 SQL 首关键字
ALLOWED_KEYWORDS = {
    "SELECT",
    "INSERT",
    "UPDATE",
    "DELETE",
    "REPLACE",
    "WITH",
    "EXPLAIN",
}

# 只允许查询元信息的 PRAGMA 子命令（避免 writable_schema 之类的危险开关）
ALLOWED_PRAGMAS = {
    "table_info",
    "table_list",
    "index_list",
    "index_info",
    "foreign_key_list",
    "database_list",
}


def ensure_test_db(force: bool = False) -> Dict[str, Any]:
    """确保练习库存在；不存在或 force=True 时就从正式库完整复制一份。

    SQLite 的 Connection.backup() 是官方提供的在线备份接口，
    它会把整库（表、索引、数据、触发器）一次性复制过去，比手工建表更可靠，
    也天然保证了「练习库结构 == 正式库结构」。
    """
    TEST_DB.parent.mkdir(parents=True, exist_ok=True)
    existed = TEST_DB.exists()
    if existed and not force:
        return {"ok": True, "action": "reused", "path": str(TEST_DB)}

    if not MAIN_DB.exists():
        return {"ok": False, "action": "failed", "reason": f"正式库不存在：{MAIN_DB}"}

    # 用只读模式打开正式库，双保险：任何写操作都会在 SQL 层被拒绝
    src = sqlite3.connect(f"file:{MAIN_DB}?mode=ro", uri=True)
    dst = sqlite3.connect(str(TEST_DB))
    try:
        src.backup(dst)  # 关键一行：整库复制
        dst.commit()
        action = "reset" if existed else "created"
        return {"ok": True, "action": action, "path": str(TEST_DB)}
    finally:
        dst.close()
        src.close()


def table_stats(con: sqlite3.Connection) -> List[Dict[str, Any]]:
    """列出练习库里所有表及其行数，方便页面展示规模。"""
    out: List[Dict[str, Any]] = []
    tables = con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    for (name,) in tables:
        count = con.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
        cols = [row[1] for row in con.execute(f'PRAGMA table_info("{name}")')]
        out.append({"table": name, "rows": count, "columns": cols})
    return out


def split_statements(sql: str) -> List[str]:
    """把可能包含多条语句的输入按分号切开。

    注意：这里要跳过字符串字面量里的分号，否则 'a;b' 这种值会被误切成两句。
    """
    statements: List[str] = []
    buf = ""
    in_string = False
    for ch in sql:
        buf += ch
        if ch == "'":
            in_string = not in_string
        elif ch == ";" and not in_string:
            piece = buf.strip()
            if piece:
                statements.append(piece)
            buf = ""
    tail = buf.strip()
    if tail:
        statements.append(tail)
    return statements


def check_statement(stmt: str) -> None:
    """白名单校验，不合规就抛异常，由上层转成友好提示。"""
    body = strip_comments(stmt)
    if not body:
        raise ValueError("空语句")
    head = body.split()[0].upper()

    if head == "PRAGMA":
        sub = body.split()[1].split("(")[0].lower() if len(body.split()) > 1 else ""
        if sub not in ALLOWED_PRAGMAS:
            raise ValueError(
                f"PRAGMA {sub or '(空)'} 不允许执行。"
                f"本沙箱只开放：{', '.join(sorted(ALLOWED_PRAGMAS))}"
            )
        return

    if head not in ALLOWED_KEYWORDS:
        raise ValueError(
            f"「{head}」语句不允许在练习库执行。"
            f"允许：{', '.join(sorted(ALLOWED_KEYWORDS))}。"
            "如果想清库重来，请点页面上的「重置练习库」按钮。"
        )


def strip_comments(stmt: str) -> str:
    """去掉行注释，避免 -- xxx 开头影响首关键字判断。"""
    lines = []
    for line in stmt.splitlines():
        line = line.strip()
        if line.startswith("--"):
            continue
        lines.append(line)
    return " ".join(lines).strip()


def execute_sql(sql: str) -> Dict[str, Any]:
    """在练习库上执行一组 SQL（支持多条语句），返回结果或错误信息。"""
    ensure_test_db()
    started = time.time()
    statements = split_statements(sql)

    con = sqlite3.connect(str(TEST_DB))
    con.row_factory = sqlite3.Row
    result: Dict[str, Any] = {
        "ok": True,
        "statements": len(statements),
        "elapsed_ms": 0,
        "changes": [],
        "columns": [],
        "rows": [],
        "rowcount": 0,
        "message": "",
    }
    try:
        # 白名单校验必须放在 try 里面：被拦截的语句也要以「友好错误」返回，
        # 而不是让 ValueError 直接冒泡成 500。
        for stmt in statements:
            check_statement(stmt)

        for stmt in statements:
            cur = con.execute(stmt)
            head = stmt.split()[0].upper()
            if cur.description is not None:
                # 有结果集（SELECT / PRAGMA / INSERT RETURNING 等）
                result["columns"] = [d[0] for d in cur.description]
                result["rows"] = [dict(r) for r in cur.fetchall()]
                result["rowcount"] = len(result["rows"])
                result["changes"].append(f"查询返回 {len(result['rows'])} 行")
            else:
                affected = cur.rowcount if cur.rowcount is not None and cur.rowcount >= 0 else 0
                result["rowcount"] = affected
                if head == "INSERT" or head == "REPLACE":
                    result["changes"].append(f"插入 {affected} 行")
                elif head == "UPDATE":
                    result["changes"].append(f"更新 {affected} 行")
                elif head == "DELETE":
                    result["changes"].append(f"删除 {affected} 行")
                else:
                    result["changes"].append(f"{head} 执行完成")
        con.commit()
        result["message"] = "；".join(result["changes"])
    except Exception as exc:  # noqa: BLE001 - 学习场景需要把异常原文回显给学习者
        con.rollback()
        result.update(
            {
                "ok": False,
                "message": f"{type(exc).__name__}: {exc}",
                "rows": [],
                "columns": [],
                "rowcount": 0,
            }
        )
    finally:
        con.close()

    result["elapsed_ms"] = round((time.time() - started) * 1000, 2)
    return result


def describe_schema() -> Dict[str, Any]:
    """返回练习库的表清单（供页面「看表结构」用）。"""
    ensure_test_db()
    con = sqlite3.connect(str(TEST_DB))
    try:
        return {"ok": True, "tables": table_stats(con)}
    finally:
        con.close()


# ---------------------------------------------------------------- 预置 SQL 练习
# 每条都带中文注释，直接对应知识点；页面上一键执行
PRESET_SQL: List[Dict[str, str]] = [
    {
        "id": "select-all",
        "level": "入门",
        "title": "SELECT：把表里的数据取出来",
        "desc": "最基础的一条查询。* 表示「所有列」，FROM 指定从哪张表取。",
        "source": "backend/recommender.py:216 SELECT * FROM video",
        "sql": (
            "-- SELECT 列 FROM 表；* 代表全部列，只取前 5 条看看长什么样\n"
            "SELECT * FROM video LIMIT 5;"
        ),
    },
    {
        "id": "select-columns",
        "level": "入门",
        "title": "SELECT 指定列 + WHERE 过滤",
        "desc": "不要 SELECT *，按需取列更快也更清晰；WHERE 用来筛选满足条件的行。",
        "source": "backend/recommender.py:259 SELECT * FROM student WHERE student_id=?",
        "sql": (
            "-- 只取课程编号、标题、平台、热度四列，并且只看免费的(is_paid=0)\n"
            "SELECT video_id, title, platform, popularity\n"
            "FROM video\n"
            "WHERE is_paid = 0\n"
            "LIMIT 8;"
        ),
    },
    {
        "id": "order-limit",
        "level": "入门",
        "title": "ORDER BY 排序 + LIMIT 取前 N 条",
        "desc": "推荐系统里「热门榜」就是这么来的：按热度倒序取前几条。",
        "source": "backend/recommender.py:114-121 ORDER BY popularity DESC LIMIT 16",
        "sql": (
            "-- DESC 表示从大到小（降序），ASC 或不写是升序\n"
            "SELECT video_id, title, popularity, rating\n"
            "FROM video\n"
            "ORDER BY popularity DESC\n"
            "LIMIT 10;"
        ),
    },
    {
        "id": "join",
        "level": "进阶",
        "title": "JOIN：把两张表按关联键拼起来",
        "desc": "video 表只有 course_id，想知道课程名就得 JOIN course 表。这是关系型数据库最核心的操作之一。",
        "source": "backend/student_profiles.py:305-309 FROM learning_record l JOIN video v ON v.video_id=l.video_id",
        "sql": (
            "-- v 和 c 是给表起的「别名」，写起来更短\n"
            "-- ON 后面是拼接条件：视频的课程编号 = 课程的编号\n"
            "SELECT v.video_id, v.title, c.name AS course_name, c.semester\n"
            "FROM video v\n"
            "JOIN course c ON v.course_id = c.course_id\n"
            "ORDER BY c.semester\n"
            "LIMIT 12;"
        ),
    },
    {
        "id": "group-by",
        "level": "进阶",
        "title": "GROUP BY 分组统计",
        "desc": "想知道每个平台各有多少门课？分组 + 计数。COUNT(*) 统计每组的行数。",
        "source": "backend/train_model.py:129-136 SELECT v.platform, COUNT(*) AS c ... GROUP BY v.platform",
        "sql": (
            "-- GROUP BY 会把相同 platform 的行归到一组，然后对每组做 COUNT\n"
            "SELECT platform,\n"
            "       COUNT(*) AS course_count,\n"
            "       ROUND(AVG(rating), 2) AS avg_rating\n"
            "FROM video\n"
            "GROUP BY platform\n"
            "ORDER BY course_count DESC;"
        ),
    },
    {
        "id": "group-having",
        "level": "进阶",
        "title": "HAVING：对分组后的结果再过滤",
        "desc": "WHERE 在分组前过滤行，HAVING 在分组后过滤「组」。两者不能混为一谈。",
        "source": "知识点延伸：项目用 Python 侧过滤，这里演示 SQL 原生写法",
        "sql": (
            "-- 只保留课程数 >= 3 的平台\n"
            "SELECT platform, COUNT(*) AS cnt\n"
            "FROM video\n"
            "GROUP BY platform\n"
            "HAVING COUNT(*) >= 3\n"
            "ORDER BY cnt DESC;"
        ),
    },
    {
        "id": "subquery",
        "level": "进阶",
        "title": "子查询：用一次查询的结果当条件",
        "desc": "括号里先算出平均热度，外层再筛出高于平均的视频。",
        "source": "知识点延伸",
        "sql": (
            "-- 内层 (SELECT AVG(popularity) FROM video) 先算出一个数，外层拿它当门槛\n"
            "SELECT video_id, title, popularity\n"
            "FROM video\n"
            "WHERE popularity > (SELECT AVG(popularity) FROM video)\n"
            "ORDER BY popularity DESC\n"
            "LIMIT 10;"
        ),
    },
    {
        "id": "like",
        "level": "进阶",
        "title": "LIKE 模糊匹配字符串",
        "desc": "% 代表任意长度字符。项目里用它做「标题包含关键词」的召回。",
        "source": "backend/recommender.py:404 SELECT * FROM video WHERE title LIKE ? OR tags LIKE ?",
        "sql": (
            "-- LIKE '%数据结构%' 意思是「只要 title 里出现过『数据结构』四个字」\n"
            "SELECT video_id, title, tags\n"
            "FROM video\n"
            "WHERE title LIKE '%数据结构%' OR tags LIKE '%数据结构%'\n"
            "LIMIT 8;"
        ),
    },
    {
        "id": "insert",
        "level": "写入",
        "title": "INSERT：插入一条新数据",
        "desc": "往 student 表里加一个练习用的学生。注意这是练习库，随便写。",
        "source": "backend/data_acquisition.py:276 INSERT INTO student VALUES (...)",
        "sql": (
            "-- INSERT INTO 表 (列1, 列2...) VALUES (值1, 值2...)\n"
            "-- 列名和值要一一对应；字符串用单引号\n"
            "INSERT INTO student\n"
            "  (student_id, name, school, major, grade, semester, level, xp, next_xp)\n"
            "VALUES\n"
            "  ('20999999', '练习同学', '江城大学', '计算机科学与技术', '大一', 1, 1, 0, 500);"
        ),
    },
    {
        "id": "insert-then-select",
        "level": "写入",
        "title": "插入后立即查询验证",
        "desc": "两段语句用分号隔开，可以一次执行。插入完马上查一下，确认真的进去了。",
        "source": "知识点延伸",
        "sql": (
            "-- 先插一条自己的学习记录，再立刻查出来看看\n"
            "INSERT INTO learning_record\n"
            "  (student_id, video_id, watched_episodes, progress, status)\n"
            "VALUES\n"
            "  ('20999999', 'V002', 6, 0.17, 'learning');\n"
            "SELECT lr.student_id, lr.watched_episodes, lr.status, v.title\n"
            "FROM learning_record lr\n"
            "JOIN video v ON v.video_id = lr.video_id\n"
            "WHERE lr.student_id = '20999999';"
        ),
    },
    {
        "id": "update",
        "level": "写入",
        "title": "UPDATE：修改已有数据",
        "desc": "⚠️ 一定要写 WHERE，不然整张表都会被改掉！这是新手最容易踩的坑。",
        "source": "backend/app.py:205 UPDATE student SET xp=xp+80 WHERE student_id=?",
        "sql": (
            "-- UPDATE 表 SET 列=新值 WHERE 条件\n"
            "-- WHERE 指定了只改这一个学生，没有 WHERE 就会改掉全表\n"
            "UPDATE student\n"
            "SET xp = xp + 80,\n"
            "    level = level + 1\n"
            "WHERE student_id = '20999999';"
        ),
    },
    {
        "id": "upsert",
        "level": "写入",
        "title": "UPSERT：存在就更新，不存在就插入",
        "desc": "项目里同步学习进度用的就是这招。ON CONFLICT 遇到主键冲突时改走更新分支。",
        "source": "backend/app.py:185-195 ON CONFLICT(student_id, video_id) DO UPDATE SET ...",
        "sql": (
            "-- 第一次执行会插入；再执行一次就会走 DO UPDATE 分支把进度改成 0.55\n"
            "INSERT INTO learning_record\n"
            "  (student_id, video_id, watched_episodes, progress, status)\n"
            "VALUES\n"
            "  ('20999999', 'V002', 20, 0.55, 'learning')\n"
            "ON CONFLICT(student_id, video_id) DO UPDATE SET\n"
            "  watched_episodes = excluded.watched_episodes,\n"
            "  progress = excluded.progress,\n"
            "  status = excluded.status;"
        ),
    },
    {
        "id": "delete",
        "level": "写入",
        "title": "DELETE：删除数据",
        "desc": "同样千万记得写 WHERE。这里删掉刚才练习插入的数据。",
        "source": "backend/student_profiles.py:255-258 DELETE FROM learning_record WHERE student_id IN (...)",
        "sql": (
            "-- 清理刚才练习插入的数据，让练习库恢复原样\n"
            "DELETE FROM learning_record WHERE student_id = '20999999';\n"
            "DELETE FROM student WHERE student_id = '20999999';"
        ),
    },
    {
        "id": "pragma-table",
        "level": "结构",
        "title": "查看表结构 PRAGMA table_info",
        "desc": "想知道某张表有哪些列、什么类型，就问它。",
        "source": "知识点延伸",
        "sql": (
            "-- cid=列序号, name=列名, type=类型, notnull=能否为空, pk=是否主键\n"
            "PRAGMA table_info('video');"
        ),
    },
    {
        "id": "pragma-tables",
        "level": "结构",
        "title": "列出库里所有表",
        "desc": "sqlite_master 是 SQLite 自带的「元数据表」，记录了所有表/索引/视图的定义。",
        "source": "知识点延伸",
        "sql": (
            "-- 主项目实际有 13 张业务表（不含 sqlite_ 开头的内部表）\n"
            "SELECT name, type\n"
            "FROM sqlite_master\n"
            "WHERE type IN ('table','index')\n"
            "  AND name NOT LIKE 'sqlite_%'\n"
            "ORDER BY type, name;"
        ),
    },
    {
        "id": "real-recall",
        "level": "实战",
        "title": "还原「正在学习的课程」召回逻辑",
        "desc": "推荐系统第一步是「召回候选」，这条 SQL 就是项目里专业路径召回的一段真实改写。",
        "source": "backend/recommender.py:77-92 专业路径多路召回",
        "sql": (
            "-- 第 1 路召回：找出状态为 studying 的课程，再取它们下面的所有视频\n"
            "SELECT c.course_id, c.name AS course_name, v.video_id, v.title, v.platform\n"
            "FROM course c\n"
            "JOIN video v ON v.course_id = c.course_id\n"
            "WHERE c.status = 'studying'\n"
            "ORDER BY v.popularity DESC\n"
            "LIMIT 12;"
        ),
    },
    {
        "id": "real-prereq",
        "level": "实战",
        "title": "还原「先修关系」进阶课召回",
        "desc": "课程的 prerequisites 字段用逗号分隔存了先修课编号，用 LIKE 做包含判断就能找出后继课程。",
        "source": "backend/recommender.py:84-88 WHERE prerequisites LIKE ?",
        "sql": (
            "-- C301(数据结构) 是很多课的先修，找出所有「把 C301 当先修」的后继课程\n"
            "SELECT course_id, name, semester, prerequisites\n"
            "FROM course\n"
            "WHERE prerequisites LIKE '%C301%'"
            "   OR prerequisites LIKE '%C101%'\n"
            "ORDER BY semester;"
        ),
    },
    {
        "id": "real-behavior",
        "level": "实战",
        "title": "还原「平台偏好」特征计算",
        "desc": "模型特征里有一项 platform_pref（平台偏好），底层就是这条聚合查询。",
        "source": "backend/train_model.py:127-138 统计学生各平台行为次数",
        "sql": (
            "-- 统计某个学生在各个平台上的行为次数（播放/完成/点赞/收藏都算）\n"
            "SELECT v.platform, COUNT(*) AS c\n"
            "FROM behavior_log b\n"
            "JOIN video v ON b.video_id = v.video_id\n"
            "WHERE b.student_id = '20240101'\n"
            "  AND b.event_type IN ('play','complete','like','favorite')\n"
            "GROUP BY v.platform\n"
            "ORDER BY c DESC;"
        ),
    },
    {
        "id": "count-tables",
        "level": "实战",
        "title": "摸清整个库的家底（各表行数）",
        "desc": "接手一个新项目，第一件事通常是看每张表有多少数据。",
        "source": "知识点延伸",
        "sql": (
            "-- 用 UNION ALL 把多个单表计数拼成一张纵表\n"
            "SELECT 'student' AS 表名, COUNT(*) AS 行数 FROM student\n"
            "UNION ALL SELECT 'course',   COUNT(*) FROM course\n"
            "UNION ALL SELECT 'video',    COUNT(*) FROM video\n"
            "UNION ALL SELECT 'video_episode', COUNT(*) FROM video_episode\n"
            "UNION ALL SELECT 'learning_record', COUNT(*) FROM learning_record\n"
            "UNION ALL SELECT 'behavior_log',    COUNT(*) FROM behavior_log\n"
            "UNION ALL SELECT 'recommend_log',   COUNT(*) FROM recommend_log\n"
            "UNION ALL SELECT 'job',      COUNT(*) FROM job\n"
            "UNION ALL SELECT 'exam_subject', COUNT(*) FROM exam_subject;"
        ),
    },
]
