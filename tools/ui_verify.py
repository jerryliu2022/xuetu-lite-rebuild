"""用 Chrome DevTools Protocol 对本项目前端做真实浏览器验收。

用法：先启动后端（python -m uvicorn backend.app:app --port 8765），再执行
    python tools/ui_verify.py
截图落在 data/artifacts/ui_shots/，文字报告落在 data/artifacts/ui_verify_report.txt。

覆盖：
- 登录页浅色/深色主题
- 四个标签页各自渲染
- 主题切换按钮生效并持久化
- 课程卡片封面图是否真的加载出来（naturalWidth > 0）
- 课程网格是否一行 4 个
- 控制台异常收集
- 响应式断点（1024 平板 / 390 手机；4 列宽屏布局由「课程网格列数」那项在默认宽度下验证）

注意：Chrome 必须复用固定的 --user-data-dir，否则每次启动都会新建/删除临时 profile，
几十次启动就会吃掉沙箱的文件删除配额，导致进程被直接杀掉。
"""
from __future__ import annotations

import asyncio
import base64
import json
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]   # 项目根目录：tools/ 的上一层
SHOT_DIR = ROOT / "data" / "artifacts" / "ui_shots"
SHOT_DIR.mkdir(parents=True, exist_ok=True)
(ROOT / "data" / "artifacts").mkdir(parents=True, exist_ok=True)
PROFILE = ROOT / "data" / "artifacts" / ".chrome-profile"
PORT = 9222
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"

report: list[str] = []


def log(msg: str) -> None:
    report.append(str(msg))


async def cdp_connect():
    import websockets

    for _ in range(40):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json/version", timeout=2) as r:
                info = json.loads(r.read())
            return info
        except Exception:
            await asyncio.sleep(0.5)
    raise RuntimeError("Chrome 调试端口未就绪")


class Session:
    def __init__(self, ws):
        self.ws = ws
        self.id = 0
        self.events: list[dict] = []

    async def send(self, method: str, params: dict | None = None, timeout: float = 30.0):
        self.id += 1
        mid = self.id
        await self.ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        while True:
            raw = await asyncio.wait_for(self.ws.recv(), timeout=timeout)
            msg = json.loads(raw)
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(f"{method} -> {msg['error']}")
                return msg.get("result", {})
            if "method" in msg:
                self.events.append(msg)

    async def eval(self, expression: str, timeout: float = 30.0):
        res = await self.send(
            "Runtime.evaluate",
            {"expression": expression, "returnByValue": True, "awaitPromise": True},
            timeout=timeout,
        )
        if res.get("exceptionDetails"):
            raise RuntimeError(json.dumps(res["exceptionDetails"])[:400])
        return res["result"].get("value")

    async def wait_for(self, expression: str, timeout: float = 25.0, label: str = ""):
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                if await self.eval(expression):
                    return True
            except Exception:
                pass
            await asyncio.sleep(0.35)
        log(f"  TIMEOUT 等待 {label or expression}")
        return False

    async def shot(self, name: str, full: bool = False):
        params = {"format": "png", "captureBeyondViewport": full}
        data = (await self.send("Page.captureScreenshot", params))["data"]
        path = SHOT_DIR / f"{name}.png"
        path.write_bytes(base64.b64decode(data))
        return path


async def new_tab(ws_url: str) -> Session:
    import websockets

    ws = await websockets.connect(ws_url, max_size=60 * 1024 * 1024, ping_interval=None)
    sess = Session(ws)
    await sess.send("Page.enable")
    await sess.send("Runtime.enable")
    await sess.send("Log.enable")
    await sess.send("Network.enable")
    await sess.send("Emulation.setDeviceMetricsOverride", {
        "width": 1440, "height": 900, "deviceScaleFactor": 1, "mobile": False,
    })
    return sess


