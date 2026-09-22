# -*- coding: utf-8 -*-
"""批量验证 learn 模块里所有「可执行内容」是否真的能跑出正确结果。

用法：
    python learn/_verify.py
"""

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from server.db_sandbox import PRESET_SQL, ensure_test_db, execute_sql  # noqa: E402
from server.code_runner import PRESET_CODE, run_code  # noqa: E402
from server import models_playground  # noqa: E402


def line(text=""):
    print(text)


def check_sql():
    line("=" * 70)
    line("【1】预置 SQL 逐条验证（在练习库副本上执行）")
    line("=" * 70)
    ensure_test_db(force=True)
    ok = fail = 0
    for item in PRESET_SQL:
        r = execute_sql(item["sql"])
        if r["ok"]:
            rows = r.get("rowcount", 0)
            line(f"  [OK] {item['id']:<18} {r['message'][:44]:<46} rows={rows}")
            ok += 1
        else:
            line(f"  [FAIL] {item['id']:<18} {r['message'][:90]}")
            fail += 1
    line(f"  ---> SQL: 成功 {ok} / 失败 {fail}")
    return fail


def check_code():
    line()
    line("=" * 70)
    line("【2】预置 Python 代码逐条验证")
    line("=" * 70)
    ok = fail = 0
    for item in PRESET_CODE:
        t0 = time.time()
        r = run_code(item["code"])
        ms = round((time.time() - t0) * 1000)
        if r["ok"]:
            preview = json.dumps(r["result"], ensure_ascii=False)[:70] if r["result"] is not None else "(无返回值)"
            line(f"  [OK] {item['id']:<20} {ms:>5}ms  result={preview}")
            ok += 1
        else:
            line(f"  [FAIL] {item['id']:<20} {r['stage']} :: {r['error'][:90]}")
            fail += 1
        # 每段代码都执行两次，验证幂等性（再跑一次不该出错）
        r2 = run_code(item["code"])
        if not r2["ok"]:
            line(f"  [不幂等!] {item['id']} 第二次执行失败：{r2['error'][:70]}")
            fail += 1
    line(f"  ---> CODE: 成功 {ok} / 失败 {fail}")
    return fail


def check_models():
    line()
    line("=" * 70)
    line("【3】推荐模型逐个验证")
    line("=" * 70)
    fail = 0
    for entry in models_playground.MODELS:
        t0 = time.time()
        try:
            out = models_playground.run_model(entry["key"], "YY08", 9)
            ms = round((time.time() - t0) * 1000)
            m = out["metrics"]
            top1 = out["items"][0]["title"][:22] if out["items"] else "(空)"
            line(f"  [OK] L{entry['level']} {entry['name'][:20]:<22} {ms:>6}ms  "
                 f"P@5={m['precision@5']:<7} NDCG@5={m['ndcg@5']:<7} 多样性={m['diversity']:<7} top1={top1}")
            if not out["items"]:
                line("       [警告] 推荐结果为空")
                fail += 1
        except Exception as exc:  # noqa: BLE001
            import traceback
            line(f"  [FAIL] {entry['name']} :: {exc}")
            line(traceback.format_exc()[-500:])
            fail += 1
    return fail


def check_students():
    """换不同学生再跑一次，确保不是写死的。"""
    line()
    line("=" * 70)
    line("【4】切换不同学生验证推荐结果是否真的不同")
    line("=" * 70)
    data = models_playground.load_data()
    fail = 0
    seen_top1 = set()
    for sid in ["YY08", "YY09", "YY10", "YY11", "YY12"]:
        try:
            out = models_playground.model_curriculum(data, sid, 9)
            top = out["items"][:3]
            titles = [t["title"][:14] for t in top]
            seen_top1.add(titles[0] if titles else "")
            line(f"  [OK] {sid} -> {titles}")
            if not top:
                fail += 1
        except Exception as exc:  # noqa: BLE001
            line(f"  [FAIL] {sid} :: {exc}")
            fail += 1
    line(f"  ---> 5 个学生的 Top1 去重后有 {len(seen_top1)} 种（>1 说明确实个性化了）")
    return fail


def check_sandbox_security():
    """验证沙箱确实拦得住危险操作。"""
    line()
    line("=" * 70)
    line("【5】沙箱安全检查")
    line("=" * 70)
    cases = [
        ("禁止 import os", "import os\nprint(os.getcwd())"),
        ("禁止 import subprocess", "import subprocess"),
        ("禁止 open 写文件", "open('x.txt','w')"),
        ("禁止 DROP TABLE", None),
        ("禁止 eval", "eval('1+1')"),
        ("禁止 import socket", "import socket\nsocket.socket()"),
    ]
    fail = 0
    for name, code in cases:
        if code is None:
            r = execute_sql("DROP TABLE video")
            blocked = (not r["ok"]) and ("不允许" in r["message"] or "sqlite" in r["message"].lower())
            line(f"  [{'OK' if blocked else 'FAIL'}] {name:<22} -> {r['message'][:60]}")
            if not blocked:
                fail += 1
            continue
        r = run_code(code)
        blocked = not r["ok"]
        line(f"  [{'OK' if blocked else 'FAIL'}] {name:<22} -> {(r['error'] or '未被拦截!')[:60]}")
        if not blocked:
            fail += 1
    return fail


if __name__ == "__main__":
    total = 0
    total += check_sql()
    total += check_code()
    total += check_models()
    total += check_students()
    total += check_sandbox_security()
    line()
    line("=" * 70)
    line(f"总失败数：{total}   （0 表示全部通过）")
    line("=" * 70)
    sys.exit(1 if total else 0)
