"""SQLite 连接管理：线程内长连接 + WAL 日志模式。

为什么要专门做一层（下面都是本机实测数字，不是推断）：

1. **写接口慢在磁盘同步，不在 SQL。** 点赞、收藏、播放进度回写都只有一条
   INSERT，却要 110~135 ms。用进程内直连、绕开 HTTP 复测，同一条 INSERT
   仍然是这个数，说明瓶颈是 commit 时的 fsync。换 journal_mode 试了一圈
   （delete / truncate / persist / memory × FULL / NORMAL），写延迟都在
   110 ms 上下，只有 WAL 能压下来。

2. **但 WAL 和「每次请求新建连接」是互斥的。** WAL 在最后一个连接关闭时会做
   checkpoint 并把 -wal / -shm 删掉。原实现每个请求都 connect()/close() 一次，
   于是每请求都白跑一遍这个动作：

       场景                              读(36 行聚合)      写(单条 INSERT)
       delete 模式 + 每请求新建连接        4.2 ms            110.7 ms
       WAL    模式 + 每请求新建连接      149.1 ms             46.3 ms
       WAL    模式 + 复用连接              0.4 ms              0.1 ms

   也就是说，光切 WAL 不改连接方式，读反而慢 35 倍。

3. **所以改成线程内长连接。** uvicorn 把同步 handler 丢进线程池执行，
   threading.local 让每个线程各持一个连接：既没有每请求的开关销，也让 WAL
   真正生效。SQLite 自身的 check_same_thread 会额外保证连接不被跨线程误用。

业务代码里大量使用 `con = connect() ... finally: con.close()` 的写法，
所以这里的 close() 故意做成空操作（见 SharedConnection），真正的释放走 close_all()。

4. **WAL 由本模块自己保证**，见 ensure_wal()：bootstrap、reset_database、评测脚本、
   临时探针，任何一个入口拿连接都会顺手确认一次，不需要调用方记得去调。
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from .data_acquisition import DB_PATH

_local = threading.local()
_alive: List[sqlite3.Connection] = []
_guard = threading.Lock()
_generation = 0
_wal_lock = threading.Lock()
_wal_done = False
_wal_attempts = 0
_WAL_MAX_ATTEMPTS = 3


class SharedConnection:
    """线程内长连接的包装。

    close() 是空操作：否则第一个用完的 handler 就会把整个线程的连接关掉，
    后续请求会拿到一个已关闭的连接。真正释放请调用 close_all()。
    """

    __slots__ = ("_con",)

    def __init__(self, con: sqlite3.Connection) -> None:
        self._con = con

    def close(self) -> None:  # noqa: D401 - 有意为之的空操作
        return None

    def __getattr__(self, name: str) -> Any:
        return getattr(self._con, name)

    def __enter__(self) -> "SharedConnection":
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False

    def __repr__(self) -> str:  # pragma: no cover - 便于排查
        return f"<SharedConnection {self._con}>"


def tune_connection(con: sqlite3.Connection) -> sqlite3.Connection:
    """连接级参数。synchronous 不会持久化，每个连接都要设一次。"""
    try:
        con.execute("PRAGMA synchronous=NORMAL")
        con.execute("PRAGMA busy_timeout=30000")
    except sqlite3.DatabaseError:
        pass
    return con


def enable_wal(path: Path = DB_PATH) -> str:
    """把库切到 WAL。该设置写在库文件头里，成功执行一次即可。"""
    try:
        con = sqlite3.connect(path, timeout=30)
        try:
            return str(con.execute("PRAGMA journal_mode=WAL").fetchone()[0])
        finally:
            con.close()
    except sqlite3.DatabaseError:
        # 个别文件系统（网络盘等）不支持 WAL，退回默认模式即可，不影响功能
        return "unavailable"


def ensure_wal() -> str:
    """进程内只确认一次库是否为 WAL。

    不靠调用方记得去调 enable_wal()——bootstrap、reset_database、脚本、
    临时探针都可能拿连接，漏一个就退回 fsync 慢路径。
    失败只重试有限次，避免库被别的进程占着时每次取连接都白等一遍。
    """
    global _wal_done, _wal_attempts
    with _wal_lock:
        if _wal_done or _wal_attempts >= _WAL_MAX_ATTEMPTS:
            return "skipped" if not _wal_done else "cached"
        _wal_attempts += 1
        mode = enable_wal()
        _wal_done = mode == "wal"
        return mode


def open_connection() -> sqlite3.Connection:
    """新建一条独立连接（给脚本、评测、采集这类需要自己管生命周期的场景用）。"""
    ensure_wal()
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    return tune_connection(con)


def _new_shared(foreign_keys: bool) -> SharedConnection:
    raw = open_connection()
    if foreign_keys:
        raw.execute("PRAGMA foreign_keys = ON")
    shared = SharedConnection(raw)
    with _guard:
        _alive.append(raw)
    return shared


def connection(foreign_keys: bool = False) -> SharedConnection:
    """取本线程的长连接，没有就建一个。

    `foreign_keys=True` 会单独持一条开了外键约束的连接（PRAGMA 是连接级的，
    不能和普通连接混用同一条，否则会把约束泄漏给不想要它的调用方）。
    student_profiles 建画像时要靠外键约束防止脏引用，所以它单独走这一档。
    """
    slot_name = "_entry_fk" if foreign_keys else "_entry"
    entry = getattr(_local, slot_name, None)
    if entry is not None and entry[0] == _generation:
        return entry[1]
    shared = _new_shared(foreign_keys)
    setattr(_local, slot_name, (_generation, shared))
    return shared


def close_all() -> None:
    """关掉本进程建过的所有长连接。

    重建数据库（reset_database 会 unlink 库文件）之前必须调用，
    否则 Windows 上文件被占用会删不掉。同时把 WAL 确认状态复位，
    因为新库文件会回到默认的 delete 模式。
    """
    global _generation, _wal_done, _wal_attempts
    with _guard:
        conns = list(_alive)
        _alive.clear()
        _generation += 1
    for con in conns:
        try:
            con.close()
        except sqlite3.Error:
            pass
    _local._entry = None
    _local._entry_fk = None
    with _wal_lock:
        _wal_done = False
        _wal_attempts = 0


def tuning_report() -> Dict[str, Any]:
    """给自检脚本用：当前实际的日志模式与连接设置。"""
    con = sqlite3.connect(DB_PATH, timeout=30)
    try:
        return {
            "journal_mode": con.execute("PRAGMA journal_mode").fetchone()[0],
            "db_path": str(DB_PATH),
        }
    finally:
        con.close()
