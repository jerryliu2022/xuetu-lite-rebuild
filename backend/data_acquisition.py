from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List

ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data" / "raw"
DB_PATH = ROOT / "data" / "processed" / "xuetu_lite.db"


def seed_payload() -> Dict[str, List[Dict[str, Any]]]:
    """Competition-friendly public-source seed data.

    The import functions deliberately use a source/source_url shape so real
    MOOC, Bilibili, job-board, and graduate-admission exports can replace this
    seed without changing the downstream database or recommender pipeline.
    """
    now = datetime.now()
    return {
        "students": [
            {
                "student_id": "20240101",
                "name": "李明",
                "school": "江城大学",
                "major": "计算机科学与技术",
                "grade": "大二",
                "semester": 3,
                "level": 6,
                "xp": 1280,
                "next_xp": 1500,
            },
            {
                "student_id": "20240102",
                "name": "周然",
                "school": "江城大学",
                "major": "软件工程",
                "grade": "大三",
                "semester": 5,
                "level": 7,
                "xp": 1640,
                "next_xp": 2100,
            },
            {
                "student_id": "20240103",
                "name": "陈思",
                "school": "江城大学",
                "major": "数据科学与大数据技术",
                "grade": "大二",
                "semester": 3,
                "level": 5,
                "xp": 930,
                "next_xp": 1200,
            },
        ],
        "courses": [
            {"course_id": "C101", "name": "高等数学", "semester": 1, "credits": 5, "status": "completed", "concepts": "函数 极限 微积分 线性空间", "prerequisites": ""},
            {"course_id": "C102", "name": "线性代数", "semester": 1, "credits": 3, "status": "completed", "concepts": "矩阵 向量 特征值 线性变换", "prerequisites": ""},
            {"course_id": "C201", "name": "C 语言程序设计", "semester": 2, "credits": 4, "status": "completed", "concepts": "变量 指针 函数 程序设计", "prerequisites": ""},
            {"course_id": "C202", "name": "离散数学", "semester": 2, "credits": 3, "status": "completed", "concepts": "集合 图论 逻辑 组合数学", "prerequisites": ""},
            {"course_id": "C301", "name": "数据结构", "semester": 3, "credits": 4, "status": "studying", "concepts": "数组 链表 树 图 算法复杂度", "prerequisites": "C201,C202"},
            {"course_id": "C302", "name": "概率论与数理统计", "semester": 3, "credits": 3, "status": "studying", "concepts": "概率 分布 统计 推断", "prerequisites": "C101"},
            {"course_id": "C401", "name": "操作系统", "semester": 4, "credits": 4, "status": "planned", "concepts": "进程 线程 内存 文件系统 调度", "prerequisites": "C301"},
            {"course_id": "C402", "name": "计算机网络", "semester": 4, "credits": 3, "status": "planned", "concepts": "TCP IP HTTP 路由 网络协议", "prerequisites": "C301"},
            {"course_id": "C501", "name": "数据库系统", "semester": 5, "credits": 4, "status": "planned", "concepts": "SQL 索引 事务 MySQL 数据建模", "prerequisites": "C301"},
            {"course_id": "C502", "name": "机器学习", "semester": 5, "credits": 3, "status": "planned", "concepts": "监督学习 特征工程 推荐系统 模型评估", "prerequisites": "C102,C302,C301"},
            {"course_id": "C601", "name": "软件工程", "semester": 6, "credits": 3, "status": "planned", "concepts": "需求 设计 测试 敏捷 项目管理", "prerequisites": "C301"},
            {"course_id": "C602", "name": "Java Web 开发", "semester": 6, "credits": 3, "status": "planned", "concepts": "Java Spring Boot Redis MySQL 后端开发", "prerequisites": "C301,C501"},
        ],
        "videos": [
            {"video_id": "V001", "course_id": "C101", "title": "高等数学・同济版全程精讲", "platform": "MOOC", "is_paid": 0, "teacher": "数学汤老师", "org": "中国大学MOOC", "popularity": 125000, "rating": 4.8, "episodes": 24, "duration": 1540, "tags": "高等数学 极限 微积分 同济", "summary": "覆盖函数、极限、导数和积分，适合课内同步与考研打底。", "source_url": "https://www.icourse163.org/", "cover_color": "#4f7cff"},
            {"video_id": "V002", "course_id": "C301", "title": "数据结构与算法・图解与实战", "platform": "B站", "is_paid": 0, "teacher": "码农林克", "org": "Bilibili", "popularity": 483000, "rating": 4.9, "episodes": 36, "duration": 1880, "tags": "数据结构 算法 链表 树 图", "summary": "用动画和刷题案例讲解核心数据结构，适合正在学习数据结构的学生。", "source_url": "https://www.bilibili.com/", "cover_color": "#884df0"},
            {"video_id": "V003", "course_id": "C502", "title": "机器学习入门・李宏毅 2024 完整版", "platform": "极客时间", "is_paid": 1, "teacher": "李宏毅", "org": "极客时间", "popularity": 52000, "rating": 4.7, "episodes": 42, "duration": 2020, "tags": "机器学习 深度学习 推荐系统 特征工程", "summary": "从回归、分类到神经网络与推荐模型，适合作为进阶课。", "source_url": "https://time.geekbang.org/", "cover_color": "#119caf"},
            {"video_id": "V004", "course_id": "C401", "title": "操作系统原理・下学期的预习课", "platform": "MOOC", "is_paid": 0, "teacher": "谢希仁团队", "org": "南京大学", "popularity": 89000, "rating": 4.6, "episodes": 28, "duration": 1320, "tags": "操作系统 进程 内存 调度 文件系统", "summary": "提前建立操作系统知识框架，承接数据结构和计算机组成。", "source_url": "https://www.icourse163.org/", "cover_color": "#f49a13"},
            {"video_id": "V005", "course_id": "C402", "title": "计算机网络・谢希仁第七版精讲", "platform": "B站", "is_paid": 0, "teacher": "网络小航", "org": "Bilibili", "popularity": 217000, "rating": 4.8, "episodes": 31, "duration": 1450, "tags": "计算机网络 TCP IP HTTP 路由", "summary": "围绕协议栈拆解网络原理，适合后续 Web 与后端方向。", "source_url": "https://www.bilibili.com/", "cover_color": "#0fb889"},
            {"video_id": "V006", "course_id": "C501", "title": "数据库系统・从原理到实战进阶", "platform": "极客时间", "is_paid": 1, "teacher": "丁奇", "org": "极客时间", "popularity": 34000, "rating": 4.7, "episodes": 45, "duration": 1680, "tags": "数据库 SQL MySQL 索引 事务", "summary": "强调索引、事务和查询优化，适合就业方向补强。", "source_url": "https://time.geekbang.org/", "cover_color": "#eb3f83"},
            {"video_id": "V007", "course_id": "C602", "title": "Java 核心编程・就业班", "platform": "MOOC", "is_paid": 0, "teacher": "韩老师", "org": "中国大学MOOC", "popularity": 101000, "rating": 4.6, "episodes": 32, "duration": 1580, "tags": "Java 面向对象 集合 并发 JVM", "summary": "系统补齐 Java 基础、集合、异常和并发。", "source_url": "https://www.icourse163.org/", "cover_color": "#d64b40"},
            {"video_id": "V008", "course_id": "C602", "title": "Spring Boot 企业级实战", "platform": "极客时间", "is_paid": 1, "teacher": "小马哥", "org": "极客时间", "popularity": 69000, "rating": 4.8, "episodes": 30, "duration": 1410, "tags": "Java Spring Boot Redis MySQL 后端开发", "summary": "从 API、ORM、缓存到部署，映射后端岗位技能。", "source_url": "https://time.geekbang.org/", "cover_color": "#8857f2"},
            {"video_id": "V009", "course_id": "C501", "title": "MySQL 数据库进阶", "platform": "MOOC", "is_paid": 0, "teacher": "周铭", "org": "浙江大学", "popularity": 76000, "rating": 4.5, "episodes": 22, "duration": 960, "tags": "MySQL 索引 SQL 事务 优化", "summary": "围绕真实业务 SQL、索引和事务隔离展开。", "source_url": "https://www.icourse163.org/", "cover_color": "#2fc99d"},
            {"video_id": "V010", "course_id": "C601", "title": "前端工程化与项目实战", "platform": "B站", "is_paid": 0, "teacher": "前端阿凯", "org": "Bilibili", "popularity": 146000, "rating": 4.6, "episodes": 26, "duration": 1160, "tags": "前端 Vue React TypeScript 工程化", "summary": "面向前端岗位的工程实践课程。", "source_url": "https://www.bilibili.com/", "cover_color": "#4b75f5"},
            {"video_id": "V011", "course_id": "C502", "title": "推荐系统实战：召回、排序与评估", "platform": "极客时间", "is_paid": 1, "teacher": "王喆", "org": "极客时间", "popularity": 58000, "rating": 4.9, "episodes": 34, "duration": 1500, "tags": "推荐系统 召回 排序 Embedding CTR", "summary": "专注推荐工程链路，适合本项目答辩技术深度。", "source_url": "https://time.geekbang.org/", "cover_color": "#2f63dc"},
            {"video_id": "V012", "course_id": "C302", "title": "概率统计・机器学习基础", "platform": "MOOC", "is_paid": 0, "teacher": "陈教授", "org": "复旦大学", "popularity": 83000, "rating": 4.4, "episodes": 20, "duration": 860, "tags": "概率 统计 分布 机器学习 基础", "summary": "为机器学习和数据分析方向补齐数学前置知识。", "source_url": "https://www.icourse163.org/", "cover_color": "#18a799"},
            {"video_id": "V013", "course_id": "C202", "title": "离散数学与图论速通", "platform": "B站", "is_paid": 0, "teacher": "图论派", "org": "Bilibili", "popularity": 111000, "rating": 4.5, "episodes": 18, "duration": 720, "tags": "离散数学 图论 逻辑 算法", "summary": "用图论案例连接数据结构与算法。", "source_url": "https://www.bilibili.com/", "cover_color": "#f2ae3f"},
            {"video_id": "V014", "course_id": "C101", "title": "考研数学一・基础强化", "platform": "B站", "is_paid": 0, "teacher": "张宇团队", "org": "Bilibili", "popularity": 412000, "rating": 4.7, "episodes": 60, "duration": 2200, "tags": "考研 数学一 高数 线代 概率", "summary": "覆盖数学一公共课基础阶段。", "source_url": "https://www.bilibili.com/", "cover_color": "#4f79ee"},
            {"video_id": "V015", "course_id": "C301", "title": "数据结构・408 统考", "platform": "MOOC", "is_paid": 0, "teacher": "王道团队", "org": "中国大学MOOC", "popularity": 228000, "rating": 4.8, "episodes": 40, "duration": 1760, "tags": "考研 408 数据结构 算法", "summary": "面向 408 计算机统考的数据结构专题。", "source_url": "https://www.icourse163.org/", "cover_color": "#864fec"},
            {"video_id": "V016", "course_id": "C401", "title": "操作系统・408 统考", "platform": "极客时间", "is_paid": 1, "teacher": "考研陈哥", "org": "极客时间", "popularity": 69000, "rating": 4.4, "episodes": 35, "duration": 1350, "tags": "考研 408 操作系统 进程 内存", "summary": "面向计算机考研的操作系统高频考点。", "source_url": "https://time.geekbang.org/", "cover_color": "#e48610"},
            {"video_id": "V017", "course_id": "C402", "title": "计算机网络・408 统考", "platform": "B站", "is_paid": 0, "teacher": "王道团队", "org": "Bilibili", "popularity": 195000, "rating": 4.6, "episodes": 36, "duration": 1280, "tags": "考研 408 计算机网络 TCP IP", "summary": "按 408 题型讲解计算机网络。", "source_url": "https://www.bilibili.com/", "cover_color": "#18b988"},
            {"video_id": "V018", "course_id": "C201", "title": "考研政治・全程班", "platform": "B站", "is_paid": 0, "teacher": "肖老师", "org": "Bilibili", "popularity": 321000, "rating": 4.5, "episodes": 48, "duration": 1780, "tags": "考研 政治 公共课 马原 毛中特", "summary": "公共考研课，适合默认考研路径起步。", "source_url": "https://www.bilibili.com/", "cover_color": "#df4b3f"},
            {"video_id": "V019", "course_id": "C102", "title": "考研英语・词汇与长难句", "platform": "MOOC", "is_paid": 0, "teacher": "唐老师", "org": "中国大学MOOC", "popularity": 186000, "rating": 4.5, "episodes": 32, "duration": 980, "tags": "考研 英语 词汇 长难句 公共课", "summary": "英语公共课词汇和阅读基础。", "source_url": "https://www.icourse163.org/", "cover_color": "#18a89b"},
        ],
        "jobs": [
            {"job_id": "J001", "title": "Java 后端开发工程师", "company": "腾讯科技", "city": "深圳", "salary": "18-28K", "fresh": "14薪", "major_match": "高度匹配", "required_major": "计算机科学与技术,软件工程", "skills": "Java,Spring Boot,MySQL,Redis,数据结构与算法", "requirement": "熟悉 Java 基础、Spring Boot、MySQL 索引与事务，具备良好的数据结构与算法能力。", "source": "拉勾网", "source_url": "https://www.lagou.com/"},
            {"job_id": "J002", "title": "数据分析师", "company": "字节跳动", "city": "北京", "salary": "15-25K", "fresh": "13薪", "major_match": "一般匹配", "required_major": "统计学,计算机科学与技术,数据科学与大数据技术", "skills": "SQL,Python,统计分析,机器学习,可视化", "requirement": "理解业务指标，熟练使用 SQL 和统计方法，能够构建基础预测模型。", "source": "BOSS直聘", "source_url": "https://www.zhipin.com/"},
            {"job_id": "J003", "title": "前端开发工程师", "company": "美团", "city": "上海", "salary": "16-26K", "fresh": "14薪", "major_match": "相关延伸", "required_major": "计算机科学与技术,软件工程", "skills": "JavaScript,TypeScript,React,工程化,计算机网络", "requirement": "掌握现代前端框架、工程化工具链与 HTTP 网络基础。", "source": "拉勾网", "source_url": "https://www.lagou.com/"},
            {"job_id": "J004", "title": "推荐算法实习生", "company": "快手", "city": "北京", "salary": "300-500/天", "fresh": "实习", "major_match": "高度匹配", "required_major": "计算机科学与技术,数据科学与大数据技术", "skills": "机器学习,推荐系统,特征工程,数据结构与算法,概率统计", "requirement": "理解召回、排序、Embedding、CTR 预估，能完成离线实验和线上指标分析。", "source": "牛客网", "source_url": "https://www.nowcoder.com/"},
        ],
        "skill_course_map": [
            {"skill": "Java", "video_id": "V007", "prerequisite_skill": ""},
            {"skill": "Spring Boot", "video_id": "V008", "prerequisite_skill": "Java"},
            {"skill": "MySQL", "video_id": "V009", "prerequisite_skill": "数据结构与算法"},
            {"skill": "Redis", "video_id": "V008", "prerequisite_skill": "Java"},
            {"skill": "数据结构与算法", "video_id": "V002", "prerequisite_skill": ""},
            {"skill": "SQL", "video_id": "V009", "prerequisite_skill": ""},
            {"skill": "统计分析", "video_id": "V012", "prerequisite_skill": ""},
            {"skill": "机器学习", "video_id": "V003", "prerequisite_skill": "概率统计,数据结构与算法"},
            {"skill": "推荐系统", "video_id": "V011", "prerequisite_skill": "机器学习"},
            {"skill": "特征工程", "video_id": "V011", "prerequisite_skill": "机器学习"},
            {"skill": "概率统计", "video_id": "V012", "prerequisite_skill": ""},
            {"skill": "React", "video_id": "V010", "prerequisite_skill": "JavaScript"},
            {"skill": "TypeScript", "video_id": "V010", "prerequisite_skill": "JavaScript"},
            {"skill": "工程化", "video_id": "V010", "prerequisite_skill": ""},
            {"skill": "计算机网络", "video_id": "V005", "prerequisite_skill": "数据结构与算法"},
        ],
        "exam_subjects": [
            {"school": "默认", "major": "计算机科学与技术", "subjects": "考研政治,考研英语,考研数学一,408 计算机统考"},
            {"school": "清华大学", "major": "计算机科学与技术", "subjects": "考研政治,考研英语,考研数学一,912 计算机专业基础"},
            {"school": "浙江大学", "major": "计算机科学与技术", "subjects": "考研政治,考研英语,考研数学一,408 计算机统考"},
            {"school": "南京大学", "major": "软件工程", "subjects": "考研政治,考研英语,考研数学二,842 数据结构与软件工程"},
        ],
        "learning_records": [
            {"student_id": "20240101", "video_id": "V001", "watched_episodes": 24, "progress": 1.0, "status": "completed"},
            {"student_id": "20240101", "video_id": "V002", "watched_episodes": 12, "progress": 0.33, "status": "learning"},
            {"student_id": "20240101", "video_id": "V005", "watched_episodes": 2, "progress": 0.06, "status": "learning"},
            {"student_id": "20240101", "video_id": "V012", "watched_episodes": 20, "progress": 1.0, "status": "completed"},
            {"student_id": "20240102", "video_id": "V008", "watched_episodes": 21, "progress": 0.7, "status": "learning"},
            {"student_id": "20240103", "video_id": "V011", "watched_episodes": 10, "progress": 0.29, "status": "learning"},
        ],
        "behavior_log": [
            {"student_id": "20240101", "event_type": "complete", "video_id": "V001", "duration": 1540, "created_at": (now - timedelta(days=22)).isoformat()},
            {"student_id": "20240101", "event_type": "like", "video_id": "V001", "duration": 0, "created_at": (now - timedelta(days=21)).isoformat()},
            {"student_id": "20240101", "event_type": "play", "video_id": "V002", "duration": 620, "created_at": (now - timedelta(days=5)).isoformat()},
            {"student_id": "20240101", "event_type": "favorite", "video_id": "V002", "duration": 0, "created_at": (now - timedelta(days=4)).isoformat()},
            {"student_id": "20240101", "event_type": "complete", "video_id": "V012", "duration": 860, "created_at": (now - timedelta(days=9)).isoformat()},
            {"student_id": "20240102", "event_type": "play", "video_id": "V008", "duration": 880, "created_at": (now - timedelta(days=3)).isoformat()},
            {"student_id": "20240103", "event_type": "play", "video_id": "V011", "duration": 740, "created_at": (now - timedelta(days=2)).isoformat()},
        ],
    }


