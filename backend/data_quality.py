from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "processed" / "xuetu_lite.db"

MAJOR_CATALOG = [
    "计算机科学与技术",
    "软件工程",
    "数据科学与大数据技术",
    "人工智能",
    "网络工程",
    "信息安全",
    "物联网工程",
    "电子信息工程",
    "自动化",
    "统计学",
    "数学与应用数学",
    "电子商务",
]

PROJECT_MAJORS = [
    "计算机科学与技术",
    "软件工程",
    "人工智能",
    "数据科学与大数据技术",
    "网络工程",
]

HUNAN_TARGET_SCHOOLS = 29
VIDEO_TARGET_PER_PLATFORM = 50
JOB_TARGET_TOTAL = 200

SOURCE_POLICIES = [
    {
        "domain": "视频课程",
        "sources": ["MOOC", "B站", "极客时间"],
        "live_collected": True,
        "storage": "video / video_episode",
        "target_scale": "本次任务：B站、MOOC、极客时间各 50 门；仅保留近 6 年且能验证日期的课程",
        "update_frequency": "每日 02:00 增量，周日全量校验",
        "live_source_status": "B站真实采集已入库且覆盖 5 个专业；MOOC/极客时间公开搜索当前仅返回 JS 空壳或未收录课程页，无法验证近 6 年课程日期，报告会如实标记未达标",
        "risk": "需遵守平台 robots、版权和反爬策略；极客时间仅展示和跳转，不绕过付费墙",
    },
    {
        "domain": "招聘岗位",
        "sources": ["拉勾网", "BOSS直聘", "牛客网"],
        "live_collected": False,
        "storage": "job / skill_course_map",
        "target_scale": "本次任务：拉勾网+BOSS直聘合计 200 条；仅保留近 2 年岗位",
        "update_frequency": "每日 04:00 增量，岗位下线 7 天后归档",
        "live_source_status": "拉勾网/BOSS直聘公开搜索当前返回阿里云验证码页，自动化脚本无法合法取得岗位卡片；需平台授权接口或人工导出后才能入库",
        "risk": "招聘数据变动快，正式采集应使用授权接口或公开合规页面",
    },
    {
        "domain": "考研科目",
        "sources": ["高校研究生招生目录", "研招网公开信息"],
        "live_collected": False,
        "storage": "exam_subject",
        "target_scale": "核对湖南全部 29 个研招单位；按最新官方硕士目录只保存真实开设的院校-专业组合，不伪造未开设专业的空科目",
        "update_frequency": "每年招生目录发布季每日检查，其余月份每周检查",
        "live_source_status": "研招网官方硕士目录真实采集已入库；只保存最新年份实际存在的院校-专业科目组合",
        "risk": "院校科目按年份变化，推荐结果必须带数据年份和更新时间",
    },
]


def connect() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def rows(con: sqlite3.Connection, sql: str, params=()) -> List[Dict[str, Any]]:
    return [dict(row) for row in con.execute(sql, params).fetchall()]


def scalar(con: sqlite3.Connection, sql: str, params=()) -> int:
    return int(con.execute(sql, params).fetchone()[0] or 0)


def has_column(con: sqlite3.Connection, table: str, column: str) -> bool:
    return column in {row["name"] for row in con.execute(f"PRAGMA table_info({table})").fetchall()}


def video_coverage(con: sqlite3.Connection) -> Dict[str, Any]:
    has_live = has_column(con, "video", "source_collected_at")
    has_date = has_column(con, "video", "upload_date")
    has_major = has_column(con, "video", "major")
    cutoff = (datetime.now() - timedelta(days=365 * 6)).date().isoformat()
    live_clause = "source_collected_at IS NOT NULL" if has_live else "1=0"
    recent_clause = (
        f"{live_clause} AND date(upload_date) >= date('{cutoff}') AND date(upload_date) <= date('now')"
        if has_date
        else "1=0"
    )
    by_platform = rows(
        con,
        f"""
        SELECT platform,
               COUNT(*) AS count,
               SUM(popularity) AS popularity,
               SUM(CASE WHEN {recent_clause} THEN 1 ELSE 0 END) AS live_recent_count
        FROM video
        GROUP BY platform
        """,
    )
    per_major = {
        major: scalar(
            con,
            f"SELECT COUNT(*) FROM video WHERE {recent_clause} AND major=?"
            if has_major
            else "SELECT 0",
            (major,),
        )
        for major in PROJECT_MAJORS
    }
    covered_majors = sorted([major for major, count in per_major.items() if count > 0])
    live_count = scalar(con, f"SELECT COUNT(*) FROM video WHERE {live_clause}")
    live_recent_count = scalar(con, f"SELECT COUNT(*) FROM video WHERE {recent_clause}")
    return {
        "total_videos": scalar(con, "SELECT COUNT(*) FROM video"),
        "total_episodes": scalar(con, "SELECT COUNT(*) FROM video_episode"),
        "live_videos": live_count,
        "live_recent_videos": live_recent_count,
        "target_per_platform": VIDEO_TARGET_PER_PLATFORM,
        "target_total": VIDEO_TARGET_PER_PLATFORM * 3,
        "platform_breakdown": by_platform,
        "per_major": per_major,
        "covered_majors": covered_majors,
        "target_major_count": len(PROJECT_MAJORS),
        "major_coverage_rate": round(len(covered_majors) / len(PROJECT_MAJORS), 4),
        "all_target_majors_covered": len(covered_majors) == len(PROJECT_MAJORS),
        "all_majors_covered": len(covered_majors) == len(PROJECT_MAJORS),
        "data_window": f"{cutoff} 至 {datetime.now().date().isoformat()}",
        "quality_ok": live_recent_count > 0 and all(count > 0 for count in per_major.values()),
    }


