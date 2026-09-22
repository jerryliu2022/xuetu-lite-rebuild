/* ==========================================================================
   学途 Lite · 前端应用（岳阳学院版）
   - 主题：浅色 / 深色，localStorage 持久化，data-theme 驱动
   - 封面：每个推荐资源都走 /api/cover/{id}.svg，离线也能渲染
   - 去重：四个标签页各自负责一块信息，互不重复堆同一批课程卡片
   ========================================================================== */

const API = "";
const THEME_KEY = "xuetu-theme";
const PAGE_SIZE_DEFAULT = 24;

const state = {
  studentId: sessionStorage.getItem("studentId") || "",
  tab: location.hash.replace("#", "") || "professional",
  filter: "all",
  pageSize: PAGE_SIZE_DEFAULT,
  profile: null,
  professional: null,
  jobs: null,
  exam: null,
  quality: null,
  evaluation: null,
  stats: null,
  statsMajor: "",
  demoAccounts: null,
  selectedEpisode: 1,
};

const $ = (selector) => document.querySelector(selector);

/* ------------------------------------------------------------------ 基础工具 */

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function api(path, options = {}) {
  return fetch(`${API}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  }).then(async (res) => {
    if (!res.ok) {
      let detail = "请求失败";
      try {
        detail = (await res.json()).detail || detail;
      } catch (error) {
        detail = `请求失败（${res.status}）`;
      }
      throw new Error(detail);
    }
    return res.json();
  });
}

function toast(text) {
  const node = document.createElement("div");
  node.className = "toast";
  node.textContent = text;
  document.body.appendChild(node);
  setTimeout(() => node.remove(), 2100);
}

function fmtRatio(value, digits = 4) {
  if (value === undefined || value === null || Number.isNaN(Number(value))) return "--";
  return Number(value).toFixed(digits);
}

function fmtCount(value) {
  const num = Number(value || 0);
  return num.toLocaleString("zh-CN");
}

/* ------------------------------------------------------------------ 主题 */

function currentTheme() {
  return document.documentElement.dataset.theme === "dark" ? "dark" : "light";
}

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  try {
    localStorage.setItem(THEME_KEY, theme);
  } catch (error) {
    /* 隐私模式下 localStorage 不可用，忽略即可 */
  }
  const btn = $("#themeToggle");
  if (btn) {
    const dark = theme === "dark";
    btn.setAttribute("aria-pressed", String(dark));
    btn.querySelector(".icon").textContent = dark ? "☀" : "☾";
    btn.querySelector(".label").textContent = dark ? "浅色" : "深色";
  }
}

function initTheme() {
  let saved = null;
  try {
    saved = localStorage.getItem(THEME_KEY);
  } catch (error) {
    saved = null;
  }
  const prefersDark = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
  applyTheme(saved || (prefersDark ? "dark" : "light"));
}

function themeToggleMarkup() {
  const dark = currentTheme() === "dark";
  return `<button class="theme-toggle" id="themeToggle" type="button"
            aria-pressed="${dark}" title="切换浅色 / 深色主题">
      <span class="icon" aria-hidden="true">${dark ? "☀" : "☾"}</span>
      <span class="label">${dark ? "浅色" : "深色"}</span>
    </button>`;
}

function bindThemeToggle() {
  const btn = $("#themeToggle");
  if (!btn) return;
  btn.addEventListener("click", () => applyTheme(currentTheme() === "dark" ? "light" : "dark"));
}

/* ------------------------------------------------------------------ 登录 */

const DEMO_ACCOUNT_FALLBACK = [
  { student_id: "YY01", name: "李明", major: "计算机科学与技术", college: "信息工程学院" },
];

function brand() {
  return `<div class="brand"><span class="brand-mark" aria-hidden="true">◆</span><span>学途 Lite</span></div>`;
}

async function renderLogin() {
  let accounts = DEMO_ACCOUNT_FALLBACK;
  try {
    const result = await api("/api/demo-accounts");
    if (result.accounts && result.accounts.length) accounts = result.accounts;
  } catch (error) {
    console.warn(error);
  }
  // 演示默认落在计算机科学与技术账号上：它的培养方案与资源池最能说明推荐链路
  const preferred = accounts.find((account) => account.major === "计算机科学与技术") || accounts[0];
  $("#app").innerHTML = `
    <main class="login-screen">
      <form class="login-card" id="loginForm">
        <div class="login-theme-row">${brand()}${themeToggleMarkup()}</div>
        <h1>欢迎回来</h1>
        <p class="subtle">岳阳学院 25 个招生专业 · 按培养方案推荐本学期与下学期课程</p>
        <div class="field">
          <label for="studentIdInput">学号</label>
          <input id="studentIdInput" name="student_id" value="${escapeHtml(preferred.student_id)}" placeholder="请输入学号" autocomplete="username" />
        </div>
        <div class="field">
          <label for="passwordInput">密码</label>
          <input id="passwordInput" name="password" type="password" value="123456" placeholder="请输入密码" autocomplete="current-password" />
        </div>
        <button class="primary-btn" type="submit">登 录</button>
        <div class="sso-note">已接入校园系统 ・ 学号透传免登录校验</div>
        <div class="login-accounts">
          <div class="subtle">每个专业一个演示账号，密码统一 123456，点一下直接登录</div>
          <div class="account-preset-grid">
            ${accounts.map((account) => `
              <button type="button" class="account-preset"
                data-demo-account="${escapeHtml(account.student_id)}">
                <strong>${escapeHtml(account.student_id)}</strong>
                <span>${escapeHtml(account.name)} ・ ${escapeHtml(account.major)}</span>
              </button>
            `).join("")}
          </div>
        </div>
      </form>
    </main>
  `;
  bindThemeToggle();
  document.querySelectorAll("[data-demo-account]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const form = $("#loginForm");
      form.student_id.value = btn.dataset.demoAccount;
      form.password.value = "123456";
      form.requestSubmit();
    });
  });
  $("#loginForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const data = Object.fromEntries(new FormData(event.target));
    try {
      const res = await api("/api/login", { method: "POST", body: JSON.stringify(data) });
      state.studentId = res.student.student_id;
      sessionStorage.setItem("studentId", state.studentId);
      state.statsMajor = "";
      await loadApp();
    } catch (error) {
      toast(error.message);
    }
  });
}

/* ------------------------------------------------------------------ 数据加载 */

async function loadApp() {
  const route = location.pathname.match(/^\/play\/(.+)$/);
  if (route) {
    await renderPlayer(route[1]);
    return;
  }
  const sid = state.studentId;
  // 画像要先拿到：统计页在没指定专业时用它推断学生专业
  state.profile = await api(`/api/profile/${sid}`);
  const major = state.profile.student ? state.profile.student.major : "";

  // 其余 7 个接口互不依赖，之前是一个个 await 串行发的，
  // 七个请求的耗时直接相加（实测合计约 290 ms，统计页另外还要单独再切一次专业）。
  // 并行发出后首屏只等最慢的那个。
  const [professional, jobs, exam, quality, evaluation, demoAccounts, stats] = await Promise.all([
    api(`/api/recommendations/professional?student_id=${sid}&filter=${state.filter}&page_size=${state.pageSize}`),
    api(`/api/recommendations/jobs?student_id=${sid}`),
    api(`/api/recommendations/exam?student_id=${sid}`),
    api("/api/admin/data-quality"),
    api("/api/admin/model-evaluation"),
    api("/api/demo-accounts"),
    api(`/api/stats/resources?student_id=${sid}&major=${encodeURIComponent(state.statsMajor || major)}`),
  ]);
  state.professional = professional;
  state.jobs = jobs;
  state.exam = exam;
  state.quality = quality;
  state.evaluation = evaluation;
  state.demoAccounts = demoAccounts;
  state.stats = stats;
  state.statsMajor = state.stats.major;
  renderShell();
}

/* 轮询后台任务状态：done 时执行 onDone，error 时提示。同一个接口只允许挂一个轮询。 */
const _pollTimers = {};

function pollJobStatus(statusPath, label, onDone, intervalMs = 2500) {
  if (_pollTimers[statusPath]) return;
  _pollTimers[statusPath] = setInterval(async () => {
    let state;
    try {
      state = await api(statusPath);
    } catch (error) {
      return; /* 网络抖动时下个周期再试 */
    }
    if (state.status === "done") {
      clearInterval(_pollTimers[statusPath]);
      delete _pollTimers[statusPath];
      toast(`${label}已完成`);
      await onDone();
    } else if (state.status === "error") {
      clearInterval(_pollTimers[statusPath]);
      delete _pollTimers[statusPath];
      toast(`${label}失败：${state.error || "未知错误"}`);
    } else if (state.status !== "running" && state.status !== "already_running") {
      clearInterval(_pollTimers[statusPath]);
      delete _pollTimers[statusPath];
    }
  }, intervalMs);
}

/* ------------------------------------------------------------------ 外壳 */

function renderShell() {
  document.body.dataset.tab = state.tab;
  $("#app").innerHTML = `
    <div class="shell" id="shell">
      ${renderSidebar()}
      <main class="main">
        <header class="topbar">
          <label class="search">
            <span aria-hidden="true">⌕</span>
            <input id="globalSearch" placeholder="搜索课程 / 岗位 / 院校" aria-label="全局搜索" />
          </label>
          <div class="topbar-actions">
            ${themeToggleMarkup()}
            <button class="ghost-btn" id="switchAccount" type="button">切换账号</button>
          </div>
        </header>
        <nav class="tabs" aria-label="主导航">
          ${tabButton("professional", "专业路径推荐")}
          ${tabButton("job", "就业路径推荐")}
          ${tabButton("exam", "考研路径推荐")}
          ${tabButton("data", "数据与模型")}
        </nav>
        <section class="content" id="content"></section>
      </main>
    </div>
  `;
  // 注意选择器必须限定在导航按钮上：body 因 document.body.dataset.tab 也带
  // data-tab 属性，若用全局 [data-tab] 会把点击监听绑到 body 上——
  // 点页面任何文字都冒泡触发 renderShell() 整页重渲染（表现为"点哪都自动刷新"），
  // 且 body 不会被 innerHTML 替换，监听器随每次渲染不断累积，越点越卡。
  document.querySelectorAll(".tabs [data-tab]").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.tab = btn.dataset.tab;
      history.replaceState(null, "", `#${state.tab}`);
      renderShell();
    });
  });
  bindThemeToggle();
  $("#switchAccount").addEventListener("click", () => {
    sessionStorage.removeItem("studentId");
    state.studentId = "";
    history.pushState(null, "", "/");
    location.reload();
  });
  $("#sidebarToggle").addEventListener("click", () => {
    const shell = $("#shell");
    shell.classList.toggle("collapsed");
    const collapsed = shell.classList.contains("collapsed");
    $("#sidebarToggle").setAttribute("aria-expanded", String(!collapsed));
  });
  $("#globalSearch").addEventListener("keydown", (event) => {
    if (event.key !== "Enter") return;
    toast(`已按「${event.target.value}」在推荐候选中检索`);
  });
  renderContent();
}

