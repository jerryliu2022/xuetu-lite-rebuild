# -*- coding: utf-8 -*-
"""
在线 Python 后端代码执行器（沙箱）。

需求背景：学习者要能通过「写一段后端代码 → 立刻看到数据库里发生什么」来理解
后端是怎么操作数据库的。所以必须允许真实执行 Python + sqlite3。

怎么保证安全又不失真？
1. AST 静态检查：代码真正执行前，先把源码解析成语法树，
   - import 的模块必须在白名单里（sqlite3/json/math/re 这类，禁掉 os/subprocess/socket）
   - 出现 open / eval / exec / __import__ / getattr 等危险名字直接拒绝
   - 访问任何 __xxx__ 双下划线属性（如 __globals__、__builtins__）也拒绝
2. 数据库路径由系统注入：变量 DB_PATH 已经被写死成练习库，
   学习者写 sqlite3.connect(DB_PATH) 是真实写法，但碰不到正式库。
3. 超时保护：用线程池跑代码，超过 8 秒就判定超时并返回提示。
"""

from __future__ import annotations

import ast
import json
import math
import re
import sqlite3
import sys
import time
from collections import Counter, defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from io import StringIO
from itertools import combinations, islice
from pathlib import Path
from typing import Any, Dict, List, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[2]
LEARN_ROOT = PROJECT_ROOT / "learn"
TEST_DB = LEARN_ROOT / "data" / "learn_test.db"

# 允许 import 的模块（全部是无副作用的纯计算/数据处理模块）
ALLOWED_MODULES = {
    "sqlite3",
    "json",
    "math",
    "re",
    "datetime",
    "collections",
    "itertools",
    "functools",
    "statistics",
    "random",
    "decimal",
    "copy",
    "time",
}

# 不允许出现在代码里的函数名
BANNED_CALLS = {
    "open",
    "eval",
    "exec",
    "compile",
    "__import__",
    "input",
    "getattr",
    "setattr",
    "delattr",
    "globals",
    "locals",
    "vars",
    "dir",
    "memoryview",
    "breakpoint",
    "help",
    "exit",
    "quit",
}

TIMEOUT_SECONDS = 8

# 能穿透沙箱、拿到外部作用域或篡改运行环境的属性
BANNED_ATTRS = {
    "__globals__", "__builtins__", "__class__", "__base__", "__bases__",
    "__subclasses__", "__dict__", "__code__", "__closure__", "__loader__",
    "__module__", "__reduce__", "__getattribute__",
}


class SandboxError(Exception):
    """沙箱拒绝执行时抛出，错误信息会直接显示给学习者。"""


def validate_code(code: str) -> None:
    """AST 静态检查：不合规就抛 SandboxError。"""
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        raise SandboxError(f"语法错误：第 {exc.lineno} 行 {exc.msg}")

    for node in ast.walk(tree):
        # 1) import 白名单
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root not in ALLOWED_MODULES:
                    raise SandboxError(
                        f"不允许 import 「{alias.name}」。"
                        f"本沙箱开放的模块：{', '.join(sorted(ALLOWED_MODULES))}"
                    )
        elif isinstance(node, ast.ImportFrom):
            if node.module is None:
                continue
            root = node.module.split(".")[0]
            if root not in ALLOWED_MODULES:
                raise SandboxError(
                    f"不允许 from {node.module} import ... 。"
                    f"本沙箱开放的模块：{', '.join(sorted(ALLOWED_MODULES))}"
                )

        # 2) 危险函数调用
        elif isinstance(node, ast.Call):
            func = node.func
            name = None
            if isinstance(func, ast.Name):
                name = func.id
            elif isinstance(func, ast.Attribute):
                name = func.attr
            if name in BANNED_CALLS:
                raise SandboxError(f"不允许调用「{name}()」，沙箱内该函数已被禁用。")

        # 3) 反射类属性：这些能穿透到沙箱外去篡改环境，必须禁掉。
        #    注意不能用「凡是 __ 开头结尾就禁」这种一刀切规则 ——
        #    那样会误伤 type(x).__name__、fn.__doc__ 之类完全无害的常用写法。
        elif isinstance(node, ast.Attribute):
            if node.attr in BANNED_ATTRS:
                raise SandboxError(
                    f"不允许访问属性「{node.attr}」，这是沙箱明令禁止的反射用法。"
                )

        # 4) 直接引用危险名字（不调用也会被拦，比如把 open 赋值给变量）
        elif isinstance(node, ast.Name):
            if node.id in BANNED_CALLS and isinstance(node.ctx, ast.Load):
                raise SandboxError(f"不允许使用名字「{node.id}」。")


