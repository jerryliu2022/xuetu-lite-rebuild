const API = "";
let state = {
  studentId: sessionStorage.getItem("studentId") || "20240101",
  tab: "professional",
  filter: "all",
  profile: null,
  professional: [],
  jobs: null,
  exam: null,
  quality: null,
  evaluation: null,
  demoAccounts: null,
  selectedEpisode: 1,
};

const $ = (selector) => document.querySelector(selector);

function api(path, options = {}) {
  return fetch(`${API}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  }).then(async (res) => {
    if (!res.ok) throw new Error((await res.json()).detail || "请求失败");
    return res.json();
  });
}

function toast(text) {
  const node = document.createElement("div");
  node.className = "toast";
  node.textContent = text;
  document.body.appendChild(node);
  setTimeout(() => node.remove(), 1900);
}

function brand() {
  return `<div class="brand"><div class="brand-mark">◆</div><span>学途 Lite</span></div>`;
}

const DEMO_ACCOUNT_FALLBACK = [
  { student_id: "20240101", name: "李明", major: "计算机科学与技术" },
  { student_id: "20240102", name: "周然", major: "软件工程" },
  { student_id: "20240103", name: "陈思", major: "数据科学与大数据技术" },
  { student_id: "20240104", name: "林澈", major: "人工智能" },
  { student_id: "20240105", name: "赵程", major: "网络工程" },
];

async function renderLogin() {
  let demoAccounts = DEMO_ACCOUNT_FALLBACK;
  try {
    const result = await api("/api/demo-accounts");
    if (result.accounts && result.accounts.length) demoAccounts = result.accounts;
  } catch (error) {
    console.warn(error);
  }
  $("#app").innerHTML = `
    <main class="login-screen">
      <form class="login-card" id="loginForm">
        ${brand()}
        <h1>欢迎回来</h1>
        <p class="subtle">登录后开启你的个性化学习路径</p>
        <div class="field">
          <label>学号</label>
          <input name="student_id" value="20240101" placeholder="请输入学号" />
        </div>
        <div class="field">
          <label>密码</label>
          <input name="password" type="password" value="123456" placeholder="请输入密码" />
        </div>
        <button class="primary-btn">登 录</button>
        <div class="sso-note">已接入校园系统 ・ 学号透传免登录校验</div>
        <div class="login-accounts">
          <div class="subtle">5 个专业演示账号，密码均为 123456，可一键登录</div>
          <div class="account-preset-grid">
            ${demoAccounts.map((account) => `
              <button type="button" class="account-preset" data-demo-account="${account.student_id}" data-demo-name="${account.name}" data-demo-major="${account.major}">
                <strong>${account.student_id}</strong>
                <span>${account.name} ・ ${account.major}</span>
              </button>
            `).join("")}
          </div>
        </div>
      </form>
    </main>
  `;
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
      await loadApp();
    } catch (error) {
      toast(error.message);
    }
  });
}

async function loadApp() {
  const route = location.pathname.match(/^\/play\/(.+)$/);
  if (route) {
    await renderPlayer(route[1]);
    return;
  }
  state.profile = await api(`/api/profile/${state.studentId}`);
  state.professional = await api(`/api/recommendations/professional?student_id=${state.studentId}&filter=${state.filter}`);
  state.jobs = await api(`/api/recommendations/jobs?student_id=${state.studentId}`);
  state.exam = await api(`/api/recommendations/exam?student_id=${state.studentId}`);
  state.quality = await api("/api/admin/data-quality");
  state.evaluation = await api("/api/admin/model-evaluation");
  state.demoAccounts = await api("/api/demo-accounts");
  renderShell();
}

function renderShell() {
  $("#app").innerHTML = `
    <div class="shell">
      ${renderSidebar()}
      <main class="main">
        <div class="topbar">
          <label class="search"><span>⌕</span><input placeholder="搜索课程 / 岗位 / 院校" /></label>
          <button class="data-entry" data-tab="data">数据质量报告</button>
          <button class="icon-btn" title="通知">♢</button>
          <button class="ghost-btn" id="switchAccount">切换账号</button>
        </div>
        <nav class="tabs">
          ${tabButton("professional", "专业路径推荐")}
          ${tabButton("job", "就业路径推荐")}
          ${tabButton("exam", "考研路径推荐")}
          ${tabButton("data", "数据与模型")}
        </nav>
        <section class="content" id="content"></section>
      </main>
    </div>
  `;
  document.querySelectorAll("[data-tab]").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.tab = btn.dataset.tab;
      renderShell();
    });
  });
  $("#switchAccount").addEventListener("click", () => {
    sessionStorage.removeItem("studentId");
    history.pushState(null, "", "/");
    location.reload();
  });
  $(".sidebar .icon-btn").addEventListener("click", () => $(".sidebar").classList.toggle("collapsed"));
  renderContent();
}

function tabButton(id, text) {
  return `<button class="tab ${state.tab === id ? "active" : ""}" data-tab="${id}">${text}</button>`;
}

function renderSidebar() {
  const { student, courses, achievements } = state.profile;
  return `
    <aside class="sidebar">
      <div class="side-head">${brand()}<button class="icon-btn hide-when-collapsed">‹</button></div>
      <section class="student-card hide-when-collapsed">
        <div class="student-main">
          <div class="avatar"></div>
          <div><h2>${student.name}</h2><div class="subtle">${student.major} ・ ${student.grade}</div></div>
        </div>
        <div class="level-row"><strong>Lv.${student.level} 进阶者</strong><span>${student.xp} / ${student.next_xp}</span></div>
        <div class="progress"><span style="width:${Math.min(100, (student.xp / student.next_xp) * 100)}%"></span></div>
      </section>
      <div class="hide-when-collapsed">
        <div class="side-title">内修 ・ 本学期晋级</div>
        <section class="path-card">
          ${courses.slice(0, 8).map((course) => `
            <div class="course-node ${course.status}">
              <span class="dot"></span><span>${course.name}</span><strong>${statusText(course.status)}</strong>
            </div>
          `).join("")}
          <div class="term-key"><span>上学期</span><span>本学期</span><span>下学期</span></div>
        </section>
        <div class="side-title">成就 ・ 已通关</div>
        <section class="achieve-panel">
          <div class="achievement-stack">
            ${achievements.slice(0, 4).map((item, index) => `
              <button class="achievement" data-achieve="${item.video_id}" style="--i:${index};background:linear-gradient(100deg, ${item.cover_color}, ${index % 2 ? "var(--cyan)" : "var(--violet)"})">
                ${item.title}<small>${item.platform} ・ 已通关</small>
              </button>
            `).join("")}
          </div>
          <div class="hint">点击卡片 ・ 抽牌查看课程详情</div>
        </section>
      </div>
    </aside>
  `;
}

function statusText(status) {
  return status === "completed" ? "已学" : status === "studying" ? "正在学" : "未学";
}

function renderContent() {
  if (state.tab === "professional") renderProfessional();
  if (state.tab === "job") renderJobs();
  if (state.tab === "exam") renderExam();
  if (state.tab === "data") renderDataModel();
  document.querySelectorAll("[data-achieve]").forEach((btn) => {
    btn.addEventListener("click", () => showAchievement(btn.dataset.achieve));
  });
}

function renderProfessional() {
  $("#content").innerHTML = `
    <div class="page-head">
      <div><h1>专业路径推荐</h1><div class="subtle">根据你的专业与学习进度，为你定制个性化学习路径</div></div>
      <button class="ghost-btn" id="refreshProfessional">换一批</button>
    </div>
    <div class="filters">
      ${filterButton("all", "全部")}
      ${filterButton("studying", "正在学相关")}
      ${filterButton("advanced", "已学进阶")}
      ${filterButton("vacation", "寒暑假预习")}
    </div>
    <div class="course-grid">${state.professional.map(videoCard).join("")}</div>
    <h2 class="section-title">热门课程榜</h2>
    <div class="hot-list">${state.professional.slice(0, 3).map((item, index) => `
      <button class="hot-item" data-video="${item.video_id}"><b>${String(index + 1).padStart(2, "0")}</b><span>${item.title}</span><span>${item.plays}</span></button>
    `).join("")}</div>
  `;
  bindVideoCards();
  document.querySelectorAll("[data-filter]").forEach((btn) => btn.addEventListener("click", async () => {
    state.filter = btn.dataset.filter;
    state.professional = await api(`/api/recommendations/professional?student_id=${state.studentId}&filter=${state.filter}`);
    renderShell();
  }));
  $("#refreshProfessional").addEventListener("click", () => {
    state.professional = [...state.professional].sort(() => Math.random() - 0.5);
    renderProfessional();
  });
}

function filterButton(id, text) {
  return `<button class="pill ${state.filter === id ? "active" : ""}" data-filter="${id}">${text}</button>`;
}

function videoCard(item) {
  return `
    <button class="video-card" data-video="${item.video_id}">
      <div class="cover" style="--cover:${item.cover_color}"></div>
      <h3>${item.title}</h3>
      <div class="meta"><span class="platform ${item.is_paid ? "paid" : ""}">${item.platform}</span><span>${item.plays}</span><span>${item.is_paid ? "付费" : "免费"}</span></div>
      <div class="score">${item.reason || item.summary}<br />综合推荐分 ${(item.score * 100 || 82).toFixed(1)}%${item.predicted_ctr ? ` ・ 模型预测 ${(item.predicted_ctr * 100).toFixed(1)}%` : ""}</div>
    </button>
  `;
}

function bindVideoCards() {
  document.querySelectorAll("[data-video]").forEach((card) => {
    card.addEventListener("click", () => {
      api("/api/track", { method: "POST", body: JSON.stringify({ student_id: state.studentId, video_id: card.dataset.video, event_type: "click" }) });
      location.href = `/play/${card.dataset.video}`;
    });
  });
}

function renderJobs() {
  const { jobs, selected, path } = state.jobs;
  $("#content").innerHTML = `
    <div class="page-head">
      <div><h1>就业路径推荐</h1><div class="subtle">根据你的专业匹配岗位 ・ 反推技能学习路径</div></div>
      <button class="ghost-btn" id="shuffleJobs">换一批岗位</button>
    </div>
    <h2 class="side-title">为你匹配的岗位</h2>
    <div class="job-grid">${jobs.slice(0, 3).map(job => `
      <button class="job-card ${job.job_id === selected.job_id ? "active" : ""}" data-job="${job.job_id}">
        <h3>${job.title}</h3>
        <div class="subtle">${job.company} ・ ${job.city}</div>
        <div class="salary">${job.salary} ・ ${job.fresh}</div>
        <div class="match ${job.major_match === "一般匹配" ? "mid" : job.major_match === "相关延伸" ? "low" : ""}">${job.major_match}</div>
      </button>
    `).join("")}</div>
    <section class="detail-card">
      <div class="page-head">
        <div><h2>招聘详情 ・ ${selected.title}</h2><p class="subtle">${selected.requirement}</p></div>
        <a class="tab active" target="_blank" href="${selected.source_url}">查看原招聘 ・ ${selected.source}</a>
      </div>
      <div class="tags">${selected.skills.split(",").map(skill => `<span class="tag">${skill}</span>`).join("")}</div>
    </section>
    <h2 class="side-title">技能反推 ・ 学习路径（按先后顺序）</h2>
    <div class="path-row">${path.slice(0, 6).map((item, index) => pathStep(item, index)).join("")}</div>
  `;
  document.querySelectorAll("[data-job]").forEach((btn) => btn.addEventListener("click", async () => {
    state.jobs = await api(`/api/recommendations/jobs?student_id=${state.studentId}&job_id=${btn.dataset.job}`);
    renderJobs();
  }));
  bindVideoCards();
  $("#shuffleJobs").addEventListener("click", () => toast("岗位池已按专业匹配度刷新"));
}

function pathStep(item, index) {
  return `
    <button class="path-step" data-video="${item.video_id}">
      <div class="step-no">第 ${index + 1} 步</div>
      <h3>${item.title}</h3>
      <div class="meta" style="padding:0"><span class="platform ${item.is_paid ? "paid" : ""}">${item.platform}</span><span>${item.is_paid ? "付费" : "免费"}</span></div>
    </button>
  `;
}

function renderExam() {
  const { public: pub, professional, school, major } = state.exam;
  $("#content").innerHTML = `
    <div class="page-head">
      <div><h1>考研路径推荐</h1><div class="subtle">默认按你的专业推荐 ・ 可输入目标院校专业重新生成</div></div>
      <button class="ghost-btn" id="regenExam">重新生成</button>
    </div>
    <form class="exam-form" id="examForm">
      <div><label class="subtle">目标院校</label><input name="school" value="${school === "默认" ? "" : school}" placeholder="如：清华大学" /></div>
      <div><label class="subtle">目标专业</label><input name="major" value="${major}" placeholder="如：计算机科学与技术" /></div>
      <button class="primary-btn">查询考研科目</button>
    </form>
    <h2 class="section-title">阶段 1 ・ 公共考研课</h2>
    <div class="course-grid">${pub.map(videoCard).join("")}</div>
    <h2 class="section-title blue">阶段 2 ・ 专业考研课（${state.exam.subjects.join(" / ")}）</h2>
    <div class="course-grid">${professional.map(videoCard).join("")}</div>
  `;
  $("#examForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const data = Object.fromEntries(new FormData(event.target));
    state.exam = await api(`/api/recommendations/exam?student_id=${state.studentId}&school=${encodeURIComponent(data.school)}&major=${encodeURIComponent(data.major)}`);
    renderExam();
  });
  $("#regenExam").addEventListener("click", () => $("#examForm").requestSubmit());
  bindVideoCards();
}

function renderDataModel() {
  const q = state.quality;
  const ev = state.evaluation;
  const avg = ev.ranking_eval.averages;
  $("#content").innerHTML = `
    <div class="page-head">
      <div><h1>数据与模型</h1><div class="subtle">采集覆盖、更新频率、模型效果与推荐质量测试</div></div>
      <div class="actions-inline">
        <button class="ghost-btn" id="collectLive">采集真实数据</button>
        <button class="ghost-btn" id="runEval">重新评估</button>
        <button class="ghost-btn" id="rebuildProfiles">重建5专业画像</button>
      </div>
    </div>
      <section class="truth-card">
        <strong>数据真实性说明</strong>
        <div>${q.truth_statement}</div>
        <div class="freshness-line">报告生成时间：${q.freshness.generated_at} ・ 视频只计最近 6 年、招聘只计最近 2 年、考研科目只计最新年份。</div>
      </section>
    <section class="account-section">
      <div class="account-section-head">
        <div><h2>5 个专业学生账号与有效性</h2><p class="subtle">每专业一个正式账号，均可用学号 / 123456 登录，展示当前账号在模型中的排序命中质量。</p></div>
        <button class="ghost-btn" data-login-account="${state.studentId}">刷新当前账号</button>
      </div>
      <div class="account-validity-grid">
        ${(state.demoAccounts?.accounts || []).map((account) => accountValidityCard(account, ev.ranking_eval.per_student)).join("")}
      </div>
    </section>
    <div class="metric-grid">
        ${metricCard("视频课程", q.video.live_recent_videos, `真实/库内 ${q.video.live_recent_videos || 0}/${q.video.total_videos} ・ 专业覆盖 ${(q.video.major_coverage_rate * 100).toFixed(1)}%`)}
        ${metricCard("招聘岗位", q.job.live_recent_jobs, `真实/库内 ${q.job.live_recent_jobs || 0}/${q.job.total_jobs} ・ 拉勾 ${q.job.lagou_count} / BOSS ${q.job.boss_count}`)}
        ${metricCard("考研科目", q.exam.latest_records, `2027 官方目录 ${q.exam.latest_records || 0} 条 ・ 专业覆盖 ${(q.exam.major_coverage_rate * 100).toFixed(1)}%`)}
      ${metricCard("训练 AUC", ev.offline_train_report.auc, `样本 ${ev.offline_train_report.sample_count} 条 ・ CPU 轻量排序`)}
    </div>
    <section class="detail-card">
      <h2>最近采集结果</h2>
      <div class="run-list">
        ${(q.collection_history || []).length ? q.collection_history.map(run => `
          <article class="run-item">
            <strong>${run.domain} / ${run.source}</strong>
            <span>${run.inserted_count} / ${run.target_count} 入库</span>
            <span>${run.ok ? "达标" : "未达标"}</span>
            <p>${run.message}</p>
          </article>
        `).join("") : `<p class="subtle">尚未执行真实联网采集。</p>`}
      </div>
    </section>
    <section class="detail-card">
      <h2>来源与更新策略</h2>
      <div class="source-grid">
        ${q.source_policies.map(policy => `
          <article class="source-card">
            <h3>${policy.domain}</h3>
            <div class="tags">${policy.sources.map(source => `<span class="tag">${source}</span>`).join("")}</div>
            <p class="subtle">当前状态：${policy.live_collected ? "已接真实采集" : "演示种子数据，采集适配器已预留"}</p>
            <p>${policy.target_scale}</p>
            ${policy.live_source_status ? `<p>${policy.live_source_status}</p>` : ""}
            <p class="subtle">更新频率：${policy.update_frequency}</p>
          </article>
        `).join("")}
      </div>
    </section>
    <section class="detail-card">
      <h2>模型效果测试方案</h2>
      <div class="metric-grid compact">
        ${metricCard("Precision@5", avg["precision@5"], "前 5 个推荐中命中弱正样本比例")}
        ${metricCard("Recall@5", avg["recall@5"], "弱正样本召回能力")}
        ${metricCard("NDCG@5", avg["ndcg@5"], "排序位置质量")}
        ${metricCard("覆盖率", avg.catalog_coverage, "推荐列表覆盖课程库比例")}
        ${metricCard("多样性", avg.platform_diversity, "MOOC / B站 / 极客时间平台分散度")}
        ${metricCard("HitRate@5", avg["hit_rate@5"], "学生维度前 5 命中率")}
      </div>
      <ol class="plan-list">
        ${ev.effect_improvement_plan.map(item => `<li>${item}</li>`).join("")}
      </ol>
    </section>
    <section class="detail-card">
      <h2>覆盖结论</h2>
      <p class="subtle">视频是否覆盖所有专业：${q.video.all_majors_covered ? "是" : "否"}。当前覆盖：${q.video.covered_majors.join("、") || "暂无"}。</p>
      <p class="subtle">岗位是否覆盖所有专业：${q.job.all_majors_covered ? "是" : "否"}。当前覆盖：${q.job.covered_majors.join("、") || "暂无"}。</p>
      <p class="subtle">考研科目是否覆盖所有专业：${q.exam.all_majors_covered ? "是" : "否"}。当前覆盖：${q.exam.covered_majors.join("、") || "暂无"}。</p>
      <p class="subtle">下一次计划更新：视频 ${q.freshness.next_video_incremental}；岗位 ${q.freshness.next_job_incremental}；考研 ${q.freshness.next_exam_check}。</p>
    </section>
  `;
  $("#runEval").addEventListener("click", async () => {
    state.evaluation = await api("/api/admin/evaluate", { method: "POST" });
    toast("模型评估已重新生成");
    renderDataModel();
  });
  $("#rebuildProfiles").addEventListener("click", async () => {
    toast("正在按当前课程库重建 5 专业画像并重训模型");
    const result = await api("/api/admin/rebuild-profiles", { method: "POST" });
    state.demoAccounts = result.accounts;
    state.quality = await api("/api/admin/data-quality");
    state.evaluation = await api("/api/admin/model-evaluation");
    toast("画像重建与模型重训完成");
    renderDataModel();
  });
  document.querySelectorAll("[data-login-account]").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.studentId = btn.dataset.loginAccount;
      sessionStorage.setItem("studentId", state.studentId);
      loadApp();
    });
  });
  $("#collectLive").addEventListener("click", async () => {
    toast("开始采集，可能需要几分钟");
    await api("/api/admin/collect-live", {
      method: "POST",
      body: JSON.stringify({ video_per_platform: 50, job_total: 200, collect_exam: true, retrain: true }),
    });
    state.quality = await api("/api/admin/data-quality");
    state.evaluation = await api("/api/admin/model-evaluation");
    toast("真实数据采集流程已结束");
    renderDataModel();
  });
}

function accountValidityCard(account, perStudent) {
  const metrics = perStudent?.[account.student_id] || {};
  const fmt = (value) => (value === undefined || value === null) ? "--" : Number.isInteger(value) ? String(value) : value <= 1 ? value.toFixed(4) : value;
  return `
    <article class="validity-card ${state.studentId === account.student_id ? "current" : ""}">
      <div class="validity-title">
        <strong>${account.student_id}</strong>
        <span class="tag">${account.name}</span>
      </div>
      <h3>${account.major}</h3>
      <p>${account.grade} ・ ${account.semester} 学期</p>
      <p>学习记录 ${account.learning_count} 门 ・ ≥25%进度 ${account.positive_count} 门 ・ 同专业真实视频 ${account.major_match_videos}/${account.learning_count}</p>
      <div class="validity-metrics">
        <span>Precision@5 <b>${fmt(metrics["precision@5"])}</b></span>
        <span>Recall@5 <b>${fmt(metrics["recall@5"])}</b></span>
        <span>NDCG@5 <b>${fmt(metrics["ndcg@5"])}</b></span>
        <span>HitRate@5 <b>${fmt(metrics["hit_rate@5"])}</b></span>
      </div>
      <button class="ghost-btn" data-login-account="${account.student_id}">${state.studentId === account.student_id ? "当前账号，点击刷新" : "用此账号登录"}</button>
    </article>
  `;
}

function metricCard(title, value, desc) {
  const display = Number.isInteger(value) ? String(value) : typeof value === "number" && value <= 1 ? value.toFixed(4) : value;
  return `
    <article class="metric-card">
      <span>${title}</span>
      <strong>${display}</strong>
      <p>${desc}</p>
    </article>
  `;
}

async function showAchievement(videoId) {
  const item = await api(`/api/videos/${videoId}?student_id=${state.studentId}`);
  const mask = document.createElement("div");
  mask.className = "modal-mask";
  mask.innerHTML = `
    <article class="modal">
      <div class="cover" style="--cover:${item.cover_color}"></div>
      <div class="modal-body">
        <h2>${item.title}</h2>
        <p class="subtle">${item.summary}</p>
        <div class="tags"><span class="tag">${item.teacher}</span><span class="tag">${item.org}</span><span class="tag">${item.platform}</span></div>
      </div>
    </article>
  `;
  mask.addEventListener("click", () => mask.remove());
  document.body.appendChild(mask);
}

async function renderPlayer(videoId) {
  const item = await api(`/api/videos/${videoId}?student_id=${state.studentId}`);
  state.selectedEpisode = Math.max(1, item.record.watched_episodes || 1);
  $("#app").innerHTML = `
    <div class="player-shell">
      <main class="player-main">
        <div class="crumb"><button class="icon-btn" id="backBtn">‹</button><span>专业路径推荐 / ${item.title.split("・")[0]}</span></div>
        <section class="video-frame" style="background:${item.cover_color}">
          <iframe id="frame" src="${item.episodes_list[state.selectedEpisode - 1].play_url}" allowfullscreen></iframe>
        </section>
        <h1 class="player-title">${item.title}</h1>
        <div class="meta" style="padding:0"><div class="avatar" style="width:48px;height:48px"></div><span>${item.teacher}</span><span class="platform ${item.is_paid ? "paid" : ""}">${item.platform}</span><span>${item.plays}</span><span>已更新至 ${item.episodes} 集</span></div>
        <div class="actions">
          <button class="ghost-btn" id="likeBtn">❤ 点赞 2.1万</button>
          <button class="ghost-btn" id="favBtn">▣ 收藏</button>
          <button class="ghost-btn" id="finishBtn">标记看完本集</button>
        </div>
        <p class="subtle" id="progressText">当前进度：已看完 ${item.record.watched_episodes || 0} / ${item.episodes} 集 ・ 看完最后一集自动计入成就</p>
      </main>
      <aside class="episode-side">
        <div class="episode-head"><span>选集 ・ 共 ${item.episodes} 集</span><span>已看 ${item.record.watched_episodes || 0} 集</span></div>
        ${item.episodes_list.slice(0, 24).map(ep => `
          <button class="episode ${ep.episode_no === state.selectedEpisode ? "active" : ""}" data-episode="${ep.episode_no}" data-url="${ep.play_url}">
            <strong>${String(ep.episode_no).padStart(2, "0")}</strong><span>${ep.title.replace(item.title.split("・")[0], "").trim()}</span><span>${ep.duration}</span>
          </button>
        `).join("")}
        <button class="ghost-btn" style="width:100%;margin-top:18px">展开全部 ${item.episodes} 集</button>
      </aside>
    </div>
  `;
  $("#backBtn").addEventListener("click", () => {
    history.pushState(null, "", "/");
    loadApp();
  });
  document.querySelectorAll("[data-episode]").forEach((btn) => btn.addEventListener("click", () => {
    state.selectedEpisode = Number(btn.dataset.episode);
    $("#frame").src = btn.dataset.url;
    document.querySelectorAll(".episode").forEach(el => el.classList.remove("active"));
    btn.classList.add("active");
  }));
  $("#likeBtn").addEventListener("click", () => api(`/api/videos/${videoId}/like`, { method: "POST", body: JSON.stringify({ student_id: state.studentId, event_type: "like" }) }).then(() => toast("已点赞，偏好已写入埋点")));
  $("#favBtn").addEventListener("click", () => api(`/api/videos/${videoId}/favorite`, { method: "POST", body: JSON.stringify({ student_id: state.studentId, event_type: "favorite" }) }).then(() => toast("已收藏，后续推荐会参考")));
  $("#finishBtn").addEventListener("click", async () => {
    const next = Math.max(state.selectedEpisode, (item.record.watched_episodes || 0) + 1);
    const res = await api(`/api/videos/${videoId}/progress`, { method: "POST", body: JSON.stringify({ student_id: state.studentId, episode_no: next }) });
    $("#progressText").textContent = `当前进度：已看完 ${res.watched_episodes} / ${item.episodes} 集 ・ ${res.status === "completed" ? "已自动计入成就" : "继续学习中"}`;
    toast(res.status === "completed" ? "课程已通关，成就卡已新增" : "进度已同步");
  });
}

if (!sessionStorage.getItem("studentId") && !location.pathname.startsWith("/play/")) {
  renderLogin();
} else {
  loadApp().catch((error) => {
    console.error(error);
    renderLogin();
    toast(error.message);
  });
}
