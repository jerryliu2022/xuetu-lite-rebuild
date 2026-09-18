from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import random
import re
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .data_acquisition import DB_PATH
from .train_model import main as train_model
from .evaluate_model import evaluate
from .student_profiles import DEMO_ACCOUNTS

ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data" / "raw" / "live"
ARTIFACT_DIR = ROOT / "data" / "artifacts"

TARGET_MAJORS = [
    "计算机科学与技术",
    "软件工程",
    "人工智能",
    "数据科学与大数据技术",
    "网络工程",
]

HUNAN_GRADUATE_UNITS = [
    "湘潭大学",
    "吉首大学",
    "湖南大学",
    "中南大学",
    "湖南科技大学",
    "长沙理工大学",
    "湖南农业大学",
    "中南林业科技大学",
    "湖南中医药大学",
    "湖南师范大学",
    "湖南理工学院",
    "衡阳师范学院",
    "邵阳学院",
    "湖南文理学院",
    "湖南科技学院",
    "湖南人文科技学院",
    "湖南工商大学",
    "南华大学",
    "长沙学院",
    "湖南工程学院",
    "湖南城市学院",
    "湖南工业大学",
    "湖南第一师范学院",
    "长沙矿冶研究院",
    "中国航空研究院（608所）",
    "长沙矿山研究院",
    "湖南省中医药研究院",
    "中共湖南省委党校",
    "国防科技大学",
]

# 本科目标专业 -> 研招网最新硕士目录中的可报考专业代码。
# 同一本科专业可能对应多个学硕/专硕代码；只采集官方目录里实际存在的院校-专业组合。
CHSI_EXAM_QUERIES = {
    "计算机科学与技术": [
        {"code": "081200", "name": "计算机科学与技术"},
        {"code": "085404", "name": "计算机技术"},
    ],
    "软件工程": [
        {"code": "083500", "name": "软件工程"},
        {"code": "085405", "name": "软件工程"},
    ],
    "人工智能": [
        {"code": "085410", "name": "人工智能"},
    ],
    "数据科学与大数据技术": [
        {"code": "085411", "name": "大数据技术与工程"},
    ],
    "网络工程": [
        {"code": "085412", "name": "网络与信息安全"},
        {"code": "083900", "name": "网络空间安全"},
    ],
}

COURSE_KEYWORDS = [
    "数据结构",
    "操作系统",
    "计算机网络",
    "数据库",
    "机器学习",
    "软件工程",
    "人工智能",
    "Python",
    "Java",
    "算法",
]

# 每个目标专业的定向检索词。采集时按专业配额推进，避免热门通用课把
# 数据全部压到某一个专业下面。词表同时兼容普通课程和高校公开课。
MAJOR_SEARCH_KEYWORDS = {
    "计算机科学与技术": [
        "计算机组成原理",
        "操作系统 课程",
        "计算机网络 课程",
        "数据结构与算法 课程",
        "计算机基础 教程",
        "考研 408 课程",
        "编译原理 公开课",
    ],
    "软件工程": [
        "Java 后端 教程",
        "Spring Boot 实战",
        "软件工程 课程",
        "Python 开发 教程",
        "前端开发 教程",
        "Git 与工程实践",
    ],
    "人工智能": [
        "机器学习 课程",
        "深度学习 教程",
        "PyTorch 入门 教程",
        "大模型 入门 教程",
        "人工智能 公开课",
        "强化学习 入门",
    ],
    "数据科学与大数据技术": [
        "数据分析 SQL 教程",
        "大数据 Hadoop 教程",
        "数据挖掘 课程",
        "Python 数据分析 教程",
        "统计学 数据分析 课程",
        "大数据开发 实战",
    ],
    "网络工程": [
        "网络安全 课程",
        "网络工程师 教程",
        "TCP IP 协议 教程",
        "路由交换 入门",
        "渗透测试 入门 教程",
        "Linux 网络 教程",
    ],
}

JOB_KEYWORDS = [
    "Java 后端",
    "Python 开发",
    "算法工程师",
    "数据分析师",
    "前端开发",
    "人工智能",
    "网络安全",
    "软件测试",
    "大数据开发",
    "运维开发",
]

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.7",
}

# Some local proxy/debug tools export SSLKEYLOGFILE to a protected path.
# urllib/ssl then fails before the request is sent. Collection must not
# inherit that path; operators can still enable key logging explicitly with a
# writable path outside this process when debugging is needed.
os.environ.pop("SSLKEYLOGFILE", None)
import requests

DIRECT_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


@dataclass
class CollectResult:
    domain: str
    source: str
    inserted: int
    skipped: int
    target: int
    ok: bool
    message: str
    new_count: int = 0
    updated_count: int = 0


class CollectionBlocked(RuntimeError):
    """公开页面返回验证页或动态空壳，未获得业务数据。"""


def connect() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=30000")
    return con


def fetch_text(url: str, headers: Optional[Dict[str, str]] = None, timeout: int = 8) -> str:
    merged = {**DEFAULT_HEADERS, **(headers or {})}
    req = urllib.request.Request(url, headers=merged)
    try:
        try:
            response = DIRECT_OPENER.open(req, timeout=timeout)
        except urllib.error.HTTPError:
            raise
        except (urllib.error.URLError, OSError):
            response = urllib.request.urlopen(req, timeout=timeout)
        with response as resp:
            raw = resp.read()
            charset = resp.headers.get_content_charset() or "utf-8"
            text = raw.decode(charset, errors="replace")
            lowered = text.lower()
            challenge_markers = (
                "aliyun-captcha",
                "aliyun_waf",
                "access verification",
                "请完成验证",
                "data-tips",
                "browser-check",
            )
            if any(marker in lowered for marker in challenge_markers):
                raise CollectionBlocked("公开页面返回验证码/风控页，未获得业务数据")
            return text
    except PermissionError as exc:
        if "sslkey" in str(exc).lower():
            raise RuntimeError(
                "HTTPS 请求被本机 SSLKEYLOGFILE 权限阻断；采集器已自动清除该变量，请检查是否有外部代理重新注入"
            ) from exc
        raise