def _make_globals() -> Dict[str, Any]:
    """构造受限执行环境：只放入安全的内置名字和工具函数。"""
    safe_builtins = {
        name: getattr(__builtins__, name) if hasattr(__builtins__, name) else None
        for name in [
            "abs", "all", "any", "bool", "dict", "enumerate", "float", "int",
            "len", "list", "map", "max", "min", "print", "range", "round",
            "set", "sorted", "str", "sum", "tuple", "zip", "filter", "reversed",
            "Exception", "ValueError", "TypeError", "KeyError", "IndexError",
            "AttributeError", "ZeroDivisionError", "isinstance", "type",
        ]
    }
    safe_builtins = {k: v for k, v in safe_builtins.items() if v is not None}

    # 把 unicode/python 版本的 builtins 处理好
    builtin_items: Dict[str, Any] = {}
    source = __builtins__ if isinstance(__builtins__, dict) else vars(__builtins__)
    for name in [
        "abs", "all", "any", "bool", "dict", "enumerate", "float", "int",
        "len", "list", "map", "max", "min", "print", "range", "round",
        "set", "sorted", "str", "sum", "tuple", "zip", "filter", "reversed",
        "Exception", "ValueError", "TypeError", "KeyError", "IndexError",
        "AttributeError", "ZeroDivisionError", "isinstance", "type",
    ]:
        if name in source:
            builtin_items[name] = source[name]

    env: Dict[str, Any] = {
        "__builtins__": builtin_items,
        # ---- 数据/计算类模块：学习者可以自由使用 ----
        "json": json,
        "math": math,
        "re": re,
        "datetime": datetime,
        "Counter": Counter,
        "defaultdict": defaultdict,
        "deque": deque,
        "itertools_combinations": combinations,
    }

    # 练习库路径：写死，学习者碰不到正式库
    env["DB_PATH"] = str(TEST_DB)

    # ---- 常用工具：让学习者的代码更接近项目写法 ----
    def connect_db(readonly: bool = False):
        """返回练习库连接。readonly=True 时用只读 URI 打开（多一层保险）。"""
        if readonly:
            return sqlite3.connect(f"file:{TEST_DB}?mode=ro", uri=True)
        con = sqlite3.connect(str(TEST_DB))
        con.row_factory = sqlite3.Row  # 让查询结果能像字典一样取值
        return con

    def rows(con, sql: str, params=()):
        """和项目 backend/recommender.py:21 里的 rows() 一模一样的写法。"""
        con.row_factory = sqlite3.Row
        return [dict(r) for r in con.execute(sql, params).fetchall()]

    env["connect_db"] = connect_db
    env["rows"] = rows
    env["sqlite3"] = sqlite3
    return env


def _execute_in_thread(code: str, env: Dict[str, Any], buffer: StringIO) -> Any:
    """真正执行的地方，跑在单独线程里以便超时控制。"""
    old_stdout = sys.stdout
    sys.stdout = buffer
    try:
        exec(compile(code, "<learn-sandbox>", "exec"), env)  # noqa: S102
        return env.get("result", None)
    finally:
        sys.stdout = old_stdout