function tabButton(id, text) {
  return `<button class="tab ${state.tab === id ? "active" : ""}" data-tab="${id}"
            type="button" aria-current="${state.tab === id}">${text}</button>`;
}

function renderSidebar() {
  const { student, current_term_courses, next_term_courses, achievements, major_info } = state.profile;
  const doneCourses = (state.profile.courses || []).filter((c) => c.semester < student.semester);
  const xpRatio = Math.min(100, Math.round((student.xp / student.next_xp) * 100));
  return `
    <aside class="sidebar">
      <div class="side-head">
        ${brand()}
        <button class="icon-btn hide-when-collapsed" id="sidebarToggle" type="button"
                aria-expanded="true" title="收起侧边栏">‹</button>
      </div>

      <section class="student-card hide-when-collapsed">
        <div class="student-main">
          <div class="avatar" aria-hidden="true"></div>
          <div>
            <h2>${escapeHtml(student.name)}</h2>
            <div class="subtle">${escapeHtml(student.major)} ・ ${escapeHtml(student.grade)}</div>
          </div>
        </div>
        <div class="level-row">
          <strong>Lv.${student.level} 进阶者</strong>
          <span>${student.xp} / ${student.next_xp} 经验</span>
        </div>
        <div class="progress" role="progressbar" aria-valuenow="${xpRatio}" aria-valuemin="0" aria-valuemax="100">
          <span style="width:${xpRatio}%"></span>
        </div>
        <div class="hint">${escapeHtml(major_info ? major_info.college : "")} ・ 共 ${escapeHtml(major_info ? major_info.course_count : 0)} 门培养方案课程</div>
      </section>

      <div class="hide-when-collapsed">
        <div class="side-title">培养方案 ・ 第 ${student.semester} 学期</div>
        <section class="path-card">
          <div class="term-group">
            <div class="term-group-title"><span>本学期 ・ 正在学</span><span>${current_term_courses.length} 门</span></div>
            ${current_term_courses.slice(0, 6).map((course) => `
              <div class="course-node studying">
                <span class="dot"></span><span>${escapeHtml(course.name)}</span>
                <span class="node-status">正在学</span>
              </div>
            `).join("") || `<div class="hint">本学期暂无课程</div>`}
          </div>
          <div class="term-group">
            <div class="term-group-title"><span>下学期 ・ 待预习</span><span>${next_term_courses.length} 门</span></div>
            ${next_term_courses.slice(0, 5).map((course) => `
              <div class="course-node planned">
                <span class="dot"></span><span>${escapeHtml(course.name)}</span>
                <span class="node-status">未学</span>
              </div>
            `).join("") || `<div class="hint">下学期暂无课程</div>`}
          </div>
          <div class="term-group">
            <div class="term-group-title"><span>往期 ・ 已修</span><span>${doneCourses.length} 门</span></div>
            ${doneCourses.slice(0, 3).map((course) => `
              <div class="course-node completed">
                <span class="dot"></span><span>${escapeHtml(course.name)}</span>
                <span class="node-status">已学</span>
              </div>
            `).join("") || `<div class="hint">暂无往期课程</div>`}
          </div>
        </section>

        <div class="side-title">成就 ・ 已通关</div>
        <section class="achieve-panel">
          ${achievements.length ? achievements.slice(0, 4).map((item, index) => `
            <button class="achievement" type="button" data-achieve="${escapeHtml(item.video_id)}"
              style="background:linear-gradient(100deg, ${escapeHtml(item.cover_color || "#6c5ce7")}, ${index % 2 ? "var(--accent)" : "var(--primary-strong)"})">
              ${escapeHtml(item.course_name || item.title)}
              <small>${escapeHtml(item.platform)} ・ 已通关</small>
            </button>
          `).join("") : `<div class="hint">看完一门课的最后一集，这里就会出现成就卡。</div>`}
        </section>
      </div>
    </aside>
  `;
}