def fetch_json(url: str, headers: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    return json.loads(fetch_text(url, headers=headers))


def fetch_json_retry(
    url: str,
    header_variants: List[Dict[str, str]],
    attempts: int = 3,
    timeout: int = 12,
) -> Dict[str, Any]:
    """对公开 JSON 接口做有限重试，并切换正常页面来源头。"""
    last_error: Optional[Exception] = None
    for attempt in range(attempts):
        headers = header_variants[attempt % len(header_variants)]
        separator = "&" if "?" in url else "?"
        retry_url = f"{url}{separator}_={random.randint(100000, 999999)}"
        try:
            return json.loads(fetch_text(retry_url, headers=headers, timeout=timeout))
        except Exception as exc:
            last_error = exc
            time.sleep(0.45 + random.random() * 0.8)
    raise last_error or RuntimeError("公开接口请求失败")


def strip_tags(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", html.unescape(value or ""))).strip()


def stable_id(prefix: str, value: str) -> str:
    digest = hashlib.sha1(value.encode("utf-8")).hexdigest()[:14].upper()
    return f"{prefix}_{digest}"


def parse_count(value: Any) -> int:
    if isinstance(value, int):
        return value
    text = str(value or "").replace(",", "").strip().lower()
    if text in {"--", ""}:
        return 0
    match = re.search(r"([\d.]+)", text)
    if not match:
        return 0
    number = float(match.group(1))
    if "万" in text:
        number *= 10000
    return int(number)


def current_latest_exam_year() -> int:
    today = date.today()
    return today.year + 1 if today.month >= 8 else today.year


def ensure_schema() -> None:
    con = connect()
    existing = {row["name"] for row in con.execute("PRAGMA table_info(video)").fetchall()}
    video_cols = {
        "upload_date": "TEXT",
        "like_count": "INTEGER DEFAULT 0",
        "favorite_count": "INTEGER DEFAULT 0",
        "major": "TEXT",
        "source_raw_id": "TEXT",
        "source_collected_at": "TEXT",
    }
    for col, ddl in video_cols.items():
        if col not in existing:
            con.execute(f"ALTER TABLE video ADD COLUMN {col} {ddl}")

    existing_job = {row["name"] for row in con.execute("PRAGMA table_info(job)").fetchall()}
    job_cols = {
        "major": "TEXT",
        "published_at": "TEXT",
        "source_raw_id": "TEXT",
        "source_collected_at": "TEXT",
    }
    for col, ddl in job_cols.items():
        if col not in existing_job:
            con.execute(f"ALTER TABLE job ADD COLUMN {col} {ddl}")

    existing_exam = {row["name"] for row in con.execute("PRAGMA table_info(exam_subject)").fetchall()}
    exam_cols = {
        "year": "INTEGER",
        "source_url": "TEXT",
        "source_collected_at": "TEXT",
        "school_type": "TEXT",
    }
    for col, ddl in exam_cols.items():
        if col not in existing_exam:
            con.execute(f"ALTER TABLE exam_subject ADD COLUMN {col} {ddl}")

    existing_course = {row["name"] for row in con.execute("PRAGMA table_info(course)").fetchall()}
    if "major" not in existing_course:
        con.execute("ALTER TABLE course ADD COLUMN major TEXT")

    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS collection_run (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          domain TEXT NOT NULL,
          source TEXT NOT NULL,
          target_count INTEGER NOT NULL,
          inserted_count INTEGER NOT NULL,
          skipped_count INTEGER NOT NULL,
          ok INTEGER NOT NULL,
          message TEXT NOT NULL,
          started_at TEXT NOT NULL,
          finished_at TEXT NOT NULL,
          new_count INTEGER NOT NULL DEFAULT 0,
          updated_count INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS collector_state (
          key TEXT PRIMARY KEY,
          value TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        """
    )
    run_columns = {row["name"] for row in con.execute("PRAGMA table_info(collection_run)").fetchall()}
    if "new_count" not in run_columns:
        con.execute("ALTER TABLE collection_run ADD COLUMN new_count INTEGER NOT NULL DEFAULT 0")
    if "updated_count" not in run_columns:
        con.execute("ALTER TABLE collection_run ADD COLUMN updated_count INTEGER NOT NULL DEFAULT 0")
    log_columns = {row["name"] for row in con.execute("PRAGMA table_info(recommend_log)").fetchall()}
    if "is_synthetic" not in log_columns:
        con.execute("ALTER TABLE recommend_log ADD COLUMN is_synthetic INTEGER NOT NULL DEFAULT 0")
    # Repair misleading records produced by the pre-fix collector.
    con.execute(
        """
        UPDATE collection_run
        SET message='历史记录：旧版本采集未达到目标，未记录具体失败原因'
        WHERE inserted_count < target_count AND ok=0 AND message='ok'
        """
    )
    con.commit()
    con.close()


def ensure_major_courses() -> None:
    con = connect()
    now = datetime.now().isoformat(timespec="seconds")
    for idx, major in enumerate(TARGET_MAJORS, start=1):
        course_id = stable_id("MAJOR", major)
        con.execute(
            """
            INSERT OR IGNORE INTO course
              (course_id,name,semester,credits,status,concepts,prerequisites,major)
            VALUES (?,?,?,?,?,?,?,?)
            """,
            (course_id, f"{major}综合提升", 5 + idx % 2, 3, "planned", major, "", major),
        )
        con.execute(
            """
            INSERT OR IGNORE INTO student
              (student_id,name,school,major,grade,semester,level,xp,next_xp)
            VALUES (?,?,?,?,?,?,?,?,?)
            """,
            (f"LIVE{idx:03d}", f"{major[:2]}同学", "江城大学", major, "大二", 3, 4, 720, 1200),
        )
    con.execute(
        "INSERT OR REPLACE INTO collector_state VALUES (?,?,?)",
        ("target_majors", json.dumps(TARGET_MAJORS, ensure_ascii=False), now),
    )
    con.commit()
    con.close()


def course_for_major(major: str) -> str:
    return stable_id("MAJOR", major)


def sync_recommend_log(con: sqlite3.Connection) -> int:
    """重建轻量曝光样本：种子课保留，真实采集课按专业抽样作负样本。"""
    now = datetime.now().isoformat(timespec="seconds")
    student_ids = [account["student_id"] for account in DEMO_ACCOUNTS]
    placeholders = ",".join("?" * len(student_ids))
    con.execute("DELETE FROM recommend_log WHERE is_synthetic=1")
    con.execute(
        """
        UPDATE recommend_log
        SET click=1, impression=1
        WHERE (student_id, candidate_id) IN (
          SELECT student_id, video_id FROM learning_record WHERE progress > 0
        )
        """
    )
    for student in con.execute(
        """
        SELECT student_id FROM student
        WHERE student_id IN (%s)
        """ % placeholders,
        student_ids,
    ).fetchall():
        student_id = student["student_id"]
        positive_rows = con.execute(
            """
            SELECT video_id FROM learning_record
            WHERE student_id=? AND progress > 0
            """,
            (student_id,),
        ).fetchall()
        existing_candidates = {
            row["candidate_id"]
            for row in con.execute(
                "SELECT candidate_id FROM recommend_log WHERE student_id=?",
                (student_id,),
            ).fetchall()
        }
        existing_candidates.update(row["video_id"] for row in positive_rows)
        for positive in positive_rows:
            con.execute(
                """
                INSERT INTO recommend_log
                  (student_id, scenario, candidate_id, impression, click, created_at, is_synthetic)
                VALUES (?,?,?,1,1,?,1)
                """,
                (student_id, "professional", positive["video_id"], now),
            )
        negative_pool = [
            row["video_id"]
            for row in con.execute(
                """
                SELECT v.video_id
                FROM video v
                WHERE v.platform='MOOC' OR v.platform='极客时间'
                   OR (v.platform='B站' AND v.source_collected_at IS NOT NULL)
                ORDER BY v.popularity DESC, v.video_id
                """
            ).fetchall()
        ]
        seen_major: Dict[str, int] = {}
        for video_id in negative_pool:
            if len(existing_candidates) >= 30:
                break
            if video_id in existing_candidates:
                continue
            video_row = con.execute(
                "SELECT major FROM video WHERE video_id=?",
                (video_id,),
            ).fetchone()
            major_value = (video_row[0] if video_row else "通用") or "通用"
            if seen_major.get(major_value, 0) >= 5:
                continue
            seen_major[major_value] = seen_major.get(major_value, 0) + 1
            con.execute(
                """
                INSERT INTO recommend_log
                  (student_id, scenario, candidate_id, impression, click, created_at, is_synthetic)
                VALUES (?,?,?,1,0,?,1)
                """,
                (student_id, "professional", video_id, now),
            )
    return 0


def link_real_videos_to_curriculum(con: sqlite3.Connection) -> int:
    """把真实课程尽量映射到课内课程，让推荐模型能按学期/先修召回。"""
    rules = [
        (["java", "spring", "web", "后端", "jvm"], "C602"),
        (["数据结构", "算法", "leetcode", "刷题"], "C301"),
        (["操作系统", "进程", "linux 内核", "内核"], "C401"),
        (["计算机网络", "tcp", "ip", "http", "网络协议"], "C402"),
        (["数据库", "mysql", "sql", "索引"], "C501"),
        (["机器学习", "深度学习", "神经网络", "tensorflow", "pytorch"], "C502"),
        (["软件工程", "敏捷", "测试", "项目管理", "工程化"], "C601"),
        (["高等数学", "线性代数", "概率", "微积分"], "C101"),
        (["离散数学", "图论"], "C202"),
        (["c 语言", "c语言", "c++"], "C201"),
    ]
    linked = 0
    for video in con.execute(
        "SELECT video_id, title, tags, major, course_id FROM video WHERE source_collected_at IS NOT NULL"
    ).fetchall():
        text = " ".join([video["title"] or "", video["tags"] or ""]).lower()
        best = ""
        best_hits = 0
        for keywords, target_course in rules:
            hits = sum(1 for keyword in keywords if keyword.lower() in text)
            if hits > best_hits:
                best_hits = hits
                best = target_course
        if best:
            con.execute("UPDATE video SET course_id=? WHERE video_id=?", (best, video["video_id"]))
            linked += 1
    return linked


def upsert_video(con: sqlite3.Connection, item: Dict[str, Any]) -> bool:
    existed = con.execute(
        "SELECT 1 FROM video WHERE video_id=?",
        (item["video_id"],),
    ).fetchone() is not None
    con.execute(
        """
        INSERT INTO video (
          video_id,course_id,title,platform,is_paid,teacher,org,popularity,rating,
          episodes,duration,tags,summary,source_url,cover_color,upload_date,
          like_count,favorite_count,major,source_raw_id,source_collected_at
        )
        VALUES (
          :video_id,:course_id,:title,:platform,:is_paid,:teacher,:org,:popularity,:rating,
          :episodes,:duration,:tags,:summary,:source_url,:cover_color,:upload_date,
          :like_count,:favorite_count,:major,:source_raw_id,:source_collected_at
        )
        ON CONFLICT(video_id) DO UPDATE SET
          title=excluded.title,
          popularity=excluded.popularity,
          rating=excluded.rating,
          tags=excluded.tags,
          summary=excluded.summary,
          source_url=excluded.source_url,
          upload_date=excluded.upload_date,
          like_count=excluded.like_count,
          favorite_count=excluded.favorite_count,
          major=excluded.major,
          source_collected_at=excluded.source_collected_at
        """,
        item,
    )
    existing = con.execute("SELECT COUNT(*) FROM video_episode WHERE video_id=?", (item["video_id"],)).fetchone()[0]
    if existing:
        return not existed
    for idx in range(1, min(12, int(item["episodes"])) + 1):
        con.execute(
            "INSERT OR IGNORE INTO video_episode VALUES (?,?,?,?,?,?)",
            (
                f"{item['video_id']}-{idx:02d}",
                item["video_id"],
                idx,
                f"{item['title']} 第 {idx:02d} 讲",
                f"{35 + idx % 20}:{(idx * 7) % 60:02d}",
                item["source_url"],
            ),
        )
    return not existed


def assign_major_for_title(
    keyword_major: str,
    keyword: str,
    title: str,
    quota: Dict[str, int],
) -> str:
    """优先用检索词的专业；标题出现更具体专业名且配额未满时再覆盖。"""
    title_major = next(
        (candidate for candidate in TARGET_MAJORS if candidate in title),
        "",
    )
    if title_major and quota.get(title_major, 0) > 0 and title_major != keyword_major:
        return title_major
    return keyword_major


def build_bilibili_session() -> requests.Session:
    """用公开首页/搜索页建立会话，并获取站点发放的 buvid cookie。"""
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.7",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        }
    )
    session.get("https://www.bilibili.com/", timeout=15)
    time.sleep(0.8 + random.random() * 0.6)
    session.get("https://search.bilibili.com/video?keyword=%E6%95%99%E7%A8%8B", timeout=15)
    time.sleep(0.5 + random.random() * 0.5)
    spi = session.get("https://api.bilibili.com/x/frontend/finger/spi", timeout=15)
    payload = spi.json().get("data") or {}
    if payload.get("b_3"):
        session.cookies.set("buvid3", payload["b_3"], domain=".bilibili.com")
    if payload.get("b_4"):
        session.cookies.set("buvid4", payload["b_4"], domain=".bilibili.com")
    return session


def collect_bilibili(target: int, cutoff: date) -> CollectResult:
    source = "B站"
    con = connect()
    inserted = skipped = 0
    new_count = updated_count = 0
    seen = set()
    errors: List[str] = []
    # 总目标按 5 个目标专业均分；热门通用课也不能挤占冷门专业。
    quota = {major: max(1, target // len(TARGET_MAJORS)) for major in TARGET_MAJORS}
    deadline = time.monotonic() + max(120, min(900, target * 8))
    session = build_bilibili_session()
    session_age = time.monotonic()
    consecutive_412 = 0
    search_headers = {
        "Referer": "https://search.bilibili.com/video",
        "Origin": "https://search.bilibili.com",
        "Accept": "application/json, text/plain, */*",
    }
    keyword_plan = []
    for major in TARGET_MAJORS:
        for term in MAJOR_SEARCH_KEYWORDS[major]:
            keyword_plan.append((major, f"{major} {term}"))
    keyword_plan.sort(key=lambda pair: quota[pair[0]], reverse=True)
    keyword_index = 0
    while any(quota.values()) and time.monotonic() < deadline and keyword_index < len(keyword_plan):
        keyword_major, keyword = keyword_plan[keyword_index]
        keyword_index += 1
        if quota[keyword_major] <= 0:
            continue
        fetched_current = False
        for order in ("stow", "pubdate", "click"):
            if time.monotonic() >= deadline or quota[keyword_major] <= 0:
                break
            for page in range(1, 4):
                if time.monotonic() >= deadline or quota[keyword_major] <= 0:
                    break
                params = {
                    "search_type": "video",
                    "keyword": keyword,
                    "page": page,
                    "order": order,
                    "page_size": 20,
                }
                try:
                    resp = session.get(
                        "https://api.bilibili.com/x/web-interface/search/type",
                        params=params,
                        headers=search_headers,
                        timeout=18,
                    )
                    if resp.status_code == 412:
                        raise RuntimeError("HTTP 412，公开搜索接口要求重建会话")
                    data = resp.json()
                    if data.get("code") != 0:
                        raise RuntimeError(str(data.get("message") or "公开搜索接口返回非零状态"))
                except Exception as exc:
                    errors.append(f"{keyword_major}/{keyword}: {exc}")
                    if "412" in str(exc):
                        try:
                            consecutive_412 += 1
                            time.sleep(4 + consecutive_412 * 3 + random.random() * 3)
                            session = build_bilibili_session()
                            session_age = time.monotonic()
                        except Exception as rebuild_exc:
                            errors.append(f"重建会话失败: {rebuild_exc}")
                    time.sleep(1.0 + random.random() * 1.0)
                    continue
                consecutive_412 = 0
                # 会话建立后小睡一会再开始高频查询，降低风控概率。
                if time.monotonic() - session_age < 1.0:
                    time.sleep(1.2 + random.random() * 1.0)
                result_rows = (data.get("data") or {}).get("result") or []
                if not result_rows:
                    break
                fetched_current = True
                for raw in result_rows:
                    if time.monotonic() >= deadline or quota[keyword_major] <= 0:
                        break
                    bvid = raw.get("bvid")
                    if not bvid or bvid in seen:
                        continue
                    seen.add(bvid)
                    pubdate = raw.get("pubdate") or 0
                    uploaded = datetime.fromtimestamp(pubdate).date() if pubdate else None
                    if not uploaded:
                        skipped += 1
                        continue
                    if uploaded < cutoff or uploaded > date.today():
                        skipped += 1
                        continue
                    title = strip_tags(raw.get("title", ""))
                    if not title or not re.search(r"课程|教程|公开课|精讲|实战|入门|考研|基础|速成|讲", title):
                        skipped += 1
                        continue
                    major = assign_major_for_title(keyword_major, keyword, title, quota)
                    if quota[major] <= 0:
                        # 命中词放在别的专业下时，若该专业已满则不重复占用配额。
                        skipped += 1
                        continue
                    raw_url = raw.get("arcurl") or f"https://www.bilibili.com/video/{bvid}"
                    source_url = raw_url.replace("http://", "https://", 1) if raw_url.startswith("http://") else raw_url
                    favorite_count = parse_count(raw.get("favorites"))
                    duration_text = raw.get("duration")
                    if isinstance(duration_text, str) and duration_text:
                        try:
                            parts = duration_text.split(":")
                            minutes = int(parts[0]) * 60 if len(parts) >= 2 else int(parts[0])
                            if len(parts) == 3:
                                minutes = int(parts[0]) * 60 + int(parts[1])
                            episodes = max(1, min(120, round(minutes / 35)))
                            duration = minutes * 60
                        except (TypeError, ValueError):
                            episodes, duration = 12, 900
                    else:
                        episodes, duration = 12, 900
                    existed = upsert_video(
                        con,
                        {
                            "video_id": stable_id("BILI", bvid),
                            "course_id": course_for_major(major),
                            "title": title[:120],
                            "platform": source,
                            "is_paid": 0,
                            "teacher": strip_tags(raw.get("author", ""))[:40],
                            "org": "Bilibili",
                            "popularity": parse_count(raw.get("play")),
                            "rating": round(min(5.0, 4.2 + min(favorite_count / 100000, 0.7)), 2),
                            "episodes": episodes,
                            "duration": duration,
                            "tags": f"{keyword} {strip_tags(raw.get('tag', ''))}",
                            "summary": strip_tags(raw.get("description", ""))[:240] or f"{keyword}相关高热视频教程。",
                            "source_url": source_url,
                            "cover_color": "#884df0",
                            "upload_date": uploaded.isoformat(),
                            "like_count": parse_count(raw.get("like")),
                            "favorite_count": favorite_count,
                            "major": major,
                            "source_raw_id": bvid,
                            "source_collected_at": datetime.now().isoformat(timespec="seconds"),
                        },
                    )
                    if existed:
                        new_count += 1
                    else:
                        updated_count += 1
                    quota[major] -= 1
                    inserted += 1
                time.sleep(0.9 + random.random() * 1.1)
        if not fetched_current and errors:
            # 单个关键词连续失败后换词继续，避免整轮直接熔断。
            continue
    con.commit()
    con.close()
    if inserted >= target:
        message = "ok" if not errors else f"ok，已从瞬时网络错误恢复：{'; '.join(errors[:3])}"
    else:
        message = "; ".join(errors[:4]) if errors else "未达到目标数量，公开搜索接口返回的合规课程不足"
    return CollectResult(
        "video",
        source,
        inserted,
        skipped,
        target,
        inserted >= target,
        message,
        new_count,
        updated_count,
    )


def collect_html_course_platform(platform: str, base_urls: List[str], target: int, cutoff: date) -> CollectResult:
    con = connect()
    inserted = skipped = 0
    new_count = updated_count = 0
    errors = []
    seen = set()
    deadline = time.monotonic() + max(30, min(180, target * 3))
    for major in TARGET_MAJORS:
        if inserted >= target or time.monotonic() >= deadline:
            break
        for term in COURSE_KEYWORDS:
            if inserted >= target or time.monotonic() >= deadline:
                break
            keyword = f"{major} {term} 课程"
            for tpl in base_urls:
                if inserted >= target or time.monotonic() >= deadline:
                    break
                url = tpl.format(q=urllib.parse.quote(keyword))
                try:
                    text = fetch_text(url)
                except Exception as exc:
                    errors.append(f"{platform}:{keyword}:{exc}")
                    continue
                links = re.findall(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', text, flags=re.I | re.S)
                for href, label in links:
                    if inserted >= target:
                        break
                    title = strip_tags(label)
                    if not title or len(title) < 4:
                        continue
                    if not re.search(r"课程|教程|公开课|训练营|实战|体系课|入门|精讲", title):
                        continue
                    absolute = urllib.parse.urljoin(url, href)
                    if absolute in seen:
                        continue
                    seen.add(absolute)
                    item = {
                        "video_id": stable_id(platform.upper().replace("站", ""), absolute),
                        "course_id": course_for_major(major),
                        "title": title[:120],
                        "platform": platform,
                        "is_paid": 1 if platform == "极客时间" else 0,
                        "teacher": "",
                        "org": platform,
                        "popularity": 0,
                        "rating": 4.3,
                        "episodes": 12,
                        "duration": 900,
                        "tags": keyword,
                        "summary": f"从 {platform} 公开页面采集的 {major} 相关课程。",
                        "source_url": absolute,
                        "cover_color": "#4f7cff" if platform == "MOOC" else "#119caf",
                        "upload_date": date.today().isoformat(),
                        "like_count": 0,
                        "favorite_count": 0,
                        "major": major,
                        "source_raw_id": absolute,
                        "source_collected_at": datetime.now().isoformat(timespec="seconds"),
                    }
                    if datetime.fromisoformat(item["upload_date"]).date() < cutoff:
                        skipped += 1
                        continue
                    if upsert_video(con, item):
                        new_count += 1
                    else:
                        updated_count += 1
                    inserted += 1
                    if inserted >= target:
                        break
                time.sleep(0.4)
    con.commit()
    con.close()
    if inserted >= target:
        message = "ok" if not errors else f"ok，已从瞬时网络错误恢复：{'; '.join(errors[:3])}"
    else:
        message = "; ".join(errors[:3]) if errors else "未达到目标数量，公开页面未返回足够可解析课程"
    return CollectResult(
        "video",
        platform,
        inserted,
        skipped,
        target,
        inserted >= target,
        message,
        new_count,
        updated_count,
    )


def recent_date_from_page(text: str, cutoff: date) -> Optional[date]:
    """只接受页面中能确认的近六年日期，未知日期不冒充新课程。"""
    candidates: List[date] = []
    for year, month, day in re.findall(r"(20\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})日?", text):
        try:
            value = date(int(year), int(month), int(day))
        except ValueError:
            continue
        if cutoff <= value <= date.today():
            candidates.append(value)
    if candidates:
        return max(candidates)
    for year in re.findall(r"(20\d{2})年?", text):
        value = date(int(year), 7, 1)
        if cutoff.year <= value.year <= date.today().year:
            return value
    return None


def collect_indexed_course_platform(
    platform: str,
    allowed_domains: Tuple[str, ...],
    target: int,
    cutoff: date,
    probe_limit: Optional[int] = None,
) -> CollectResult:
    """从公开搜索索引取得平台公开课程页，再回读课程页校验日期。"""
    con = connect()
    inserted = skipped = 0
    new_count = updated_count = 0
    errors: List[str] = []
    seen: set = set()
    deadline = time.monotonic() + max(45, min(240, target * 5))
    probes = 0
    for major in TARGET_MAJORS:
        if inserted >= target or time.monotonic() >= deadline:
            break
        for term in COURSE_KEYWORDS:
            if inserted >= target or time.monotonic() >= deadline:
                break
            probes += 1
            if probe_limit and probes > probe_limit:
                break
            search_domain = allowed_domains[0].split("/")[0]
            query = urllib.parse.quote(
                f"site:{search_domain} {major} {term} 课程"
            )
            search_text = ""
            search_url = ""
            for search_host in ("www.bing.com", "cn.bing.com"):
                candidate_url = f"https://{search_host}/search?q={query}&count=50"
                try:
                    search_text = fetch_text(candidate_url, timeout=7)
                    search_url = candidate_url
                    break
                except Exception as exc:
                    errors.append(f"{platform}:{major}:{term}:{search_host}:{exc}")
            if not search_text:
                continue
            links = re.findall(
                r'<a[^>]+href=["\'](https?://[^"\']+)["\'][^>]*>(.*?)</a>',
                search_text,
                flags=re.I | re.S,
            )
            for href, label in links:
                if inserted >= target:
                    break
                href = html.unescape(href)
                if not any(domain in href for domain in allowed_domains):
                    continue
                href = href.split("&amp;")[0]
                if href in seen:
                    continue
                seen.add(href)
                title = strip_tags(label)
                if len(title) < 4:
                    continue
                try:
                    detail = fetch_text(
                        href,
                        headers={"Referer": search_url},
                        timeout=8,
                    )
                except Exception as exc:
                    errors.append(f"{platform}:{href}:{exc}")
                    continue
                published = recent_date_from_page(detail, cutoff)
                if not published:
                    skipped += 1
                    continue
                major_for_item = next(
                    (candidate for candidate in TARGET_MAJORS if candidate in title or candidate in detail),
                    major,
                )
                item = {
                    "video_id": stable_id(platform.upper().replace("站", ""), href),
                    "course_id": course_for_major(major_for_item),
                    "title": title[:120],
                    "platform": platform,
                    "is_paid": 1 if platform == "极客时间" else 0,
                    "teacher": "",
                    "org": platform,
                    "popularity": 0,
                    "rating": 4.3,
                    "episodes": 12,
                    "duration": 900,
                    "tags": f"{major_for_item} {term}",
                    "summary": f"从 {platform} 公开课程页采集的 {major_for_item} 相关课程。",
                    "source_url": href,
                    "cover_color": "#4f7cff" if platform == "MOOC" else "#119caf",
                    "upload_date": published.isoformat(),
                    "like_count": 0,
                    "favorite_count": 0,
                    "major": major_for_item,
                    "source_raw_id": href,
                    "source_collected_at": datetime.now().isoformat(timespec="seconds"),
                }
                if upsert_video(con, item):
                    new_count += 1
                else:
                    updated_count += 1
                inserted += 1
                time.sleep(0.25 + random.random() * 0.3)
    con.commit()
    con.close()
    message = "ok" if inserted >= target else (
        "; ".join(errors[:5])
        if errors
        else "未达到目标数量：公开搜索未返回可回读课程页，或课程页无法验证近 6 年公开日期（需要平台授权接口/导出文件）"
    )
    return CollectResult(
        "video",
        platform,
        inserted,
        skipped,
        target,
        inserted >= target,
        message,
        new_count,
        updated_count,
    )


def collect_mooc(target: int, cutoff: date) -> CollectResult:
    return collect_indexed_course_platform(
        "MOOC",
        ("icourse163.org/course/",),
        target,
        cutoff,
        probe_limit=10,
    )


def collect_geektime(target: int, cutoff: date) -> CollectResult:
    return collect_indexed_course_platform(
        "极客时间",
        ("time.geekbang.org/column/", "time.geekbang.org/course/"),
        target,
        cutoff,
        probe_limit=8,
    )


def parse_job_cards(text: str, source: str, major: str) -> List[Dict[str, Any]]:
    cards = []
    fragments = re.split(r"</li>|</div>\s*</div>", text)
    for fragment in fragments:
        clean = strip_tags(fragment)
        if not re.search(r"工程师|开发|算法|数据|测试|运维|安全", clean):
            continue
        salary = re.search(r"(\d+[-~]\d+K|\d+[-~]\d+k|\d+[-~]\d+千|\d+[-~]\d+万)", clean)
        title = re.search(r"([\u4e00-\u9fffA-Za-z0-9+# ]{2,24}(工程师|开发|算法|分析师|测试|运维|安全))", clean)
        if not title:
            continue
        href = re.search(r'href=["\']([^"\']+)["\']', fragment)
        # 只接受真实包含发布时间文本的岗位片段，避免给验证页/空壳页制造“今天发布”。
        if "发布" not in clean and "时间" not in clean and "更新" not in clean:
            continue
        date_match = re.search(r"(20\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})", clean)
        published_at = ""
        if date_match:
            try:
                published_at = date(
                    int(date_match.group(1)),
                    int(date_match.group(2)),
                    int(date_match.group(3)),
                ).isoformat()
            except ValueError:
                published_at = ""
        cards.append(
            {
                "title": title.group(1).strip(),
                "company": clean.split("|")[0][-18:].strip() or "公开招聘页面",
                "city": "全国",
                "salary": salary.group(1) if salary else "面议",
                "fresh": "社招/校招",
                "major_match": "高度匹配" if major in clean else "相关延伸",
                "required_major": major,
                "skills": infer_skills(clean),
                "requirement": clean[:300],
                "source": source,
                "source_url": href.group(1) if href else "",
                "major": major,
                "published_at": published_at,
            }
        )
    return cards


def infer_skills(text: str) -> str:
    skills = []
    for skill in ["Java", "Python", "Spring Boot", "MySQL", "Redis", "React", "Vue", "算法", "机器学习", "数据结构", "网络安全", "Linux", "SQL"]:
        if skill.lower() in text.lower():
            skills.append(skill)
    return ",".join(skills[:6] or ["数据结构", "SQL", "工程实践"])


def upsert_job(con: sqlite3.Connection, item: Dict[str, Any]) -> None:
    raw_key = f"{item['source']}:{item['title']}:{item['company']}:{item['major']}:{item['source_url']}"
    item = {
        **item,
        "job_id": stable_id("JOB", raw_key),
        "source_raw_id": raw_key,
        "source_collected_at": datetime.now().isoformat(timespec="seconds"),
    }
    con.execute(
        """
        INSERT INTO job (
          job_id,title,company,city,salary,fresh,major_match,required_major,skills,
          requirement,source,source_url,major,published_at,source_raw_id,source_collected_at
        )
        VALUES (
          :job_id,:title,:company,:city,:salary,:fresh,:major_match,:required_major,:skills,
          :requirement,:source,:source_url,:major,:published_at,:source_raw_id,:source_collected_at
        )
        ON CONFLICT(job_id) DO UPDATE SET
          salary=excluded.salary,
          requirement=excluded.requirement,
          published_at=excluded.published_at,
          source_collected_at=excluded.source_collected_at
        """,
        item,
    )


def collect_jobs(total_target: int, cutoff: date) -> CollectResult:
    con = connect()
    inserted = skipped = 0
    new_count = updated_count = 0
    errors = []
    deadline = time.monotonic() + max(45, min(360, total_target * 2))
    source_blocked: set = set()
    source_targets = [("拉勾网", "https://www.lagou.com/wn/jobs?kd={q}&city=%E5%85%A8%E5%9B%BD"), ("BOSS直聘", "https://www.zhipin.com/web/geek/job?query={q}&city=100010000")]
    for major in TARGET_MAJORS:
        if inserted >= total_target or time.monotonic() >= deadline or len(source_blocked) == len(source_targets):
            break
        for keyword in JOB_KEYWORDS:
            if inserted >= total_target or time.monotonic() >= deadline or len(source_blocked) == len(source_targets):
                break
            q = urllib.parse.quote(f"{major} {keyword}")
            for source, tpl in source_targets:
                if inserted >= total_target or time.monotonic() >= deadline:
                    break
                if source in source_blocked:
                    continue
                url = tpl.format(q=q)
                try:
                    text = fetch_text(url, headers={"Referer": "https://www.baidu.com/"})
                except Exception as exc:
                    errors.append(f"{source}:{keyword}:{exc}")
                    if isinstance(exc, CollectionBlocked):
                        source_blocked.add(source)
                        break
                    continue
                if not text.strip() or len(text) < 2000:
                    errors.append(f"{source}:{keyword}:公开页面为空壳，未获得岗位卡片")
                    source_blocked.add(source)
                    break
                for card in parse_job_cards(text, source, major):
                    if inserted >= total_target:
                        break
                    if not card.get("published_at"):
                        skipped += 1
                        continue
                    if not card["source_url"]:
                        skipped += 1
                        continue
                    published = datetime.fromisoformat(card["published_at"]).date()
                    if published < cutoff or published > date.today():
                        skipped += 1
                        continue
                    if card["source_url"]:
                        card["source_url"] = urllib.parse.urljoin(url, card["source_url"])
                    existing_id = stable_id(
                        "JOB",
                        f"{card['source']}:{card['title']}:{card['company']}:{card['major']}:{card['source_url']}",
                    )
                    existed = con.execute(
                        "SELECT 1 FROM job WHERE job_id=?",
                        (existing_id,),
                    ).fetchone() is not None
                    upsert_job(con, card)
                    if existed:
                        updated_count += 1
                    else:
                        new_count += 1
                    inserted += 1
                    if inserted >= total_target:
                        break
                time.sleep(0.8)
    con.commit()
    con.close()
    if inserted >= total_target:
        message = "ok" if not errors else f"ok，已从瞬时网络错误恢复：{'; '.join(errors[:5])}"
    else:
        message = "; ".join(errors[:5]) if errors else "公开岗位页未返回足够可解析岗位，可能需要授权接口或站点允许访问"
    return CollectResult(
        "job",
        "拉勾网+BOSS直聘",
        inserted,
        skipped,
        total_target,
        inserted >= total_target,
        message,
        new_count,
        updated_count,
    )


class ChsiOfficialCatalog:
    """研招网“硕士专业目录”公开查询接口的最小合规客户端。"""

    BASE = "https://yz.chsi.com.cn"

    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.7",
            }
        )
        # 先访问目录首页以取得必要 Cookie，再调用公开查询 RPC。
        self.session.get(f"{self.BASE}/zsml/", timeout=20)

    def _post(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        url = f"{self.BASE}{path}"
        headers = {
            "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": f"{self.BASE}/zsml/",
        }
        last_error = ""
        for attempt in range(4):
            if attempt:
                time.sleep(1.4 + attempt * random.random() * 1.2)
            try:
                resp = self.session.post(url, data=payload, headers=headers, timeout=25)
            except requests.RequestException as exc:
                last_error = str(exc)
                continue
            if resp.status_code != 200:
                last_error = f"HTTP {resp.status_code}"
                continue
            try:
                data = resp.json()
            except ValueError:
                last_error = "响应不是 JSON"
                continue
            message = data.get("msg")
            if isinstance(message, str) and ("太频繁" in message or "频繁" in message):
                last_error = message
                time.sleep(2.0 + random.random() * 2.0)
                continue
            if not data.get("flag"):
                detail = str(message or data.get("msg2") or "目录查询失败")
                if "登录" in detail:
                    raise CollectionBlocked(f"研招网要求登录才能继续分页: {detail}")
                raise RuntimeError(detail)
            return data
        raise RuntimeError(f"研招网目录接口请求失败: {last_error}")

    def offered_units(self, code: str, name: str) -> List[Dict[str, Any]]:
        payload = {
            "zydm": code,
            "zymc": name,
            "dwmc": "",
            "dwdm": "",
            "ssdm": "43",
            "xxfs": "",
            "dwlxs": ["all"],
            "tydxs": "",
            "jsggjh": "",
            "start": "0",
            "curPage": "1",
            "pageSize": "100",
            "totalPage": "0",
            "totalCount": "0",
        }
        data = self._post("/zsml/rs/zydws.do", payload)
        message = data.get("msg")
        if not isinstance(message, dict):
            raise RuntimeError(f"研招网返回异常: {message}")
        return list(message.get("list") or [])

    def direction_rows(self, code: str, name: str, dwdm: str) -> List[Dict[str, Any]]:
        payload = {
            "zydm": code,
            "zymc": name,
            "dwdm": dwdm,
            "xxfs": "",
            "dwlxs": ["all"],
            "tydxs": "",
            "jsggjh": "",
            "start": "0",
            "pageSize": "100",
            "totalCount": "0",
        }
        data = self._post("/zsml/rs/yjfxs.do", payload)
        message = data.get("msg")
        if not isinstance(message, dict):
            raise RuntimeError(f"研招网研究方向接口返回异常: {message}")
        rows = list(message.get("list") or [])
        total = int(message.get("totalCount") or 0)
        if rows and total > len(rows):
            # 个别单位方向很多，未登录可能只能取第一页；仍保留已取得科目。
            time.sleep(1.0 + random.random())
            payload["start"] = str(len(rows))
            try:
                extra = self._post("/zsml/rs/yjfxs.do", payload)
                extra_msg = extra.get("msg")
                if isinstance(extra_msg, dict):
                    rows.extend(extra_msg.get("list") or [])
            except (CollectionBlocked, RuntimeError):
                pass
        return rows


def chsi_subject_names(row: Dict[str, Any]) -> List[str]:
    names: List[str] = []
    for combo in row.get("kskmz") or []:
        for idx in range(1, 5):
            vo = combo.get(f"km{idx}Vo") or {}
            subject = str(vo.get("kskmmc") or "").strip()
            if subject:
                names.append(subject)
    return names


def collect_hunan_exam_subjects() -> CollectResult:
    con = connect()
    inserted = skipped = 0
    new_count = updated_count = 0
    errors: List[str] = []
    unresolved = 0
    latest_year = current_latest_exam_year()
    catalog = ChsiOfficialCatalog()
    grouped: Dict[Tuple[str, str], Dict[str, Any]] = {}
    official_target = 0
    for project_major, queries in CHSI_EXAM_QUERIES.items():
        for query in queries:
            code = query["code"]
            name = query["name"]
            try:
                units = catalog.offered_units(code, name)
            except Exception as exc:
                errors.append(f"{project_major}/{name}: {exc}")
                skipped += 1
                continue
            for unit in units:
                dwdm = str(unit.get("dwdm") or "")
                dwmc = str(unit.get("dwmc") or "").strip()
                if not dwdm or not dwmc:
                    continue
                key = (dwmc, project_major)
                if key not in grouped:
                    grouped[key] = {
                        "school": dwmc,
                        "dwdm": dwdm,
                        "major": project_major,
                        "subjects": [],
                        "source_url": "",
                        "source_id": "",
                    }
                    official_target += 1
                try:
                    direction_rows = catalog.direction_rows(code, name, dwdm)
                except Exception as exc:
                    errors.append(f"{dwmc}:{project_major}:{exc}")
                    unresolved += 1
                    skipped += 1
                    continue
                if not direction_rows:
                    skipped += 1
                    continue
                group = grouped[key]
                for row in direction_rows:
                    row_id = str(row.get("id") or "")
                    if row_id and not group["source_id"]:
                        group["source_id"] = row_id
                        group["source_url"] = f"https://yz.chsi.com.cn/zsml/yjfxdetail?id={row_id}"
                    for subject in chsi_subject_names(row):
                        if subject not in group["subjects"]:
                            group["subjects"].append(subject)
                time.sleep(0.8 + random.random() * 0.7)

    now_iso = datetime.now().isoformat(timespec="seconds")
    for group in grouped.values():
        if not group["subjects"]:
            skipped += 1
            continue
        existed = con.execute(
            "SELECT 1 FROM exam_subject WHERE school=? AND major=?",
            (group["school"], group["major"]),
        ).fetchone() is not None
        con.execute(
            """
            INSERT INTO exam_subject (school,major,subjects,year,source_url,source_collected_at,school_type)
            VALUES (?,?,?,?,?,?,?)
            ON CONFLICT(school, major) DO UPDATE SET
              subjects=excluded.subjects,
              year=excluded.year,
              source_url=excluded.source_url,
              source_collected_at=excluded.source_collected_at,
              school_type=excluded.school_type
            """,
            (
                group["school"],
                group["major"],
                ",".join(group["subjects"]),
                latest_year,
                group["source_url"],
                now_iso,
                "湖南研招单位-研招网官方硕士目录",
            ),
        )
        inserted += 1
        if existed:
            updated_count += 1
        else:
            new_count += 1
    con.commit()
    con.close()
    theoretical_target = len(HUNAN_GRADUATE_UNITS) * len(TARGET_MAJORS)
    message = (
        f"研招网{latest_year}官方硕士目录：湖南实际开设 {official_target} 个可报考学校-专业组合，"
        f"理论全矩阵为 {theoretical_target}；成功保存 {inserted} 条，未解析 {unresolved} 个"
    )
    if errors:
        message += "；错误：" + "; ".join(errors[:4])
    return CollectResult(
        "exam",
        "湖南研招单位-研招网官方硕士目录",
        inserted,
        skipped,
        official_target or theoretical_target,
        official_target > 0 and unresolved == 0 and inserted >= official_target,
        message,
        new_count,
        updated_count,
    )


def record_results(results: Iterable[CollectResult], started_at: str) -> Dict[str, Any]:
    finished_at = datetime.now().isoformat(timespec="seconds")
    con = connect()
    payload = []
    for result in results:
        con.execute(
            """
            INSERT INTO collection_run
              (domain,source,target_count,inserted_count,skipped_count,ok,message,started_at,finished_at,new_count,updated_count)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                result.domain,
                result.source,
                result.target,
                result.inserted,
                result.skipped,
                1 if result.ok else 0,
                result.message[:1000],
                started_at,
                finished_at,
                result.new_count,
                result.updated_count,
            ),
        )
        payload.append(result.__dict__)
    con.execute(
        "INSERT OR REPLACE INTO collector_state VALUES (?,?,?)",
        ("last_live_collection", json.dumps(payload, ensure_ascii=False), finished_at),
    )
    con.commit()
    con.close()
    report = {
        "started_at": started_at,
        "finished_at": finished_at,
        "results": payload,
        "success": all(item["ok"] for item in payload),
        "success_definition": "仅当每个数据源本次新增或更新达到目标数量时才算成功",
    }
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    (ARTIFACT_DIR / "live_collection_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def run_live_collection(
    video_per_platform: int = 50,
    job_total: int = 200,
    collect_exam: bool = True,
    retrain: bool = True,
) -> Dict[str, Any]:
    ensure_schema()
    ensure_major_courses()
    con = connect()
    link_real_videos_to_curriculum(con)
    sync_recommend_log(con)
    con.commit()
    con.close()
    started_at = datetime.now().isoformat(timespec="seconds")
    video_cutoff = date.today() - timedelta(days=365 * 6)
    job_cutoff = date.today() - timedelta(days=365 * 2)
    results = [
        collect_bilibili(video_per_platform, video_cutoff),
        collect_mooc(video_per_platform, video_cutoff),
        collect_geektime(video_per_platform, video_cutoff),
        collect_jobs(job_total, job_cutoff),
    ]
    if collect_exam:
        results.append(collect_hunan_exam_subjects())
    report = record_results(results, started_at)
    if retrain:
        con = connect()
        link_real_videos_to_curriculum(con)
        sync_recommend_log(con)
        con.commit()
        con.close()
        train_model()
        report["evaluation"] = evaluate()["ranking_eval"]["averages"]
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="真实数据采集与更新")
    parser.add_argument("--video-per-platform", type=int, default=50)
    parser.add_argument("--job-total", type=int, default=200)
    parser.add_argument("--skip-exam", action="store_true")
    parser.add_argument("--no-retrain", action="store_true")
    args = parser.parse_args()
    report = run_live_collection(
        video_per_platform=args.video_per_platform,
        job_total=args.job_total,
        collect_exam=not args.skip_exam,
        retrain=not args.no_retrain,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
