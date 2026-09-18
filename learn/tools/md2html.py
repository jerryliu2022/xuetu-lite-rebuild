#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
把 learn/docs/ 下的 Markdown 文档转成带样式的独立 HTML 页面。

为什么不直接 pip install markdown：
    这台机器的 Python 环境是项目在用的，不想为了一个转换动作往里面装包。
    而且本文档用到的 Markdown 语法很规整（标题/代码块/表格/列表/引用/行内格式），
    自己解析这一小撮语法完全够用，还顺带能把「源码:行号」这类内容自动加上样式。

用法：
    python learn/tools/md2html.py                      # 转换 docs 下全部 .md
    python learn/tools/md2html.py 文件名.md             # 只转指定文件
输出：
    learn/static/docs/<文件名>.html
"""

import html
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOCS_DIR = ROOT / "learn" / "docs"
OUT_DIR = ROOT / "learn" / "static" / "docs"


def esc(text: str) -> str:
    """转义 HTML 特殊字符，防止文档里的 < > & 破坏页面结构。"""
    return html.escape(text, quote=False)


def inline(text: str) -> str:
    """处理行内格式：`代码`、**粗体**、[链接](url)。

    技巧：先把行内代码抠出来存进列表，用占位符替换，
    等整体转义完再放回去——否则代码里的 < > 会被当标签转义掉。
    """
    codes = []

    def stash(match):
        codes.append(match.group(1))
        return f"\x00{len(codes) - 1}\x00"

    text = re.sub(r"`([^`]+)`", stash, text)
    text = esc(text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', text)

    def unstash(match):
        return "<code>" + esc(codes[int(match.group(1))]) + "</code>"

    return re.sub(r"\x00(\d+)\x00", unstash, text)


def slugify(text: str) -> str:
    """从标题文本生成锚点 id，跟 Markdown 的目录链接规则保持一致。

    GitHub 风格：转小写 → 去掉非字母数字汉字 → 空格换连字符。
    """
    text = re.sub(r"`", "", text)
    text = text.strip().lower()
    text = re.sub(r"[^\w\u4e00-\u9fff\s-]", "", text)
    return re.sub(r"\s+", "-", text)


def convert(md_text: str) -> str:
    lines = md_text.split("\n")
    out = []
    i = 0
    n = len(lines)

    while i < n:
        line = lines[i]

        # ---- 代码块 ----
        if line.startswith("```"):
            lang = line[3:].strip()
            body = []
            i += 1
            while i < n and not lines[i].startswith("```"):
                body.append(lines[i])
                i += 1
            i += 1  # 跳过结束的 ```
            cls = f' class="lang-{esc(lang)}"' if lang else ""
            out.append(f"<pre><code{cls}>{esc(chr(10).join(body))}</code></pre>")
            continue

        # ---- 分隔线 ----
        if re.match(r"^-{3,}$", line.strip()):
            out.append("<hr>")
            i += 1
            continue

        # ---- 标题 ----
        m = re.match(r"^(#{1,4})\s+(.*)$", line)
        if m:
            level = len(m.group(1))
            text = m.group(2).strip()
            anchor = slugify(text)
            out.append(f'<h{level} id="{anchor}">{inline(text)}</h{level}>')
            i += 1
            continue

        # ---- 表格：连续以 | 开头的行 ----
        if line.strip().startswith("|"):
            block = []
            while i < n and lines[i].strip().startswith("|"):
                block.append(lines[i].strip())
                i += 1
            out.append(render_table(block))
            continue

        # ---- 引用块：连续的 > 行合并 ----
        if line.strip().startswith(">"):
            block = []
            while i < n and lines[i].strip().startswith(">"):
                block.append(lines[i].strip()[1:].strip())
                i += 1
            out.append("<blockquote>" + inline(" ".join(block)) + "</blockquote>")
            continue

        # ---- 列表 ----
        if re.match(r"^\s*[-*]\s+", line):
            items = []
            while i < n and re.match(r"^\s*[-*]\s+", lines[i]):
                items.append(re.sub(r"^\s*[-*]\s+", "", lines[i]))
                i += 1
            out.append("<ul>" + "".join(f"<li>{inline(x)}</li>" for x in items) + "</ul>")
            continue

        if re.match(r"^\s*\d+\.\s+", line):
            items = []
            while i < n and re.match(r"^\s*\d+\.\s+", lines[i]):
                items.append(re.sub(r"^\s*\d+\.\s+", "", lines[i]))
                i += 1
            out.append("<ol>" + "".join(f"<li>{inline(x)}</li>" for x in items) + "</ol>")
            continue

        # ---- 空行 ----
        if not line.strip():
            i += 1
            continue

        # ---- 普通段落 ----
        out.append(f"<p>{inline(line.strip())}</p>")
        i += 1

    return "\n".join(out)


def render_table(block):
    """把 Markdown 表格块（首行表头 + 分隔行 + 数据行）渲染成 <table>。"""
    rows = []
    for row in block:
        if re.match(r"^\|[\s:|-]+\|$", row):  # 跳过分隔行 |---|---|
            continue
        cells = [c.strip() for c in row.strip("|").split("|")]
        rows.append(cells)

    if not rows:
        return ""

    parts = ["<div class='tw'><table>"]
    parts.append(
        "<thead><tr>"
        + "".join(f"<th>{inline(c)}</th>" for c in rows[0])
        + "</tr></thead>"
    )
    parts.append("<tbody>")
    for row in rows[1:]:
        parts.append("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in row) + "</tr>")
    parts.append("</tbody></table></div>")
    return "".join(parts)


def build_toc(md_text: str) -> str:
    """从 h2/h3 抓出一个目录，插到正文最前面，方便长文档跳转。"""
    items = []
    in_code = False
    for line in md_text.split("\n"):
        if line.startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            continue
        m = re.match(r"^(#{2,3})\s+(.*)$", line)
        if m:
            level = len(m.group(1))
            text = m.group(2).strip()
            items.append((level, text, slugify(text)))

    if not items:
        return ""

    links = "".join(
        f'<a class="toc-l{lv}" href="#{anchor}">{esc(text)}</a>'
        for lv, text, anchor in items
    )
    return f'<nav class="toc"><div class="toc-title">目录</div>{links}</nav>'


CSS = """
:root {
  --bg: #ffffff; --panel: #f7f7f5; --ink: #1f1f1d; --ink-2: #5f5e5a;
  --line: #e3e1db; --accent: #185fa5; --code-bg: #f4f4f2; --code-ink: #2c2c2a;
  --warn-bg: #fdf6e7; --warn-line: #d9a440;
}
* { box-sizing: border-box; }
body {
  margin: 0; padding: 0; background: var(--bg); color: var(--ink);
  font-family: -apple-system, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
  font-size: 15px; line-height: 1.75;
}
.wrap { max-width: 900px; margin: 0 auto; padding: 48px 28px 120px; }
h1 { font-size: 28px; font-weight: 600; margin: 0 0 8px; letter-spacing: -0.01em; }
h2 {
  font-size: 21px; font-weight: 600; margin: 52px 0 16px;
  padding-bottom: 10px; border-bottom: 1px solid var(--line);
}
h3 { font-size: 17px; font-weight: 600; margin: 34px 0 12px; }
h4 { font-size: 15px; font-weight: 600; margin: 24px 0 10px; color: var(--ink-2); }
p { margin: 12px 0; }
strong { font-weight: 600; }
a { color: var(--accent); }
hr { border: 0; border-top: 1px solid var(--line); margin: 40px 0; }
code {
  background: var(--code-bg); color: var(--code-ink); padding: 2px 6px;
  border-radius: 4px; font-size: 0.88em;
  font-family: ui-monospace, Consolas, "Courier New", monospace;
}
pre {
  background: var(--code-bg); border: 1px solid var(--line); border-radius: 8px;
  padding: 16px 18px; overflow-x: auto; margin: 16px 0;
}
pre code {
  background: none; padding: 0; font-size: 13px; line-height: 1.65;
  white-space: pre; color: var(--code-ink);
}
blockquote {
  margin: 16px 0; padding: 12px 16px; background: var(--warn-bg);
  border-left: 3px solid var(--warn-line); border-radius: 0 6px 6px 0;
  color: #4a4030; font-size: 14px;
}
blockquote p { margin: 0; }
ul, ol { margin: 12px 0; padding-left: 26px; }
li { margin: 6px 0; }
.tw { overflow-x: auto; margin: 16px 0; }
table { border-collapse: collapse; width: 100%; font-size: 14px; }
th, td {
  border: 1px solid var(--line); padding: 9px 12px; text-align: left;
  vertical-align: top;
}
th { background: var(--panel); font-weight: 600; white-space: nowrap; }
tbody tr:nth-child(even) { background: #fbfbfa; }
.toc {
  background: var(--panel); border: 1px solid var(--line); border-radius: 10px;
  padding: 18px 22px; margin: 28px 0 8px;
}
.toc-title { font-size: 13px; font-weight: 600; color: var(--ink-2); margin-bottom: 10px; }
.toc a {
  display: block; text-decoration: none; color: var(--ink-2);
  font-size: 13.5px; padding: 3px 0;
}
.toc a:hover { color: var(--accent); }
.toc-l3 { padding-left: 18px !important; font-size: 13px !important; }
"""


def main():
    args = sys.argv[1:]
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    files = [DOCS_DIR / a for a in args] if args else sorted(DOCS_DIR.glob("*.md"))
    if not files:
        sys.exit(f"{DOCS_DIR} 下没有 .md 文件")

    log = []
    for md_path in files:
        if not md_path.exists():
            log.append(f"跳过（不存在）：{md_path.name}")
            continue

        md_text = md_path.read_text(encoding="utf-8")
        title = md_path.stem
        for line in md_text.split("\n"):
            if line.startswith("# "):
                title = line[2:].strip()
                break

        body = convert(md_text)
        toc = build_toc(md_text)

        page = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title>
<style>{CSS}</style>
</head>
<body>
<div class="wrap">
{toc}
{body}
</div>
</body>
</html>"""

        out_path = OUT_DIR / (md_path.stem + ".html")
        out_path.write_text(page, encoding="utf-8")
        log.append(f"OK {md_path.name} -> {out_path.relative_to(ROOT)} ({len(page)} 字节)")

    print("\n".join(log))


if __name__ == "__main__":
    main()