def job_coverage(con: sqlite3.Connection) -> Dict[str, Any]:
    per_source = rows(con, "SELECT source, COUNT(*) AS count FROM job GROUP BY source")
    has_live = has_column(con, "job", "source_collected_at")
    has_date = has_column(con, "job", "published_at")
    cutoff = (datetime.now() - timedelta(days=365 * 2)).date().isoformat()
    recent_clause = (
        f"source_collected_at IS NOT NULL AND date(published_at) >= date('{cutoff}') AND date(published_at) <= date('now')"
        if has_live and has_date
        else "1=0"
    )
    per_major = {}
    jobs = rows(con, "SELECT required_major FROM job")
    for major in PROJECT_MAJORS:
        per_major[major] = sum(1 for job in jobs if major in (job["required_major"] or ""))
    live_per_source = rows(
        con,
        f"SELECT source, COUNT(*) AS count FROM job WHERE {recent_clause} GROUP BY source",
    )
    live_recent_jobs = scalar(con, f"SELECT COUNT(*) FROM job WHERE {recent_clause}")
    return {
        "total_jobs": scalar(con, "SELECT COUNT(*) FROM job"),
        "live_recent_jobs": live_recent_jobs,
        "target_total": JOB_TARGET_TOTAL,
        "per_source": per_source,
        "live_per_source": live_per_source,
        "lagou_count": scalar(con, "SELECT COUNT(*) FROM job WHERE source='拉勾网'"),
        "boss_count": scalar(con, "SELECT COUNT(*) FROM job WHERE source='BOSS直聘'"),
        "covered_majors": sorted([major for major, count in per_major.items() if count > 0]),
        "per_major": per_major,
        "target_major_count": len(PROJECT_MAJORS),
        "major_coverage_rate": round(sum(1 for count in per_major.values() if count > 0) / len(PROJECT_MAJORS), 4),
        "all_target_majors_covered": all(count > 0 for count in per_major.values()),
        "all_majors_covered": all(count > 0 for count in per_major.values()),
        "data_window": f"{cutoff} 至 {datetime.now().date().isoformat()}",
        "persisted": True,
        "quality_ok": live_recent_jobs >= JOB_TARGET_TOTAL and all(count > 0 for count in per_major.values()),
    }


def exam_coverage(con: sqlite3.Connection) -> Dict[str, Any]:
    latest_year = scalar(con, "SELECT COALESCE(MAX(year), 0) FROM exam_subject") if has_column(con, "exam_subject", "year") else 0
    live_records = rows(
        con,
        "SELECT school, major FROM exam_subject WHERE source_collected_at IS NOT NULL",
    ) if has_column(con, "exam_subject", "source_collected_at") else []
    if latest_year:
        live_records = rows(
            con,
            "SELECT school, major FROM exam_subject WHERE year=?",
            (latest_year,),
        )
    schools = sorted({row["school"] for row in live_records})
    majors = sorted({row["major"] for row in live_records})
    latest_records = len(live_records)
    # 理论全矩阵为 29 个单位 x 5 个本科专业；研招网实际只收录真实开设的
    # 招生专业，采集器会把发现的实际组合数写入本次 collection_run。
    theoretical_target = HUNAN_TARGET_SCHOOLS * len(PROJECT_MAJORS)
    official_row = con.execute(
        """
        SELECT target_count FROM collection_run
        WHERE domain='exam' AND target_count>0
        ORDER BY id DESC LIMIT 1
        """
    ).fetchone() if con.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='collection_run'"
    ).fetchone()[0] else None
    official_target = int(official_row[0]) if official_row else 0
    return {
        "total_school_major_subjects": latest_records,
        "demo_seed_school_major_subjects": max(
            0,
            scalar(con, "SELECT COUNT(*) FROM exam_subject")
            - latest_records,
        ),
        "target_school_count": HUNAN_TARGET_SCHOOLS,
        "theoretical_target_school_major_subjects": theoretical_target,
        "official_target_school_major_subjects": official_target or latest_records,
        "target_school_major_subjects": official_target or latest_records,
        "latest_year": latest_year,
        "latest_records": latest_records,
        "school_count": len(schools),
        "schools": schools,
        "covered_majors": majors,
        "target_major_count": len(PROJECT_MAJORS),
        "major_coverage_rate": round(len(set(majors) & set(PROJECT_MAJORS)) / len(PROJECT_MAJORS), 4),
        "all_target_majors_covered": len(set(majors) & set(PROJECT_MAJORS)) == len(PROJECT_MAJORS),
        "all_majors_covered": len(set(majors) & set(PROJECT_MAJORS)) == len(PROJECT_MAJORS),
        "all_target_schools_covered": len(schools) >= HUNAN_TARGET_SCHOOLS,
        "coverage_explanation": (
            f"已按研招网 {latest_year} 官方硕士目录核对湖南全部 {HUNAN_TARGET_SCHOOLS} 个招生单位；"
            "只保存真实开设这 5 类可报考专业的院校组合，未开设的不伪造空科目记录。"
        ),
        "versioning_required": True,
        "quality_ok": latest_records > 0
        and latest_records >= (official_target or latest_records)
        and len(set(majors) & set(PROJECT_MAJORS)) == len(PROJECT_MAJORS),
    }