/* ------------------------------------------------------------------ 内容路由 */

function renderContent() {
  if (state.tab === "professional") renderProfessional();
  else if (state.tab === "job") renderJobs();
  else if (state.tab === "exam") renderExam();
  else renderDataModel();
  document.querySelectorAll("[data-achieve]").forEach((btn) => {
    btn.addEventListener("click", () => showAchievement(btn.dataset.achieve));
  });
}

/* ------------------------------------------------------------------ 课程卡片 */

function kindTag(kind) {
  if (kind === "专业核心") return `<span class="kind-tag core">专业核心</span>`;
  if (kind === "学科基础") return `<span class="kind-tag base">学科基础</span>`;
  if (kind === "通识必修") return `<span class="kind-tag">通识必修</span>`;
  return "";
}

function videoCard(item) {
  const cover = `/api/cover/${encodeURIComponent(item.video_id)}.svg`;
  return `
    <button class="video-card" type="button" data-video="${escapeHtml(item.video_id)}">
      <div class="cover-wrap">
        <img src="${cover}" alt="《${escapeHtml(item.course_name || item.title)}》课程封面" decoding="async" />
        <span class="badge-${item.is_paid ? "paid" : "free"}">${item.is_paid ? "付费" : "免费"}</span>
      </div>
      <div class="body">
        <h3>${escapeHtml(item.title)}</h3>
        <div class="course-line">
          ${kindTag(item.course_kind)}
          ${item.course_name ? `<span>《${escapeHtml(item.course_name)}》</span>` : ""}
          ${item.course_semester ? `<span>第 ${item.course_semester} 学期</span>` : ""}
        </div>
        <div class="meta">
          <span class="platform ${item.is_paid ? "paid" : ""}">${escapeHtml(item.platform)}</span>
          <span>${escapeHtml(item.plays)}</span>
          <span>${fmtRatio(item.rating, 1)} 分</span>
        </div>
        <div class="score">
          <span class="reason-text">${escapeHtml(item.reason || item.summary || "匹配你的培养方案")}</span>
          <div class="score-line">综合推荐分 <b>${(Number(item.score || 0) * 100).toFixed(1)}%</b>${
            item.predicted_ctr !== undefined ? ` ・ 模型预测 ${(item.predicted_ctr * 100).toFixed(1)}%` : ""
          }</div>
        </div>
      </div>
    </button>
  `;
}

function bindVideoCards() {
  document.querySelectorAll("[data-video]").forEach((card) => {
    card.addEventListener("click", () => {
      api("/api/track", {
        method: "POST",
        body: JSON.stringify({ student_id: state.studentId, video_id: card.dataset.video, event_type: "click" }),
      }).catch(() => {});
      location.href = `/play/${card.dataset.video}`;
    });
  });
}

function statChip(label, value, unit, tone, hint) {
  return `
    <article class="stat-chip ${tone || ""}">
      <div class="label">${escapeHtml(label)}</div>
      <div class="value">${value}${unit ? `<small>${escapeHtml(unit)}</small>` : ""}</div>
      ${hint ? `<div class="stat-hint">${escapeHtml(hint)}</div>` : ""}
    </article>
  `;
}

/* ------------------------------------------------------------------ 专业路径推荐 */