def run_code(code: str) -> Dict[str, Any]:
    """执行一段学习者提交的 Python 代码，捕获 stdout、返回值和异常。"""
    started = time.time()

    # 第一步：静态检查（不合格直接返回，根本不执行）
    try:
        validate_code(code)
    except SandboxError as exc:
        return {
            "ok": False,
            "stdout": "",
            "result": None,
            "error": str(exc),
            "stage": "静态检查",
            "elapsed_ms": round((time.time() - started) * 1000, 2),
        }

    # 第二步：确保练习库存在
    TEST_DB.parent.mkdir(parents=True, exist_ok=True)
    if not TEST_DB.exists():
        from .db_sandbox import ensure_test_db

        ensure_test_db()

    env = _make_globals()
    buffer = StringIO()
    pool = ThreadPoolExecutor(max_workers=1)

    try:
        future = pool.submit(_execute_in_thread, code, env, buffer)
        try:
            result = future.result(timeout=TIMEOUT_SECONDS)
        except TimeoutError:
            return {
                "ok": False,
                "stdout": buffer.getvalue(),
                "result": None,
                "error": f"执行超时（超过 {TIMEOUT_SECONDS} 秒）。大概率是写了死循环，检查一下 while 条件。",
                "stage": "执行超时",
                "elapsed_ms": round((time.time() - started) * 1000, 2),
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "ok": False,
                "stdout": buffer.getvalue(),
                "result": None,
                "error": f"{type(exc).__name__}: {exc}",
                "stage": "运行时",
                "elapsed_ms": round((time.time() - started) * 1000, 2),
            }

        # result 需要能 JSON 序列化，不能是连接对象之类的
        try:
            json.dumps(result, ensure_ascii=False)
        except TypeError:
            result = repr(result)

        return {
            "ok": True,
            "stdout": buffer.getvalue(),
            "result": result,
            "error": "",
            "stage": "完成",
            "elapsed_ms": round((time.time() - started) * 1000, 2),
        }
    finally:
        pool.shutdown(wait=False)