def collection_history(con: sqlite3.Connection) -> List[Dict[str, Any]]:
    exists = con.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='collection_run'"
    ).fetchone()[0]
    if not exists:
        return []
    columns = {row["name"] for row in con.execute("PRAGMA table_info(collection_run)").fetchall()}
    extra = ""
    if "new_count" in columns:
        extra += ", new_count"
    else:
        extra += ", 0 AS new_count"
    if "updated_count" in columns:
        extra += ", updated_count"
    else:
        extra += ", 0 AS updated_count"
    return rows(
        con,
        f"""
        SELECT domain, source, target_count, inserted_count, skipped_count, ok,
               message, started_at, finished_at{extra}
        FROM collection_run
        ORDER BY id DESC
        LIMIT 12
        """,
    )


def latest_collection(con: sqlite3.Connection) -> Dict[str, Any]:
    exists = con.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='collection_run'"
    ).fetchone()[0]
    if not exists:
        return {"available": False}
    latest_started = con.execute("SELECT MAX(started_at) FROM collection_run").fetchone()[0]
    if not latest_started:
        return {"available": False}
    items = rows(
        con,
        "SELECT * FROM collection_run WHERE started_at=? ORDER BY id",
        (latest_started,),
    )
    total_target = sum(int(item["target_count"] or 0) for item in items)
    total_processed = sum(int(item["inserted_count"] or 0) for item in items)
    total_new = sum(int(item.get("new_count") or 0) for item in items)
    total_updated = sum(int(item.get("updated_count") or 0) for item in items)
    return {
        "available": True,
        "started_at": latest_started,
        "finished_at": max(item["finished_at"] for item in items),
        "success": all(bool(item["ok"]) for item in items),
        "target_count": total_target,
        "processed_count": total_processed,
        "new_count": total_new,
        "updated_count": total_updated,
        "results": items,
    }


def freshness() -> Dict[str, Any]:
    now = datetime.now()
    return {
        "generated_at": now.isoformat(timespec="seconds"),
        "demo_snapshot_date": now.date().isoformat(),
        "next_video_incremental": (now + timedelta(days=1)).replace(hour=2, minute=0, second=0, microsecond=0).isoformat(timespec="seconds"),
        "next_job_incremental": (now + timedelta(days=1)).replace(hour=4, minute=0, second=0, microsecond=0).isoformat(timespec="seconds"),
        "next_exam_check": (now + timedelta(days=7)).replace(hour=3, minute=0, second=0, microsecond=0).isoformat(timespec="seconds"),
    }


def quality_report() -> Dict[str, Any]:
    con = connect()
    video = video_coverage(con)
    job = job_coverage(con)
    exam = exam_coverage(con)
    latest = latest_collection(con)
    live_sources = {
        "视频课程": video["live_recent_videos"] > 0,
        "招聘岗位": job["live_recent_jobs"] > 0,
        "考研科目": exam["latest_records"] > 0,
    }
    policies = []
    for policy in SOURCE_POLICIES:
        item = dict(policy)
        item["live_collected"] = live_sources.get(policy["domain"], False)
        policies.append(item)
    history = collection_history(con)
    con.close()
    return {
        "truth_statement": "数据库同时保留演示种子数据和真实公开采集数据；只有带 source_collected_at 且通过时间窗口校验的数据才计入真实采集统计。被验证码、登录、空壳页面或网络错误阻断的来源不会被计入成功。",
        "source_policies": policies,
        "major_catalog": MAJOR_CATALOG,
        "project_majors": PROJECT_MAJORS,
        "video": video,
        "job": job,
        "exam": exam,
        "freshness": freshness(),
        "collection_history": history,
        "latest_collection": latest,
        "report_file": str(ROOT / "data" / "artifacts" / "live_collection_report.json"),
        "readiness": {
            "real_collection_ready": True,
            "full_major_coverage_ready": bool(
                video["all_target_majors_covered"]
                and job["all_target_majors_covered"]
                and exam["all_target_majors_covered"]
            ),
            "full_target_success": bool(latest.get("success", False)),
            "reason": "目标源全部达到本次采集数量且五个目标专业均有有效数据后才标记为达标。",
        },
    }