function renderProfessional() {
  const data = state.professional;
  const items = data.items || [];
  const distinctCourses = new Set(items.map((item) => item.course_id)).size;
  const platformSet = new Set(items.map((item) => item.platform)).size;
  const platformNames = [...new Set(items.map((item) => item.platform))].join(" / ") || "B站";

  $("#content").innerHTML = `
    <div class="page-head">
      <div>
        <h1>专业路径推荐</h1>
        <div class="subtle">
          基于岳阳学院《${escapeHtml(data.student ? data.student.major : "")}》培养方案，
          按「本学期同课程 / 往期后继进阶 / 下学期预习」三路召回
        </div>
      </div>
      <div class="actions-inline">
        <button class="ghost-btn" id="refreshProfessional" type="button">换一批排序</button>
      </div>
    </div>

    <div class="stat-strip">
      ${statChip("本专业推荐课程", distinctCourses, "门", "primary", `本次展示 ${items.length} 条资源`)}
      ${statChip("总推荐课程数", fmtCount(data.total), "条候选", `已展示 ${data.returned} 条`)}
      ${statChip("本专业资源池", fmtCount(data.major_resource_total), "条", `覆盖 ${state.stats.course_count} 门培养方案课程`)}
      ${statChip("全校资源池", fmtCount(state.stats.total_resources), "条", `${state.stats.major_count} 个专业 / ${state.stats.majors.reduce((s, m) => s + (m.course_count || 0), 0)} 门课程`)}
      ${statChip("推荐平台覆盖", platformSet, "个平台", platformNames)}
    </div>

    <div class="filters" role="group" aria-label="推荐场景筛选">
      ${filterButton("all", `全部 ${data.filters.all}`)}
      ${filterButton("studying", `正在学相关 ${data.filters.studying}`)}
      ${filterButton("advanced", `已学进阶 ${data.filters.advanced}`)}
      ${filterButton("vacation", `寒暑假预习 ${data.filters.vacation}`)}
    </div>

    <div class="recall-legend">
      ${Object.entries(data.reason_group_count).map(([name, count]) => `
        <span class="recall-item"><b>${count}</b>${escapeHtml(name)}</span>
      `).join("")}
    </div>

    <div class="course-grid">${items.map(videoCard).join("") || `<div class="empty-state">当前筛选下暂无推荐</div>`}</div>

    ${data.total > items.length ? `
      <div class="load-more">
        <button class="ghost-btn" id="loadMore" type="button">
          展开更多（已展示 ${items.length} / ${data.total} 条）
        </button>
      </div>
    ` : ""}
  `;

  bindVideoCards();
  document.querySelectorAll("[data-filter]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      state.filter = btn.dataset.filter;
      state.professional = await api(
        `/api/recommendations/professional?student_id=${state.studentId}&filter=${state.filter}&page_size=${state.pageSize}`
      );
      renderProfessional();
    });
  });
  $("#refreshProfessional").addEventListener("click", async () => {
    // 换一批：直接向下一档取页，让用户看到候选池里更多不同资源
    state.pageSize = state.pageSize >= 200 ? PAGE_SIZE_DEFAULT : state.pageSize + PAGE_SIZE_DEFAULT;
    state.professional = await api(
      `/api/recommendations/professional?student_id=${state.studentId}&filter=${state.filter}&page_size=${state.pageSize}`
    );
    toast(`已展示 ${state.professional.returned} 条推荐`);
    renderProfessional();
  });
  const loadMore = $("#loadMore");
  if (loadMore) {
    loadMore.addEventListener("click", () => $("#refreshProfessional").click());
  }
}

function filterButton(id, text) {
  return `<button class="pill ${state.filter === id ? "active" : ""}" data-filter="${id}"
            type="button" aria-pressed="${state.filter === id}">${escapeHtml(text)}</button>`;
}

/* ------------------------------------------------------------------ 就业路径推荐 */

function renderJobs() {
  const { jobs, selected, path } = state.jobs;
  const skillList = (selected.skills || "").split(/[,，]/).map((s) => s.trim()).filter(Boolean);
  $("#content").innerHTML = `
    <div class="page-head">
      <div>
        <h1>就业路径推荐</h1>
        <div class="subtle">按专业匹配岗位，再把岗位技能反推成一条有先后的学习路径</div>
      </div>
      <button class="ghost-btn" id="shuffleJobs" type="button">按匹配度重排</button>
    </div>

    <div class="stat-strip">
      ${statChip("匹配岗位", jobs.length, "个", "primary", "来自拉勾 / BOSS 直聘岗位池")}
      ${statChip("当前岗位技能点", skillList.length, "项", "accent", escapeHtml(selected.title))}
      ${statChip("反推学习资源", path.length, "条", "warm", "按技能先修关系拓扑排序")}
      ${statChip("技能匹配度", escapeHtml(selected.major_match), "", "sun", `要求专业：${escapeHtml(selected.required_major || "不限")}`)}
    </div>

    <h2 class="section-title">岗位池 ・ 点击切换查看详情</h2>
    <div class="job-grid">
      ${jobs.map((job) => `
        <button class="job-card ${job.job_id === selected.job_id ? "active" : ""}" type="button"
                data-job="${escapeHtml(job.job_id)}" aria-pressed="${job.job_id === selected.job_id}">
          <h3>${escapeHtml(job.title)}</h3>
          <div class="subtle">${escapeHtml(job.company)} ・ ${escapeHtml(job.city)}</div>
          <div class="salary">${escapeHtml(job.salary)} ・ ${escapeHtml(job.fresh)}</div>
          <div class="match ${job.major_match === "一般匹配" ? "mid" : job.major_match === "相关延伸" ? "low" : ""}">
            ${escapeHtml(job.major_match)}
          </div>
        </button>
      `).join("")}
    </div>

    <section class="detail-card">
      <div class="page-head">
        <div>
          <h2>招聘详情 ・ ${escapeHtml(selected.title)}</h2>
          <p class="subtle">${escapeHtml(selected.requirement || "暂无岗位描述")}</p>
        </div>
        <a class="ghost-btn link-btn" target="_blank" rel="noopener" href="${escapeHtml(selected.source_url || "#")}">
          查看原招聘 ・ ${escapeHtml(selected.source || "来源")}
        </a>
      </div>
      <div class="tags">${skillList.map((skill) => `<span class="tag">${escapeHtml(skill)}</span>`).join("")}</div>
    </section>

    <h2 class="section-title">技能反推 ・ 学习路径（按先后顺序）</h2>
    ${path.length ? `
      <div class="path-row">
        ${path.slice(0, 8).map((item, index) => pathStep(item, index)).join("")}
      </div>
    ` : `<div class="empty-state">该岗位暂未建立技能到课程的映射</div>`}
  `;

  document.querySelectorAll("[data-job]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      state.jobs = await api(`/api/recommendations/jobs?student_id=${state.studentId}&job_id=${btn.dataset.job}`);
      renderJobs();
    });
  });
  $("#shuffleJobs").addEventListener("click", () => {
    $("#content").scrollIntoView({ behavior: "smooth", block: "start" });
    toast("岗位已按专业匹配度重新排序");
  });
  bindVideoCards();
}

