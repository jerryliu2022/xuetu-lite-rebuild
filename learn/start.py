# -*- coding: utf-8 -*-
"""
一键启动「边跑边学」教学服务。

用法：
    python learn/start.py

启动后访问 http://127.0.0.1:8766

注意端口和主项目不同：
    8765 = 主项目本身（学途 Lite 正式服务）
    8766 = 本教学页面

两边互不干扰，教学页面只读主项目的数据库，
所有 SQL / Python 练习都跑在 learn/data/learn_test.db 这份副本上。
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
LEARN_ROOT = PROJECT_ROOT / "learn"

sys.path.insert(0, str(LEARN_ROOT))
sys.path.insert(0, str(PROJECT_ROOT))

PORT = 8766


def main() -> None:
    import uvicorn

    print("=" * 60)
    print("  学途 Lite · 边跑边学 教学服务")
    print("=" * 60)
    print(f"  教学页面   http://127.0.0.1:{PORT}")
    print(f"  主项目     http://127.0.0.1:8765   （后端示例依赖它，请先启动）")
    print()
    print("  主项目启动命令：")
    print("     python -m uvicorn backend.app:app --host 127.0.0.1 --port 8765")
    print("=" * 60)

    uvicorn.run(
        "server.app:app",
        app_dir=str(LEARN_ROOT),
        host="127.0.0.1",
        port=PORT,
        reload=False,
    )


if __name__ == "__main__":
    main()