async def main() -> int:
    # 1) 启动 Chrome（固定 profile，复用登录态与缓存）
    if PROFILE.exists():
        pass  # 复用，不删除
    else:
        PROFILE.mkdir(parents=True, exist_ok=True)

    proc = subprocess.Popen(
        [
            CHROME,
            "--headless=new",
            f"--remote-debugging-port={PORT}",
            f"--user-data-dir={PROFILE}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-extensions",
            "--disable-background-networking",
            "--disable-sync",
            "--window-size=1440,900",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        info = await cdp_connect()
        log(f"chrome ready: {info.get('Browser')}")

        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json/list", timeout=5) as r:
            tabs = json.loads(r.read())
        target = next(t for t in tabs if t["type"] == "page")
        sess = await new_tab(target["webSocketDebuggerUrl"])

        # 清掉上个 session 的登录态，并强制把主题钉在浅色，保证截图命名与实际一致
        await sess.send("Page.navigate", {"url": "http://127.0.0.1:8765/"})
        await sess.wait_for("!!document.querySelector('#loginForm')", label="登录页(预热)")
        await sess.eval("localStorage.setItem('xuetu-theme','light')")
        await sess.wait_for("!!document.querySelector('#loginForm')", label="登录页")
        await sess.eval("sessionStorage.clear()")
        await sess.send("Page.reload", {"ignoreCache": True})
        await sess.wait_for("!!document.querySelector('#loginForm')", label="登录页(重载)")

        # ---------- 登录页 ----------
        login_info = await sess.eval("""JSON.stringify({
            theme: document.documentElement.dataset.theme,
            hasThemeBtn: !!document.getElementById('themeToggle'),
            accounts: document.querySelectorAll('[data-demo-account]').length,
            defaultId: document.getElementById('studentIdInput').value,
            cardBg: getComputedStyle(document.querySelector('.login-card')).backgroundColor,
            bodyBg: getComputedStyle(document.body).backgroundColor,
            titleColor: getComputedStyle(document.querySelector('.login-card h1')).color,
        })""")
        log("[登录页 浅色] " + login_info)
        await sess.shot("01-login-light")

        # 切深色
        await sess.eval("document.getElementById('themeToggle').click()")
        await asyncio.sleep(0.6)
        dark_info = await sess.eval("""JSON.stringify({
            theme: document.documentElement.dataset.theme,
            stored: localStorage.getItem('xuetu-theme'),
            label: document.querySelector('#themeToggle .label').textContent,
            cardBg: getComputedStyle(document.querySelector('.login-card')).backgroundColor,
            bodyColor: getComputedStyle(document.body).color,
        })""")
        log("[登录页 深色] " + dark_info)
        await sess.shot("02-login-dark")

        # 切回浅色（验证双向）
        await sess.eval("document.getElementById('themeToggle').click()")
        await asyncio.sleep(0.45)
        log("[主题回切] " + await sess.eval(
            "document.documentElement.dataset.theme + ' / ' + localStorage.getItem('xuetu-theme')"
        ))

        # ---------- 登录：计算机科学与技术 ----------
        await sess.eval("""(() => {
            const acc = [...document.querySelectorAll('[data-demo-account]')]
              .find(b => b.textContent.includes('计算机科学与技术'));
            acc.click();
            return true;
        })()""")
        ok = await sess.wait_for("!!document.querySelector('.course-grid')", timeout=40, label="主界面")
        log(f"[登录后] grid 出现 = {ok}")
        await asyncio.sleep(1.2)

        # ---------- 专业路径推荐 ----------
        pro = await sess.eval("""JSON.stringify({
            tab: document.body.dataset.tab,
            chips: [...document.querySelectorAll('.stat-chip')].map(c => c.querySelector('.label').textContent + '=' + c.querySelector('.value').textContent.trim()),
            cards: document.querySelectorAll('.video-card').length,
            covers: [...document.querySelectorAll('.video-card .cover-wrap img')].map(i => i.naturalWidth).slice(0, 8),
            coversLoaded: [...document.querySelectorAll('.video-card .cover-wrap img')].filter(i => i.naturalWidth > 0).length,
            hotList: document.querySelectorAll('.hot-list').length,
            legend: [...document.querySelectorAll('.recall-item')].map(e => e.textContent.trim()),
            filters: [...document.querySelectorAll('[data-filter]')].map(e => e.textContent.trim()),
        })""")
        log("[专业路径推荐] " + pro)
        await sess.shot("03-professional-light", full=True)

        # 网格列数
        cols = await sess.eval("""(() => {
            const grid = document.querySelector('.course-grid');
            const n = getComputedStyle(grid).gridTemplateColumns.split(' ').length;
            const card = document.querySelector('.video-card').getBoundingClientRect();
            return JSON.stringify({columns: n, cardW: Math.round(card.width), cardH: Math.round(card.height)});
        })()""")
        log("[课程网格] " + cols)

        # 封面图真的请求成功了吗
        await sess.send("Page.navigate", {"url": "http://127.0.0.1:8765/"})
        await sess.wait_for("!!document.querySelector('.course-grid')", timeout=40, label="重载主界面")
        net = [e for e in sess.events if e.get("method") == "Network.responseReceived"
               and "/api/cover/" in json.dumps(e.get("params", {}))]
        codes: dict[int, int] = {}
        for e in net:
            c = e["params"]["response"]["status"]
            codes[c] = codes.get(c, 0) + 1
        log(f"[封面请求] 响应码分布 = {codes}  样本url = {net[0]['params']['response']['url'] if net else '无'}")

        # ---------- 深色下的专业页 ----------
        await sess.eval("document.getElementById('themeToggle').click()")
        await asyncio.sleep(0.6)
        dark_pro = await sess.eval("""JSON.stringify({
            theme: document.documentElement.dataset.theme,
            cardBg: getComputedStyle(document.querySelector('.video-card')).backgroundColor,
            textColor: getComputedStyle(document.querySelector('.video-card h3')).color,
            pageBg: getComputedStyle(document.body).backgroundColor,
        })""")
        log("[专业路径推荐 深色] " + dark_pro)
        await sess.shot("04-professional-dark")

        # 换个筛选
        await sess.eval("document.querySelectorAll('[data-filter]')[3].click()")
        await asyncio.sleep(1.6)
        log("[寒假预习筛选] cards=" + str(await sess.eval("document.querySelectorAll('.video-card').length")))

        # ---------- 就业路径推荐 ----------
        await sess.eval("[...document.querySelectorAll('[data-tab]')].find(b=>b.dataset.tab==='job').click()")
        ok = await sess.wait_for("!!document.querySelector('.job-grid')", timeout=25, label="就业页")
        await asyncio.sleep(0.8)
        job = await sess.eval("""JSON.stringify({
            jobs: document.querySelectorAll('.job-card').length,
            selected: document.querySelector('.job-card.active h3').textContent,
            skills: document.querySelectorAll('.detail-card .tag').length,
            pathSteps: document.querySelectorAll('.path-step').length,
            firstSteps: [...document.querySelectorAll('.path-step h3')].slice(0,4).map(e=>e.textContent),
            chips: [...document.querySelectorAll('.stat-chip')].map(c => c.querySelector('.label').textContent + '=' + c.querySelector('.value').textContent.trim()),
            emptyState: document.querySelectorAll('.empty-state').length,
        })""")
        log(f"[就业路径推荐] {ok} {job}")
        await sess.shot("05-jobs-dark", full=True)

        # 换一个岗位
        await sess.eval("document.querySelectorAll('.job-card')[1].click()")
        await asyncio.sleep(1.4)
        log("[切换岗位] " + await sess.eval("""JSON.stringify({
            selected: document.querySelector('.job-card.active h3').textContent,
            pathSteps: document.querySelectorAll('.path-step').length,
        })"""))

        # ---------- 考研路径推荐 ----------
        await sess.eval("[...document.querySelectorAll('[data-tab]')].find(b=>b.dataset.tab==='exam').click()")
        await sess.wait_for("!!document.querySelector('.course-grid')", timeout=25, label="考研页")
        await asyncio.sleep(0.8)
        exam = await sess.eval("""JSON.stringify({
            grids: document.querySelectorAll('.course-grid').length,
            cards: document.querySelectorAll('.video-card').length,
            dupIds: (() => {
                const ids = [...document.querySelectorAll('.video-card')].map(c=>c.dataset.video);
                return ids.length - new Set(ids).size;
            })(),
            subjects: document.querySelector('.exam-form') ? true : false,
            chips: [...document.querySelectorAll('.stat-chip')].map(c => c.querySelector('.label').textContent + '=' + c.querySelector('.value').textContent.trim()),
            coversLoaded: [...document.querySelectorAll('.video-card .cover-wrap img')].filter(i=>i.naturalWidth>0).length,
        })""")
        log("[考研路径推荐] " + exam)
        await sess.shot("06-exam-dark", full=True)

        # ---------- 数据与模型 ----------
        await sess.eval("[...document.querySelectorAll('[data-tab]')].find(b=>b.dataset.tab==='data').click()")
        ok = await sess.wait_for("!!document.getElementById('statsMajor')", timeout=30, label="数据页")
        await asyncio.sleep(1.0)
        data = await sess.eval("""JSON.stringify({
            loaded: %s,
            tables: document.querySelectorAll('table.data-table').length,
            courseRows: document.querySelectorAll('table.data-table')[0] ? document.querySelectorAll('table.data-table')[0].querySelectorAll('tbody tr').length : 0,
            majorRows: document.querySelectorAll('table.data-table')[1] ? document.querySelectorAll('table.data-table')[1].querySelectorAll('tbody tr').length : 0,
            majorOptions: document.querySelectorAll('#statsMajor option').length,
            chips: [...document.querySelectorAll('.stat-chip')].map(c => c.querySelector('.label').textContent + '=' + c.querySelector('.value').textContent.trim()),
            metrics: [...document.querySelectorAll('.metric-card')].map(c => c.querySelector('span').textContent + '=' + c.querySelector('strong').textContent),
            accounts: document.querySelectorAll('.validity-card').length,
            runItems: document.querySelectorAll('.run-item').length,
        })""" % ("true" if ok else "false"))
        log("[数据与模型] " + data)
        await sess.shot("07-data-dark", full=True)

        # 切换专业看统计
        await sess.eval("""(() => {
            const sel = document.getElementById('statsMajor');
            sel.value = '人工智能'.replace('人工智能','美术学');
            sel.dispatchEvent(new Event('change'));
        })()""") if False else None
        await sess.eval("""(() => {
            const sel = document.getElementById('statsMajor');
            const opt = [...sel.options].find(o => o.value === '美术学');
            sel.value = opt.value;
            sel.dispatchEvent(new Event('change'));
            return sel.value;
        })()""")
        await asyncio.sleep(1.8)
        log("[切换统计专业] " + await sess.eval("""JSON.stringify({
            major: document.querySelector('.stats-summary').textContent.replace(/\\s+/g,' ').trim(),
            courseRows: document.querySelector('table.data-table tbody').querySelectorAll('tr').length,
        })"""))

        # ---------- 响应式（切回专业页，网格才是考察对象） ----------
        await sess.eval("[...document.querySelectorAll('[data-tab]')].find(b=>b.dataset.tab==='professional').click()")
        await sess.wait_for("!!document.querySelector('.course-grid')", timeout=25, label="回到专业页")

        async def scroll_all():
            for ratio in (0.35, 0.7, 1.0):
                await sess.eval(f"window.scrollTo(0, document.body.scrollHeight * {ratio})")
                await asyncio.sleep(0.55)
            await sess.eval("window.scrollTo(0, 0)")
            await asyncio.sleep(0.4)

        await scroll_all()
        loaded = await sess.eval(
            "[...document.querySelectorAll('.video-card .cover-wrap img')].filter(i=>i.naturalWidth>0).length"
            " + '/' + document.querySelectorAll('.video-card .cover-wrap img').length"
        )
        log(f"[滚动后封面加载] {loaded}")

        for w, h, name in ((1024, 800, "08-tablet-1024"), (390, 844, "09-phone-390")):
            await sess.send("Emulation.setDeviceMetricsOverride",
                            {"width": w, "height": h, "deviceScaleFactor": 1, "mobile": w < 600})
            await asyncio.sleep(0.9)
            info = await sess.eval("""JSON.stringify((() => {
                const grid = [...document.querySelectorAll('.course-grid, .job-grid, .metric-grid')]
                  .find(el => getComputedStyle(el).display.includes('grid'));
                return {
                  liveGrid: grid ? grid.className : '无',
                  cols: grid ? getComputedStyle(grid).gridTemplateColumns.split(' ').length : 0,
                  overflowX: document.documentElement.scrollWidth > window.innerWidth + 2,
                  docW: document.documentElement.scrollWidth,
                  winW: window.innerWidth,
                };
            })())""")
            log(f"[响应式 {w}] {info}")
            await sess.shot(name, full=True)
        await sess.send("Emulation.setDeviceMetricsOverride",
                        {"width": 1440, "height": 900, "deviceScaleFactor": 1, "mobile": False})

        # ---------- 控制台异常 ----------
        bad = {}
        for e in sess.events:
            if e.get("method") == "Network.responseReceived":
                resp = e["params"]["response"]
                if resp["status"] >= 400:
                    bad[f"{resp['status']} {resp['url']}"] = bad.get(f"{resp['status']} {resp['url']}", 0) + 1
        log(f"[失败请求] {len(bad)} 个不同 URL")
        for url, cnt in bad.items():
            log(f"   {cnt}x {url}")

        errors = []
        for e in sess.events:
            if e.get("method") == "Runtime.exceptionThrown":
                errors.append("EXC " + str(e["params"]["exceptionDetails"].get("text"))[:200])
            if e.get("method") == "Log.entryAdded":
                entry = e["params"]["entry"]
                if entry.get("level") in ("error", "warning"):
                    errors.append(f"{entry['level'].upper()} {entry.get('text','')[:200]}")
        log(f"[控制台异常] {len(errors)} 条")
        for item in errors[:20]:
            log("   " + item)

        # ---------- 播放页 ----------
        await sess.send("Emulation.setDeviceMetricsOverride",
                        {"width": 1440, "height": 900, "deviceScaleFactor": 1, "mobile": False})
        await sess.send("Page.navigate", {"url": "http://127.0.0.1:8765/"})
        await sess.wait_for("!!document.querySelector('.course-grid')", timeout=40, label="播放页前置：主界面")
        await asyncio.sleep(1.0)
        await sess.eval("document.querySelector('.video-card').click()")
        await sess.wait_for("!!document.querySelector('.player-shell')", timeout=30, label="播放页")
        await asyncio.sleep(1.5)
        play = await sess.eval("""JSON.stringify({
            shell: !!document.querySelector('.player-shell'),
            title: (document.querySelector('.player-title')||{}).textContent,
            episodes: document.querySelectorAll('.episode').length,
            hasFrame: !!document.getElementById('frame'),
            progress: (document.getElementById('progressText')||{}).textContent.replace(/\\s+/g,' ').trim(),
        })""")
        log("[播放页] " + play)
        await sess.shot("10-player-dark")

    finally:
        (ROOT / "data" / "artifacts" / "ui_verify_report.txt").write_text("\n".join(report), encoding="utf-8")
        try:
            proc.terminate()
            proc.wait(timeout=10)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    print("\n".join(report))
    return 0


async def guarded() -> int:
    try:
        return await main()
    except Exception as exc:  # noqa: BLE001
        import traceback

        report.append("SCRIPT_ERROR " + str(exc))
        report.append(traceback.format_exc())
        (ROOT / "data" / "artifacts" / "ui_verify_report.txt").write_text("\n".join(report), encoding="utf-8")
        print("\n".join(report))
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(guarded()))