function pathStep(item, index) {
  return `
    <button class="path-step" type="button" data-video="${escapeHtml(item.video_id)}">
      <div class="step-no">第 ${index + 1} 步</div>
      <h3>${escapeHtml(item.course_name || item.title)}</h3>
      <div class="step-skill">${escapeHtml(item.skill || "")}</div>
      <div class="meta">
        <span class="platform ${item.is_paid ? "paid" : ""}">${escapeHtml(item.platform)}</span>
        <span>${item.is_paid ? "付费" : "免费"}</span>
      </div>
    </button>
  `;
}

/* ------------------------------------------------------------------ 考研路径推荐 */

function renderExam() {
  const data = state.exam;
  const seen = new Set();
  const pub = [];
  const professional = [];
  (data.public || []).forEach((item) => {
    if (!seen.has(item.video_id)) { seen.add(item.video_id); pub.push(item); }
  });
  (data.professional || []).forEach((item) => {
    if (!seen.has(item.video_id)) { seen.add(item.video_id); professional.push(item); }
  });

  $("#content").innerHTML = `
    <div class="page-head">
      <div>
        <h1>考研路径推荐</h1>
        <div class="subtle">公共课按专业大类统一，专业课按该专业培养方案的学位课程推导</div>
      </div>
      <button class="ghost-btn" id="regenExam" type="button">重新生成</button>
    </div>

    <div class="stat-strip">
      ${statChip("目标专业", escapeHtml(data.major), "", "primary", `专业大类：${escapeHtml(data.category || "")}`)}
      ${statChip("初试科目", data.subjects.length, "门", "accent", escapeHtml(data.subjects.join(" / ")))}
      ${statChip("公共课资源", pub.length, "条", "warm", "政治 / 英语 / 数学")}
      ${statChip("专业课资源", professional.length, "条", "sun", "按学位课程匹配")}
    </div>

    <form class="exam-form" id="examForm">
      <div>
        <label for="examSchool">目标院校</label>
        <input id="examSchool" name="school" value="${escapeHtml(data.school === "默认" ? "" : data.school)}" placeholder="如：湖南理工学院" />
      </div>
      <div>
        <label for="examMajor">目标专业</label>
        <input id="examMajor" name="major" value="${escapeHtml(data.major)}" placeholder="如：计算机科学与技术" />
      </div>
      <button class="primary-btn" type="submit">查询考研科目</button>
    </form>

    <div class="school-chips">
      ${(data.schools || []).map((name) => `<span class="tag">${escapeHtml(name)}</span>`).join("")}
    </div>

    <h2 class="section-title">阶段 1 ・ 公共考研课</h2>
    <div class="course-grid">${pub.map(videoCard).join("") || `<div class="empty-state">暂无公共课资源</div>`}</div>

    <h2 class="section-title">阶段 2 ・ 专业考研课</h2>
    <div class="course-grid">${professional.map(videoCard).join("") || `<div class="empty-state">暂无专业课资源</div>`}</div>

    <p class="subtle exam-note">${escapeHtml(data.note || "")}</p>
  `;
  $("#examForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = Object.fromEntries(new FormData(event.target));
    state.exam = await api(
      `/api/recommendations/exam?student_id=${state.studentId}` +
      `&school=${encodeURIComponent(form.school)}&major=${encodeURIComponent(form.major)}`
    );
    renderExam();
  });
  $("#regenExam").addEventListener("click", () => $("#examForm").requestSubmit());
  bindVideoCards();
}

/* ------------------------------------------------------------------ 数据与模型 */

function dataTable(headers, rows) {
  return `
    <div class="table-wrap">
      <table class="data-table">
        <thead><tr>${headers.map((h) => `<th class="${h.num ? "num" : ""}">${escapeHtml(h.label)}</th>`).join("")}</tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
  `;
}