def write_seed_files() -> None:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    for name, rows in seed_payload().items():
        (RAW_DIR / f"{name}.json").write_text(
            json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8"
        )


def load_rows(name: str) -> List[Dict[str, Any]]:
    path = RAW_DIR / f"{name}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def execute_many(con: sqlite3.Connection, sql: str, rows: Iterable[Dict[str, Any]]) -> None:
    rows = list(rows)
    if rows:
        con.executemany(sql, rows)


def reset_database() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    if DB_PATH.exists():
        DB_PATH.unlink()
    con = sqlite3.connect(DB_PATH)
    con.execute("PRAGMA foreign_keys = ON")
    con.executescript(
        """
        CREATE TABLE student (
          student_id TEXT PRIMARY KEY,
          name TEXT NOT NULL,
          school TEXT NOT NULL,
          major TEXT NOT NULL,
          grade TEXT NOT NULL,
          semester INTEGER NOT NULL,
          level INTEGER NOT NULL,
          xp INTEGER NOT NULL,
          next_xp INTEGER NOT NULL
        );
        CREATE TABLE course (
          course_id TEXT PRIMARY KEY,
          name TEXT NOT NULL,
          semester INTEGER NOT NULL,
          credits REAL,
          status TEXT NOT NULL,
          concepts TEXT,
          prerequisites TEXT
        );
        CREATE TABLE video (
          video_id TEXT PRIMARY KEY,
          course_id TEXT NOT NULL,
          title TEXT NOT NULL,
          platform TEXT NOT NULL,
          is_paid INTEGER NOT NULL,
          teacher TEXT,
          org TEXT,
          popularity INTEGER NOT NULL,
          rating REAL NOT NULL,
          episodes INTEGER NOT NULL,
          duration INTEGER NOT NULL,
          tags TEXT,
          summary TEXT,
          source_url TEXT,
          cover_color TEXT,
          FOREIGN KEY(course_id) REFERENCES course(course_id)
        );
        CREATE TABLE video_episode (
          episode_id TEXT PRIMARY KEY,
          video_id TEXT NOT NULL,
          episode_no INTEGER NOT NULL,
          title TEXT NOT NULL,
          duration TEXT NOT NULL,
          play_url TEXT NOT NULL,
          FOREIGN KEY(video_id) REFERENCES video(video_id)
        );
        CREATE TABLE learning_record (
          student_id TEXT NOT NULL,
          video_id TEXT NOT NULL,
          watched_episodes INTEGER NOT NULL,
          progress REAL NOT NULL,
          status TEXT NOT NULL,
          PRIMARY KEY(student_id, video_id)
        );
        CREATE TABLE achievement (
          student_id TEXT NOT NULL,
          video_id TEXT NOT NULL,
          completed_at TEXT NOT NULL,
          PRIMARY KEY(student_id, video_id)
        );
        CREATE TABLE behavior_log (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          student_id TEXT NOT NULL,
          event_type TEXT NOT NULL,
          video_id TEXT,
          duration INTEGER DEFAULT 0,
          created_at TEXT NOT NULL
        );
        CREATE TABLE job (
          job_id TEXT PRIMARY KEY,
          title TEXT NOT NULL,
          company TEXT NOT NULL,
          city TEXT NOT NULL,
          salary TEXT NOT NULL,
          fresh TEXT,
          major_match TEXT NOT NULL,
          required_major TEXT,
          skills TEXT,
          requirement TEXT,
          source TEXT,
          source_url TEXT
        );
        CREATE TABLE skill_course_map (
          skill TEXT NOT NULL,
          video_id TEXT NOT NULL,
          prerequisite_skill TEXT,
          PRIMARY KEY(skill, video_id)
        );
        CREATE TABLE exam_subject (
          school TEXT NOT NULL,
          major TEXT NOT NULL,
          subjects TEXT NOT NULL,
          PRIMARY KEY(school, major)
        );
        CREATE TABLE recommend_log (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          student_id TEXT NOT NULL,
          scenario TEXT NOT NULL,
          candidate_id TEXT NOT NULL,
          impression INTEGER DEFAULT 1,
          click INTEGER DEFAULT 0,
          created_at TEXT NOT NULL
        );
        """
    )

    execute_many(con, "INSERT INTO student VALUES (:student_id,:name,:school,:major,:grade,:semester,:level,:xp,:next_xp)", load_rows("students"))
    execute_many(con, "INSERT INTO course VALUES (:course_id,:name,:semester,:credits,:status,:concepts,:prerequisites)", load_rows("courses"))
    execute_many(
        con,
        "INSERT INTO video VALUES (:video_id,:course_id,:title,:platform,:is_paid,:teacher,:org,:popularity,:rating,:episodes,:duration,:tags,:summary,:source_url,:cover_color)",
        load_rows("videos"),
    )
    episodes = []
    for video in load_rows("videos"):
        for idx in range(1, video["episodes"] + 1):
            title_prefix = video["title"].split("・")[0]
            episodes.append(
                {
                    "episode_id": f"{video['video_id']}-{idx:02d}",
                    "video_id": video["video_id"],
                    "episode_no": idx,
                    "title": f"{title_prefix} 第 {idx:02d} 讲",
                    "duration": f"{42 + idx % 12}:{(idx * 7) % 60:02d}",
                    "play_url": f"https://player.bilibili.com/player.html?bvid=BV1xx411c7mD&page={idx}",
                }
            )
    execute_many(con, "INSERT INTO video_episode VALUES (:episode_id,:video_id,:episode_no,:title,:duration,:play_url)", episodes)
    execute_many(con, "INSERT INTO learning_record VALUES (:student_id,:video_id,:watched_episodes,:progress,:status)", load_rows("learning_records"))
    complete_time = datetime.now().isoformat()
    achievements = [
        {"student_id": row["student_id"], "video_id": row["video_id"], "completed_at": complete_time}
        for row in load_rows("learning_records")
        if row["status"] == "completed"
    ]
    execute_many(con, "INSERT INTO achievement VALUES (:student_id,:video_id,:completed_at)", achievements)
    execute_many(con, "INSERT INTO behavior_log (student_id,event_type,video_id,duration,created_at) VALUES (:student_id,:event_type,:video_id,:duration,:created_at)", load_rows("behavior_log"))
    execute_many(con, "INSERT INTO job VALUES (:job_id,:title,:company,:city,:salary,:fresh,:major_match,:required_major,:skills,:requirement,:source,:source_url)", load_rows("jobs"))
    execute_many(con, "INSERT INTO skill_course_map VALUES (:skill,:video_id,:prerequisite_skill)", load_rows("skill_course_map"))
    execute_many(con, "INSERT INTO exam_subject VALUES (:school,:major,:subjects)", load_rows("exam_subjects"))

    # Exposure/click labels are derived from seeded interactions to bootstrap the ranker.
    videos = [row["video_id"] for row in load_rows("videos")]
    positive = {(row["student_id"], row["video_id"]) for row in load_rows("learning_records")}
    rec_logs = []
    for student in load_rows("students"):
        for video_id in videos:
            rec_logs.append(
                {
                    "student_id": student["student_id"],
                    "scenario": "professional",
                    "candidate_id": video_id,
                    "impression": 1,
                    "click": 1 if (student["student_id"], video_id) in positive else 0,
                    "created_at": datetime.now().isoformat(),
                }
            )
    execute_many(con, "INSERT INTO recommend_log (student_id,scenario,candidate_id,impression,click,created_at) VALUES (:student_id,:scenario,:candidate_id,:impression,:click,:created_at)", rec_logs)
    con.commit()
    con.close()


def main() -> None:
    write_seed_files()
    reset_database()
    print(f"seeded raw data in {RAW_DIR}")
    print(f"created database at {DB_PATH}")


if __name__ == "__main__":
    main()