# ---------------------------------------------------------------- 预置后端代码示例
# 每段都可以在页面上直接运行，全部针对练习库，注释详尽
PRESET_CODE: List[Dict[str, str]] = [
    {
        "id": "basic-connect",
        "level": "第 1 步",
        "title": "连接数据库并执行第一条查询",
        "desc": "后端操作数据库永远是这三步：连接 → 执行 SQL → 处理结果/关闭连接。",
        "source": "backend/recommender.py:15-22 connect() 与 rows()",
        "code": (
            "# 1) 连接数据库：一个文件就是一个库，路径由沙箱注入（指向练习库）\n"
            "con = sqlite3.connect(DB_PATH)\n"
            "\n"
            "# 2) 设置 row_factory，这样查出来的每一行都能用列名取值，而不是只能用下标\n"
            "con.row_factory = sqlite3.Row\n"
            "\n"
            "# 3) 执行 SQL：这是最基础的 SELECT\n"
            "cur = con.execute('SELECT video_id, title, platform FROM video LIMIT 5')\n"
            "\n"
            "# 4) 取结果：fetchall() 一次拿全部，fetchone() 只拿第一行\n"
            "rows_list = [dict(r) for r in cur.fetchall()]\n"
            "for row in rows_list:\n"
            "    print(row['video_id'], '|', row['title'], '|', row['platform'])\n"
            "\n"
            "# 5) 用完一定要关，否则连接会一直占着文件锁\n"
            "con.close()\n"
            "\n"
            "# 页面上「返回值」区域展示的就是这个 result\n"
            "result = rows_list"
        ),
    },
    {
        "id": "param-query",
        "level": "第 2 步",
        "title": "带参数的查询（防止 SQL 注入）",
        "desc": "永远不要拼接字符串写 SQL，用 ? 占位符。项目里所有查询都是这么写的。",
        "source": "backend/app.py:122 SELECT * FROM student WHERE student_id=?",
        "code": (
            "con = sqlite3.connect(DB_PATH)\n"
            "con.row_factory = sqlite3.Row\n"
            "\n"
            "# ? 是占位符，第二个参数用元组传进去，sqlite3 会自动做转义\n"
            "# 如果直接拼字符串：f\"... WHERE student_id='{sid}'\" ，一旦用户输入带引号就会出问题\n"
            "sid = 'YY08'\n"
            "row = con.execute('SELECT * FROM student WHERE student_id=?', (sid,)).fetchone()\n"
            "student = dict(row) if row else None\n"
            "print('查到的学生：', student)\n"
            "\n"
            "# 再看一个 range 条件的例子：某学生所有有进度的学习记录\n"
            "records = con.execute(\n"
            "    'SELECT video_id, progress, status FROM learning_record WHERE student_id=? AND progress>0',\n"
            "    (sid,)\n"
            ").fetchall()\n"
            "print('学习记录条数：', len(records))\n"
            "for r in records:\n"
            "    print('  ', r['video_id'], r['progress'], r['status'])\n"
            "\n"
            "con.close()\n"
            "result = student"
        ),
    },
    {
        "id": "insert-flow",
        "level": "第 3 步",
        "title": "插入数据：为什么必须 commit",
        "desc": "写操作（INSERT/UPDATE/DELETE）后一定要 commit，否则连接关闭时数据会被回滚丢掉。",
        "source": "backend/app.py:170-175 INSERT INTO behavior_log ... + db.commit()",
        "code": (
            "con = sqlite3.connect(DB_PATH)\n"
            "\n"
            "# 插入一条埋点日志，模拟用户在页面上点了一个视频\n"
            "now = datetime.now().isoformat(timespec='seconds')\n"
            "cur = con.execute(\n"
            "    'INSERT INTO behavior_log (student_id, event_type, video_id, duration, created_at) '\n"
            "    'VALUES (?, ?, ?, ?, ?)',\n"
            "    ('YY08', 'click', 'V002', 0, now)\n"
            ")\n"
            "print('新记录自增 id =', cur.lastrowid)\n"
            "\n"
            "# 关键：写操作必须提交！sqlite3 默认不会自动提交\n"
            "con.commit()\n"
            "\n"
            "# 提交后再查，确认真的写进去了\n"
            "now_first = con.execute(\n"
            "    'SELECT id, student_id, event_type, video_id FROM behavior_log ORDER BY id DESC LIMIT 1'\n"
            ").fetchone()\n"
            "print('刚刚写进去的那条：', tuple(now_first))\n"
            "\n"
            "con.close()\n"
            "result = dict(zip(['id','student_id','event_type','video_id'], now_first))"
        ),
    },
    {
        "id": "update-flow",
        "level": "第 4 步",
        "title": "更新数据并观察变化前后",
        "desc": "还原项目里的「看完一集加经验值」逻辑，演示 UPDATE + 立刻回读验证。",
        "source": "backend/app.py:205 UPDATE student SET xp=xp+80 WHERE student_id=?",
        "code": (
            "con = sqlite3.connect(DB_PATH)\n"
            "con.row_factory = sqlite3.Row\n"
            "\n"
            "sid = 'YY08'\n"
            "before = con.execute('SELECT xp, level FROM student WHERE student_id=?', (sid,)).fetchone()\n"
            "print('更新前 xp =', before['xp'])\n"
            "\n"
            "# 看完一门课给 80 经验值\n"
            "con.execute('UPDATE student SET xp = xp + 80 WHERE student_id = ?', (sid,))\n"
            "con.commit()\n"
            "\n"
            "after = con.execute('SELECT xp, level FROM student WHERE student_id=?', (sid,)).fetchone()\n"
            "print('更新后 xp =', after['xp'])\n"
            "\n"
            "# 再改回去，保持练习库干净（幂等，方便反复运行）\n"
            "con.execute('UPDATE student SET xp = ? WHERE student_id = ?', (before['xp'], sid))\n"
            "con.commit()\n"
            "\n"
            "restored = con.execute('SELECT xp FROM student WHERE student_id=?', (sid,)).fetchone()\n"
            "print('还原后 xp =', restored['xp'])\n"
            "\n"
            "con.close()\n"
            "result = {'before': before['xp'], 'after': after['xp'], 'restored': restored['xp']}"
        ),
    },
    {
        "id": "upsert-flow",
        "level": "第 5 步",
        "title": "UPSERT：更新学习进度的正确姿势",
        "desc": "同一个学生+视频只能有一条进度记录，重复提交应该覆盖而不是报错。这是项目里最实用的一个 SQL 技巧。",
        "source": "backend/app.py:185-195 ON CONFLICT(student_id, video_id) DO UPDATE",
        "code": (
            "con = sqlite3.connect(DB_PATH)\n"
            "con.row_factory = sqlite3.Row\n"
            "\n"
            "def save_progress(con, sid, vid, watched, episodes):\n"
            "    \"\"\"保存学习进度：已有记录就更新，没有就插入。\"\"\"\n"
            "    progress = min(1.0, round(watched / episodes, 4))\n"
            "    status = 'completed' if watched >= episodes else 'learning'\n"
            "    con.execute(\n"
            "        'INSERT INTO learning_record (student_id, video_id, watched_episodes, progress, status) '\n"
            "        'VALUES (?, ?, ?, ?, ?) '\n"
            "        'ON CONFLICT(student_id, video_id) DO UPDATE SET '\n"
            "        '  watched_episodes=excluded.watched_episodes, '\n"
            "        '  progress=excluded.progress, '\n"
            "        '  status=excluded.status',\n"
            "        (sid, vid, watched, progress, status)\n"
            "    )\n"
            "    con.commit()\n"
            "    return {'watched': watched, 'progress': progress, 'status': status}\n"
            "\n"
            "sid, vid, total = 'YY08', 'V002', 36\n"
            "\n"
            "# 第一次：走到第 6 集\n"
            "print('第 1 次提交：', save_progress(con, sid, vid, 6, total))\n"
            "# 第二次：走到第 30 集 —— 会走更新分支，而不是报主键冲突\n"
            "print('第 2 次提交：', save_progress(con, sid, vid, 30, total))\n"
            "\n"
            "cur = con.execute(\n"
            "    'SELECT * FROM learning_record WHERE student_id=? AND video_id=?', (sid, vid)\n"
            ").fetchone()\n"
            "print('最终库里的记录：', dict(cur))\n"
            "\n"
            "con.close()\n"
            "result = dict(cur)"
        ),
    },
    {
        "id": "reproduce-recall",
        "level": "第 6 步",
        "title": "还原真实推荐逻辑：多路召回里的「专业匹配」",
        "desc": "把 recommender.py 里的一段召回逻辑拆成最朴素的版本，看看后端是怎么从库里捞出候选课的。",
        "source": "backend/recommender.py:114-123 按专业 + 热度兜底召回",
        "code": (
            "con = sqlite3.connect(DB_PATH)\n"
            "con.row_factory = sqlite3.Row\n"
            "\n"
            "sid = 'YY08'\n"
            "\n"
            "# 第 1 步：拿到这个学生的专业和学期\n"
            "student = dict(con.execute('SELECT * FROM student WHERE student_id=?', (sid,)).fetchone())\n"
            "print('学生专业：', student['major'], '| 第', student['semester'], '学期')\n"
            "\n"
            "# 第 2 步：召回 1 —— 下学期要学的课，适合提前预习\n"
            "planned = con.execute(\n"
            "    'SELECT * FROM course WHERE semester = ?', (student['semester'] + 1,)\n"
            ").fetchall()\n"
            "print('下学期课程：', [c['name'] for c in planned])\n"
            "\n"
            "# 第 3 步：召回 2 —— 这些课对应的高热度视频\n"
            "candidates = []\n"
            "for c in planned:\n"
            "    vids = con.execute(\n"
            "        'SELECT video_id, title, popularity FROM video WHERE course_id=? ORDER BY popularity DESC LIMIT 3',\n"
            "        (c['course_id'],)\n"
            "    ).fetchall()\n"
            "    for v in vids:\n"
            "        candidates.append({\n"
            "            'video_id': v['video_id'],\n"
            "            'title': v['title'],\n"
            "            'course': c['name'],\n"
            "            'reason': f\"下学期将学《{c['name']}》，适合假期预习\"\n"
            "        })\n"
            "\n"
            "print(f'\\n共召回 {len(candidates)} 个候选：')\n"
            "for item in candidates:\n"
            "    print('  -', item['title'], '<-', item['reason'])\n"
            "\n"
            "con.close()\n"
            "result = candidates"
        ),
    },
    {
        "id": "simulate-api",
        "level": "第 7 步",
        "title": "亲手写一个「后端接口函数」",
        "desc": "把上面所有步骤串起来：这就是 app.py 里那些 @app.get 装饰的函数真正在做的事——取参数、查库、组装返回。",
        "source": "backend/app.py:137-139 profile() / backend/recommender.py:299-317 user_profile()",
        "code": (
            "con = sqlite3.connect(DB_PATH)\n"
            "con.row_factory = sqlite3.Row\n"
            "\n"
            "def get_profile(student_id):\n"
            "    \"\"\"模拟 GET /api/profile/{student_id} 的内部实现。\n"
            "\n"
            "    真实项目里，这个函数外面套一层 @app.get('/api/profile/{student_id}')，\n"
            "    然后把返回的 dict 交给 FastAPI 自动序列化成 JSON。\n"
            "    \"\"\"\n"
            "    # 1) 基本信息\n"
            "    student = con.execute('SELECT * FROM student WHERE student_id=?', (student_id,)).fetchone()\n"
            "    if not student:\n"
            "        # 真实项目里这里会 raise HTTPException(status_code=404, detail='未找到数据')\n"
            "        raise ValueError('未找到该学生')\n"
            "\n"
            "    # 2) 培养方案课程表\n"
            "    courses = con.execute('SELECT * FROM course ORDER BY semester, course_id').fetchall()\n"
            "\n"
            "    # 3) 学习记录（JOIN 出课程标题，前端才有东西可以显示）\n"
            "    records = con.execute(\n"
            "        'SELECT lr.video_id, lr.progress, lr.status, v.title '\n"
            "        'FROM learning_record lr LEFT JOIN video v ON v.video_id = lr.video_id '\n"
            "        'WHERE lr.student_id = ?',\n"
            "        (student_id,)\n"
            "    ).fetchall()\n"
            "\n"
            "    # 4) 组装返回 JSON —— 注意这里全是基本类型，才能被 JSON 序列化\n"
            "    return {\n"
            "        'student': dict(student),\n"
            "        'course_count': len(courses),\n"
            "        'records': [dict(r) for r in records],\n"
            "    }\n"
            "\n"
            "profile = get_profile('YY08')\n"
            "print('接口返回结构：', json.dumps(profile, ensure_ascii=False)[:300], '...')\n"
            "print('\\n学生：', profile['student']['name'], profile['student']['major'])\n"
            "print('学习记录：')\n"
            "for r in profile['records']:\n"
            "    print(f\"   进度 {r['progress']:.0%}  {r['title']}\")\n"
            "\n"
            "con.close()\n"
            "result = profile"
        ),
    },
    {
        "id": "transaction",
        "level": "第 8 步",
        "title": "事务：要么全成功，要么全回滚",
        "desc": "中途出错时用 rollback() 撤销。这在做「新增记录 + 加经验值」这种多步操作时非常重要。",
        "source": "知识点延伸（对应后端写接口的健壮性要求）",
        "code": (
            "con = sqlite3.connect(DB_PATH)\n"
            "before = con.execute('SELECT COUNT(*) FROM behavior_log').fetchone()[0]\n"
            "print('操作前 behavior_log 行数：', before)\n"
            "\n"
            "try:\n"
            "    con.execute(\n"
            "        'INSERT INTO behavior_log (student_id, event_type, video_id, duration, created_at) '\n"
            "        'VALUES (?,?,?,?,?)',\n"
            "        ('YY08', 'play', 'V002', 60, datetime.now().isoformat(timespec='seconds'))\n"
            "    )\n"
            "    print('第 1 步：插入埋点 OK')\n"
            "\n"
            "    # 故意制造一个错误：往不存在的表插数据\n"
            "    con.execute('INSERT INTO not_exist_table VALUES (1)')\n"
            "    con.commit()\n"
            "except Exception as e:\n"
            "    # 出错了就回滚：刚才那步插入也会被撤销\n"
            "    con.rollback()\n"
            "    print('捕获到异常：', type(e).__name__, e)\n"
            "    print('已执行 rollback()，前面的插入被撤销')\n"
            "\n"
            "after = con.execute('SELECT COUNT(*) FROM behavior_log').fetchone()[0]\n"
            "print('操作后 behavior_log 行数：', after)\n"
            "print('结论：行数不变说明事务回滚生效了 →', before == after)\n"
            "\n"
            "con.close()\n"
            "result = {'before': before, 'after': after, 'rollback_worked': before == after}"
        ),
    },
]