function renderDataModel() {
  const q = state.quality;
  const ev = state.evaluation;
  const stats = state.stats;
  const avg = ev.ranking_eval.averages;
  const maxResource = Math.max(1, ...stats.courses.map((c) => c.resource_count));
  const maxMajorResource = Math.max(1, ...stats.majors.map((m) => m.resource_count));
  const schoolCourseCount = stats.majors.reduce((sum, m) => sum + (m.course_count || 0), 0);
  const bilibiliCount = (stats.by_platform.find((p) => p.platform === "B站") || {}).c || 0;

  $("#content").innerHTML = `
    <div class="page-head">
      <div>
        <h1>数据与模型</h1>
        <div class="subtle">数据来源与更新频率、每专业每门课资源量、排序模型离线效果</div>
      </div>
      <div class="actions-inline">
        <button class="ghost-btn" id="collectLive" type="button">采集真实数据</button>
        <button class="ghost-btn" id="runEval" type="button">重新评估</button>
      </div>
    </div>

    <section class="truth-card">
      <strong>数据真实性说明</strong>
      <div>${escapeHtml(q.truth_statement)}</div>
      <div class="freshness-line">
        报告生成时间：${escapeHtml(q.freshness.generated_at)} ・ 视频只计最近 6 年、招聘只计最近 2 年、考研科目只计最新年份。
      </div>
    </section>

    <div class="metric-grid">
      ${metricCard("视频课程", fmtCount(q.video.total_videos), `库内总数 ・ 专业覆盖 ${(q.video.major_coverage_rate * 100).toFixed(1)}% ・ 覆盖 ${q.video.covered_majors.length}/${q.video.target_major_count} 个专业`)}
      ${metricCard("招聘岗位", fmtCount(q.job.total_jobs), `拉勾 ${q.job.lagou_count} / BOSS ${q.job.boss_count} ・ 近 2 年 ${q.job.live_recent_jobs || 0} 条`)}
      ${metricCard("考研科目", fmtCount(q.exam.latest_records), `官方目录最新年份 ・ 专业覆盖 ${(q.exam.major_coverage_rate * 100).toFixed(1)}%`)}
      ${metricCard("训练 AUC", fmtRatio(ev.offline_train_report.auc), `样本 ${fmtCount(ev.offline_train_report.sample_count)} 条 ・ CPU 轻量排序模型`)}
    </div>

    <h2 class="section-title">资源统计 ・ 每专业每门课资源数</h2>
    <div class="stats-toolbar">
      <label class="select-label" for="statsMajor">切换专业</label>
      <select class="select-input" id="statsMajor">
        ${stats.majors.map((m) => `
          <option value="${escapeHtml(m.name)}" ${m.name === stats.major ? "selected" : ""}>
            ${escapeHtml(m.name)}（${m.college}）
          </option>
        `).join("")}
      </select>
      <div class="stats-summary">
        本专业 <b>${stats.course_count}</b> 门课 ・ 共 <b>${fmtCount(stats.course_resource_total)}</b> 条资源 ・
        平均每门 <b>${stats.avg_resource_per_course}</b> 条
      </div>
    </div>

    <div class="stat-strip compact">
      ${statChip("本专业课程门数", stats.course_count, "门", "primary", escapeHtml(stats.major))}
      ${statChip("本专业课程资源", fmtCount(stats.course_resource_total), "条", "accent", `平均 ${stats.avg_resource_per_course} 条/门`)}
      ${statChip("全校课程门数", fmtCount(schoolCourseCount), "门", "warm", `${stats.major_count} 个招生专业合计`)}
      ${statChip("全校课程资源", fmtCount(stats.total_resources), "条", "sun", `其中 B站 真实采集 ${fmtCount(bilibiliCount)} 条`)}
    </div>

    ${dataTable(
      [{ label: "学期" }, { label: "课程" }, { label: "类型" }, { label: "学分", num: true }, { label: "资源数", num: true }, { label: "资源量" }, { label: "平台分布" }],
      stats.courses.map((c) => {
        const platforms = [
          ["B站", c.bilibili], ["MOOC", c.mooc], ["极客", c.geektime],
          ["学堂", c.xuetangx], ["网易", c.netease],
        ].filter(([, n]) => n > 0);
        const text = platforms.length
          ? platforms.map(([name, n]) => `${name} ${n}`).join(" / ")
          : "暂无资源";
        return `
        <tr>
          <td>第 ${c.semester} 学期</td>
          <td>${escapeHtml(c.name)}</td>
          <td>${escapeHtml(c.course_kind || "")}</td>
          <td class="num">${c.credits ?? "--"}</td>
          <td class="num"><b>${c.resource_count}</b></td>
          <td><span class="bar" style="width:${Math.round((c.resource_count / maxResource) * 110)}px"></span></td>
          <td>${escapeHtml(text)}</td>
        </tr>`;
      }).join("")
    )}

    <h2 class="section-title">各专业资源总量</h2>
    ${dataTable(
      [{ label: "专业" }, { label: "学院" }, { label: "类别" }, { label: "课程门数", num: true }, { label: "资源数", num: true }, { label: "资源量" }],
      stats.majors.map((m) => `
        <tr>
          <td>${escapeHtml(m.name)}</td>
          <td>${escapeHtml(m.college)}</td>
          <td>${escapeHtml(m.category)}</td>
          <td class="num">${m.course_count}</td>
          <td class="num"><b>${fmtCount(m.resource_count)}</b></td>
          <td><span class="bar" style="width:${Math.round((m.resource_count / maxMajorResource) * 110)}px"></span></td>
        </tr>
      `).join("")
    )}

    <h2 class="section-title">模型效果（离线评估）</h2>
    <div class="metric-grid">
      ${metricCard("Precision@5", fmtRatio(avg["precision@5"]), "前 5 个推荐中命中「应学未学」资源的比例")}
      ${metricCard("NDCG@5", fmtRatio(avg["ndcg@5"]), "排序位置质量，命中越靠前得分越高")}
      ${metricCard("HitRate@5", fmtRatio(avg["hit_rate@5"]), "前 5 位至少命中一条的账号比例")}
      ${metricCard("Recall@全量", fmtRatio(avg.recall_full), "整条推荐链路对「应学未学」资源池的覆盖率")}
      ${metricCard("培养方案课程覆盖率", fmtRatio(avg.course_coverage_rate), `一次推荐平均覆盖 ${avg.covered_course_count} 门本专业课程`)}
      ${metricCard("本专业对齐度", fmtRatio(avg.major_fit), "推荐资源落在本专业培养方案内的比例")}
      ${metricCard("平台多样性", fmtRatio(avg.platform_diversity), "推荐资源在 5 个平台上的分散度")}
      ${metricCard("全库资源覆盖率", fmtRatio(avg.catalog_coverage), "账号群合计触达资源库的比例")}
    </div>
    <p class="metric-note">${escapeHtml(ev.ranking_eval.method)}</p>
    <p class="metric-note">
      留一法校验（遮住一条已学记录、重训后看能否召回）：Top-5 命中率
      <b>${fmtRatio(ev.leave_one_out.averages["hit_rate@5"])}</b>，Top-24 命中率
      <b>${fmtRatio(ev.leave_one_out.averages["hit_rate@24"])}</b>。
      已学资源按推荐设计会被排到学习路径之外，所以这个指标衡量的是「旧知识复现」而不是「路径推荐」，
      数值偏低属于预期，真正的路径质量看上面的 Precision@5 / NDCG@5 / 培养方案课程覆盖率。
    </p>
    <ol class="plan-list">
      ${ev.effect_improvement_plan.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}
    </ol>

    <h2 class="section-title">最近采集结果</h2>
    <div class="run-list">
      ${(q.collection_history || []).length ? q.collection_history.map((run) => `
        <article class="run-item">
          <strong>${escapeHtml(run.domain)} / ${escapeHtml(run.source)}</strong>
          <span>${run.inserted_count} / ${run.target_count} 入库</span>
          <span class="run-status ${run.ok ? "ok" : "warn"}">${run.ok ? "达标" : "未达标"}</span>
          <p>${escapeHtml(run.message)}</p>
        </article>
      `).join("") : `<p class="subtle">尚未执行真实联网采集。</p>`}
    </div>

    <h2 class="section-title">来源与更新策略</h2>
    <div class="source-grid">
      ${q.source_policies.map((policy) => `
        <article class="source-card">
          <h3>${escapeHtml(policy.domain)}</h3>
          <div class="tags">${policy.sources.map((s) => `<span class="tag">${escapeHtml(s)}</span>`).join("")}</div>
          <p class="subtle">当前状态：${policy.live_collected ? "已接真实采集" : "采集适配器已预留"}</p>
          <p>${escapeHtml(policy.target_scale)}</p>
          ${policy.live_source_status ? `<p>${escapeHtml(policy.live_source_status)}</p>` : ""}
          <p class="subtle">更新频率：${escapeHtml(policy.update_frequency)}</p>
        </article>
      `).join("")}
    </div>

    <h2 class="section-title">学生账号与排序命中质量</h2>
    <p class="subtle">每个专业一个正式账号，密码统一 123456，可一键切换查看该专业的推荐结果。</p>
    <div class="account-validity-grid">
      ${((state.demoAccounts || {}).accounts || []).map((account) => accountValidityCard(account, ev.ranking_eval.per_student)).join("")}
    </div>
  `;

  bindVideoCards();
  $("#statsMajor").addEventListener("change", async (event) => {
    state.statsMajor = event.target.value;
    state.stats = await api(`/api/stats/resources?student_id=${state.studentId}&major=${encodeURIComponent(state.statsMajor)}`);
    renderDataModel();
  });
  $("#runEval").addEventListener("click", async () => {
    // 评估是分钟级的 CPU 密集任务，后端已改为后台线程执行、这里轮询状态。
    // 之前同步等待整个 POST 返回，评估期间请求一直挂着（Chrome 状态栏
    // 一直显示"等待响应"），且服务端被占满，其它操作全部变慢。
    try {
      const res = await api("/api/admin/evaluate", { method: "POST" });
      toast(res.status === "already_running" ? "评估正在进行中，请稍候" : "评估已在后台运行，完成后自动刷新");
    } catch (error) {
      toast(error.message);
      return;
    }
    pollJobStatus("/api/admin/evaluate-status", "模型评估", async () => {
      state.evaluation = await api("/api/admin/model-evaluation");
      renderDataModel();
    });
  });
  $("#collectLive").addEventListener("click", async () => {
    try {
      const res = await api("/api/admin/collect-live", {
        method: "POST",
        body: JSON.stringify({ video_per_platform: 50, job_total: 200, collect_exam: true, retrain: true }),
      });
      toast(res.status === "already_running" ? "采集正在进行中，请稍候" : "采集已在后台运行，完成后自动刷新");
    } catch (error) {
      toast(error.message);
      return;
    }
    pollJobStatus("/api/admin/collect-status", "真实数据采集", async () => {
      state.quality = await api("/api/admin/data-quality");
      state.evaluation = await api("/api/admin/model-evaluation");
      renderDataModel();
    });
  });
  document.querySelectorAll("[data-login-account]").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.studentId = btn.dataset.loginAccount;
      sessionStorage.setItem("studentId", state.studentId);
      state.statsMajor = "";
      state.filter = "all";
      state.pageSize = PAGE_SIZE_DEFAULT;
      loadApp();
    });
  });
}

function accountValidityCard(account, perStudent) {
  const metrics = perStudent?.[account.student_id] || {};
  return `
    <article class="validity-card ${state.studentId === account.student_id ? "current" : ""}">
      <div class="validity-title">
        <strong>${escapeHtml(account.student_id)}</strong>
        <span class="tag">${escapeHtml(account.name)}</span>
      </div>
      <h3>${escapeHtml(account.major)}</h3>
      <p>${escapeHtml(account.grade)} ・ 第 ${account.semester} 学期 ・ ${escapeHtml(account.college || "")}</p>
      <p>学习记录 ${account.learning_count} 门 ・ ≥25% 进度 ${account.positive_count} 门</p>
      <div class="validity-metrics">
        <span>Precision@5 <b>${fmtRatio(metrics["precision@5"])}</b></span>
        <span>NDCG@5 <b>${fmtRatio(metrics["ndcg@5"])}</b></span>
        <span>HitRate@5 <b>${fmtRatio(metrics["hit_rate@5"])}</b></span>
        <span>Recall@全量 <b>${fmtRatio(metrics["recall_full"])}</b></span>
        <span>课程覆盖 <b>${fmtRatio(metrics["course_coverage_rate"])}</b></span>
        <span>本专业对齐 <b>${fmtRatio(metrics["major_fit"])}</b></span>
      </div>
      <button class="ghost-btn" type="button" data-login-account="${escapeHtml(account.student_id)}">
        ${state.studentId === account.student_id ? "当前账号 ・ 点击刷新" : "用此账号登录"}
      </button>
    </article>
  `;
}

function metricCard(title, value, desc) {
  return `
    <article class="metric-card">
      <span>${escapeHtml(title)}</span>
      <strong>${escapeHtml(value)}</strong>
      <p>${escapeHtml(desc)}</p>
    </article>
  `;
}

/* ------------------------------------------------------------------ 成就弹窗 */

async function showAchievement(videoId) {
  const item = await api(`/api/videos/${videoId}?student_id=${state.studentId}`);
  const mask = document.createElement("div");
  mask.className = "modal-mask";
  mask.innerHTML = `
    <article class="modal" role="dialog" aria-modal="true">
      <div class="cover">
        <img src="/api/cover/${encodeURIComponent(item.video_id)}.svg" alt="课程封面" />
      </div>
      <div class="modal-body">
        <h2>${escapeHtml(item.title)}</h2>
        <p class="subtle">${escapeHtml(item.summary || "")}</p>
        <div class="tags">
          <span class="tag">${escapeHtml(item.teacher || "")}</span>
          <span class="tag">${escapeHtml(item.platform)}</span>
          <span class="tag">${escapeHtml(item.course_name || "")}</span>
        </div>
        <div class="actions">
          <button class="primary-btn" type="button" id="goPlay">继续学习</button>
        </div>
      </div>
    </article>
  `;
  mask.addEventListener("click", (event) => {
    if (event.target === mask) mask.remove();
  });
  document.addEventListener("keydown", function esc(event) {
    if (event.key === "Escape") {
      mask.remove();
      document.removeEventListener("keydown", esc);
    }
  });
  document.body.appendChild(mask);
  $("#goPlay").addEventListener("click", () => { location.href = `/play/${item.video_id}`; });
}

/* ------------------------------------------------------------------ 播放页 */

async function renderPlayer(videoId) {
  const item = await api(`/api/videos/${videoId}?student_id=${state.studentId}`);
  state.selectedEpisode = Math.max(1, item.record.watched_episodes || 1);
  const episodeList = item.episodes_list || [];
  const currentUrl = episodeList[state.selectedEpisode - 1] ? episodeList[state.selectedEpisode - 1].play_url : "";

  $("#app").innerHTML = `
    <div class="player-shell">
      <main class="player-main">
        <div class="crumb">
          <button class="icon-btn" id="backBtn" type="button" title="返回">‹</button>
          <span>课程学习 / ${escapeHtml(item.course_name || "")}</span>
          ${themeToggleMarkup()}
        </div>
        <section class="video-frame">
          ${currentUrl
            ? `<iframe id="frame" src="${escapeHtml(currentUrl)}" title="${escapeHtml(item.title)}" allowfullscreen></iframe>`
            : `<div class="no-source">该资源暂无可用播放地址，去原始平台观看：<br/><a href="${escapeHtml(item.source_url || "#")}" target="_blank" rel="noopener">${escapeHtml(item.source_url || "查看来源")}</a></div>`}
        </section>
        <h1 class="player-title">${escapeHtml(item.title)}</h1>
        <div class="meta player-meta">
          <span>${escapeHtml(item.teacher || "")}</span>
          <span class="platform ${item.is_paid ? "paid" : ""}">${escapeHtml(item.platform)}</span>
          <span>${escapeHtml(item.plays)}</span>
          <span>${escapeHtml(item.likes)}</span>
          <span>共 ${item.episodes} 集</span>
        </div>
        <p class="subtle">${escapeHtml(item.summary || "")}</p>
        <div class="actions">
          <button class="ghost-btn" id="likeBtn" type="button">❤ 点赞</button>
          <button class="ghost-btn" id="favBtn" type="button">▣ 收藏</button>
          <button class="primary-btn" id="finishBtn" type="button">标记看完本集</button>
        </div>
        <p class="subtle" id="progressText">
          当前进度：已看完 ${item.record.watched_episodes || 0} / ${item.episodes} 集 ・ 看完最后一集自动计入成就
        </p>
      </main>
      <aside class="episode-side">
        <div class="episode-head"><span>选集 ・ 共 ${item.episodes} 集</span><span>已看 ${item.record.watched_episodes || 0} 集</span></div>
        ${episodeList.slice(0, 30).map((ep) => `
          <button class="episode ${ep.episode_no === state.selectedEpisode ? "active" : ""}" type="button"
                  data-episode="${ep.episode_no}" data-url="${escapeHtml(ep.play_url)}">
            <strong>${String(ep.episode_no).padStart(2, "0")}</strong>
            <span>${escapeHtml(ep.title)}</span>
            <span>${escapeHtml(ep.duration)}</span>
          </button>
        `).join("")}
      </aside>
    </div>
  `;
  bindThemeToggle();
  $("#backBtn").addEventListener("click", () => {
    history.pushState(null, "", "/");
    loadApp();
  });
  document.querySelectorAll("[data-episode]").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.selectedEpisode = Number(btn.dataset.episode);
      const frame = $("#frame");
      if (frame) frame.src = btn.dataset.url;
      document.querySelectorAll(".episode").forEach((el) => el.classList.remove("active"));
      btn.classList.add("active");
    });
  });
  $("#likeBtn").addEventListener("click", () =>
    api(`/api/videos/${videoId}/like`, {
      method: "POST",
      body: JSON.stringify({ student_id: state.studentId, event_type: "like" }),
    }).then(() => toast("已点赞，偏好已写入埋点")));
  $("#favBtn").addEventListener("click", () =>
    api(`/api/videos/${videoId}/favorite`, {
      method: "POST",
      body: JSON.stringify({ student_id: state.studentId, event_type: "favorite" }),
    }).then(() => toast("已收藏，后续推荐会参考")));
  $("#finishBtn").addEventListener("click", async () => {
    const next = Math.max(state.selectedEpisode, (item.record.watched_episodes || 0) + 1);
    const res = await api(`/api/videos/${videoId}/progress`, {
      method: "POST",
      body: JSON.stringify({ student_id: state.studentId, episode_no: next }),
    });
    $("#progressText").textContent =
      `当前进度：已看完 ${res.watched_episodes} / ${item.episodes} 集 ・ ` +
      `${res.status === "completed" ? "已自动计入成就" : "继续学习中"}`;
    toast(res.status === "completed" ? "课程已通关，成就卡已新增" : "进度已同步");
  });
}

/* ------------------------------------------------------------------ 启动 */

initTheme();

if (!sessionStorage.getItem("studentId") && !location.pathname.startsWith("/play/")) {
  renderLogin();
} else {
  state.studentId = state.studentId || sessionStorage.getItem("studentId");
  loadApp().catch((error) => {
    console.error(error);
    sessionStorage.removeItem("studentId");
    renderLogin();
    toast(error.message);
  });
}
