      (() => {
        "use strict";

        const app = document.querySelector("#app");
        const PAGE = document.body.dataset.page || "home";
        const PAGE_ROLE = document.body.dataset.role || "";
        const SESSION_KEY = "purslyx-web-session-v1";
        const DEFAULT_RESUME = `张三
工作经历：
负责 React 前端项目开发，使用 TypeScript 和 Vue 完成组件设计。
通过性能优化和自动化测试提升交付质量。
项目经历：
参与用户后台改版，推动需求分析、开发和上线。`;
        const DEFAULT_JOB = `前端开发工程师
公司：示例科技
地点：杭州
薪资：18K-28K
任职要求：
1、熟悉 React 或 Vue 与 TypeScript
2、负责前端项目开发和交付
3、关注性能优化与自动化测试`;

        function initialWorkspacePage() {
          const pages = new Set(["dashboard", "resume", "pool", "report", "rewrite", "variants", "interview", "tasks", "usage", "stats", "admin-metrics", "admin-users", "admin-roles", "admin-usage", "admin-logs"]);
          return pages.has(PAGE) ? PAGE : "dashboard";
        }

        function initialScreen() {
          return PAGE === "home" ? "demo" : "formal";
        }

        function navigationRole() {
          return state.session.account?.registration_role || PAGE_ROLE || "seeker";
        }

        function routeFor(page, query = {}) {
          const role = navigationRole();
          const routes = role === "recruiter"
            ? { dashboard: "/app/recruiter/dashboard", resume: "/app/recruiter/materials", report: "/app/recruiter/report", tasks: "/app/recruiter/tasks", usage: "/app/recruiter/usage", stats: "/app/recruiter/stats" }
            : { dashboard: "/app/seeker/dashboard", resume: "/app/seeker/resume", pool: "/app/seeker/pool", report: "/app/seeker/report", rewrite: "/app/seeker/rewrite", variants: "/app/seeker/variants", interview: "/app/seeker/interview", tasks: "/app/seeker/tasks", usage: "/app/seeker/usage", stats: "/app/seeker/stats" };
          const path = page.startsWith("admin-") ? `/app/admin/${page.slice(6)}` : routes[page] || routes.dashboard;
          const params = new URLSearchParams(query);
          return `${path}${params.toString() ? `?${params.toString()}` : ""}`;
        }

        function currentRouteIsAuth() {
          return PAGE === "login" || PAGE === "register";
        }

        const state = {
          screen: initialScreen(),
          health: { status: "loading", environment: "201", backend: "PostgreSQL" },
          toast: "",
          demo: {
            resumeTitle: "演示简历",
            resumeText: DEFAULT_RESUME,
            jobTitle: "前端开发工程师 JD",
            jobText: DEFAULT_JOB,
            running: false,
            phase: "等待执行",
            logs: [],
            result: null,
            error: "",
          },
          auth: { mode: document.body.dataset.authMode || "login", email: "", password: "", role: "seeker", token: "", notice: "", error: "", running: false },
          session: loadSession(),
          workspace: {
            page: initialWorkspacePage(),
            loading: false,
            loaded: false,
            error: "",
            requestNo: 0,
            selectedPool: null,
            selectedAnalysis: null,
            selectedInterview: null,
            selectedVariant: null,
            selectedPreference: null,
            selectedRewrite: null,
            selectedTask: null,
            selectedTaskEtag: "",
            rewriteSource: null,
            rewriteSegmentKeys: [],
            pendingDocument: null,
            pendingDocumentDraft: null,
            browserDraft: null,
            browserDraftError: "",
            recruiterSubjectId: "",
            selectedAdminUser: null,
            adminLogType: "operations",
            adminLogFormat: "csv",
            adminLogFilters: { created_from: "", created_to: "", account_id: "", result: "", request_id: "", action: "", event_type: "", task_type: "", task_status: "", retry_count_min: "" },
            adminMetricFilters: { date_from: "", date_to: "", registration_role: "all", feature: "all" },
            statsDate: "",
            selectedFeedback: null,
            selectedRole: null,
            selectedLog: null,
            lastExport: null,
            lastLogExport: null,
            pollTimer: null,
            pollInFlight: false,
            data: { documents: [], preferences: [], facts: [], pool: [], analyses: [], interviews: [], variants: [], tasks: [], usage: null, stats: null, admin: { metrics: null, users: [], roles: [], grants: [], costs: null, feedback: [], logs: { operations: [], security: [], tasks: [] } } },
          },
        };

        function loadSession() {
          try {
            // 浏览器同步会打开新标签页，正式会话需要在同源标签页之间恢复。
            const stored = localStorage.getItem(SESSION_KEY) || sessionStorage.getItem(SESSION_KEY);
            return stored ? JSON.parse(stored) : { token: "", csrf: "", account: null };
          } catch (_) {
            return { token: "", csrf: "", account: null };
          }
        }

        function saveSession() {
          if (!state.session.token) {
            localStorage.removeItem(SESSION_KEY);
            sessionStorage.removeItem(SESSION_KEY);
            return;
          }
          localStorage.setItem(SESSION_KEY, JSON.stringify(state.session));
          sessionStorage.setItem(SESSION_KEY, JSON.stringify(state.session));
        }

        function clearSession() {
          if (state.workspace.pollTimer) window.clearTimeout(state.workspace.pollTimer);
          state.workspace.pollTimer = null;
          state.workspace.pollInFlight = false;
          state.session = { token: "", csrf: "", account: null };
          saveSession();
          state.workspace.page = "dashboard";
          state.workspace.loaded = false;
          state.workspace.loading = false;
          state.workspace.error = "";
          state.workspace.requestNo += 1;
          state.workspace.selectedPool = null;
          state.workspace.selectedAnalysis = null;
          state.workspace.selectedInterview = null;
          state.workspace.selectedVariant = null;
          state.workspace.selectedPreference = null;
          state.workspace.selectedRewrite = null;
          state.workspace.selectedTask = null;
          state.workspace.selectedTaskEtag = "";
          state.workspace.rewriteSource = null;
          state.workspace.rewriteSegmentKeys = [];
          state.workspace.pendingDocumentDraft = null;
          state.workspace.browserDraft = null;
          state.workspace.browserDraftError = "";
          state.workspace.recruiterSubjectId = "";
          state.workspace.selectedAdminUser = null;
          state.workspace.adminMetricFilters = { date_from: "", date_to: "", registration_role: "all", feature: "all" };
          state.workspace.statsDate = "";
          state.workspace.selectedFeedback = null;
          state.workspace.selectedRole = null;
          state.workspace.selectedLog = null;
          state.workspace.lastExport = null;
          state.workspace.lastLogExport = null;
          state.workspace.data = { documents: [], preferences: [], facts: [], pool: [], analyses: [], interviews: [], variants: [], tasks: [], usage: null, stats: null, admin: { metrics: null, users: [], roles: [], grants: [], costs: null, feedback: [], logs: { operations: [], security: [], tasks: [] } } };
        }

        function esc(value) {
          return String(value ?? "")
            .replaceAll("&", "&amp;")
            .replaceAll("<", "&lt;")
            .replaceAll(">", "&gt;")
            .replaceAll('"', "&quot;")
            .replaceAll("'", "&#39;");
        }

        function key(prefix) {
          const random = Math.random().toString(36).slice(2, 10);
          return `${prefix}-${Date.now()}-${random}`;
        }

        function json(value) {
          return JSON.stringify(value, null, 2);
        }

        function number(value, fallback = "—") {
          const parsed = Number(value);
          return Number.isFinite(parsed) ? String(parsed) : fallback;
        }

        function percent(value, fallback = "—") {
          const parsed = Number(value);
          return Number.isFinite(parsed) ? `${Math.round(parsed * 100)}%` : fallback;
        }

        function date(value) {
          if (!value) return "—";
          const parsed = new Date(value);
          if (Number.isNaN(parsed.getTime())) return "—";
          return parsed.toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
        }

        function labelStatus(value) {
          return {
            supported: "有依据",
            partially_supported: "部分依据",
            gap: "存在差距",
            needs_confirmation: "待确认",
            matched: "符合",
            conflicted: "条件冲突",
            unknown: "未知",
            unrestricted: "不限",
            negotiable: "面议",
            prefer: "倾向",
            important: "重要",
            required: "必须",
            new: "待处理",
            reviewed: "已查看",
            closed: "已关闭",
            available: "已完成",
            succeeded: "已完成",
            queued: "排队中",
            running: "处理中",
            retry_wait: "等待重试",
            needs_input: "待补充",
            exporting: "导出中",
            opening: "准备中",
            opening_failed: "开场失败",
            awaiting_requirements: "待分析",
            failed: "失败",
            completed: "已完成",
            confirmed: "已确认",
            ended_early: "提前结束",
            awaiting_answer: "等待回答",
            processing: "处理中",
          }[value] || value || "未知";
        }

        function statusClass(value) {
          if (["supported", "matched", "available", "succeeded", "completed", "reviewed", "ended_early"].includes(value)) return "success";
          if (["gap", "conflicted", "failed"].includes(value)) return "attention";
          if (["needs_confirmation", "unknown", "queued", "running", "retry_wait", "needs_input", "exporting", "opening", "awaiting_requirements", "processing", "awaiting_answer"].includes(value)) return "opportunity";
          return "neutral";
        }

        class ApiError extends Error {
          constructor(status, code, message, action) {
            super(message);
            this.status = status;
            this.code = code;
            this.action = action;
          }
        }

        function requestInit(options = {}) {
          const method = String(options.method || "GET").toUpperCase();
          const headers = { Accept: "application/json", ...(options.headers || {}) };
          const formal = Boolean(options.formal);
          if (formal && state.session.token) headers.Authorization = `Bearer ${state.session.token}`;
          if (formal && !["GET", "HEAD", "OPTIONS"].includes(method)) {
            const csrf = state.session.csrf || readCookie("purslyx_csrf");
            if (csrf) headers["X-CSRF-Token"] = csrf;
          }
          if (options.idempotencyKey) headers["Idempotency-Key"] = options.idempotencyKey;
          const init = { method, headers, credentials: "same-origin", cache: "no-store" };
          if (options.formData !== undefined) {
            // 浏览器会自动补 multipart boundary，不能手动设置 Content-Type。
            init.body = options.formData;
          } else if (options.body !== undefined) {
            headers["Content-Type"] = "application/json";
            init.body = JSON.stringify(options.body);
          }
          return init;
        }

        function readCookie(name) {
          const prefix = `${name}=`;
          const value = document.cookie.split(";").map((item) => item.trim()).find((item) => item.startsWith(prefix));
          return value ? decodeURIComponent(value.slice(prefix.length)) : "";
        }

        async function requestDetailed(path, options = {}) {
          const response = await fetch(path, requestInit(options));
          let payload = null;
          try {
            const text = await response.text();
            payload = text ? JSON.parse(text) : null;
          } catch (_) {
            payload = null;
          }
          if (response.status === 304) return { response, data: null };
          if (!response.ok) {
            const error = payload && payload.error ? payload.error : {};
            throw new ApiError(response.status, error.code || "REQUEST_FAILED", error.message || `请求失败（${response.status}）`, error.action);
          }
          return { response, data: payload && Object.prototype.hasOwnProperty.call(payload, "data") ? payload.data : payload };
        }

        async function request(path, options = {}) {
          const result = await requestDetailed(path, options);
          return result.data;
        }

        async function loadHealth() {
          try {
            const response = await fetch("/health", { cache: "no-store" });
            const payload = await response.json();
            if (!response.ok || payload.status !== "ok" || payload.database?.backend !== "postgresql") throw new Error("201 PostgreSQL 不可用");
            state.health = { status: "ok", environment: payload.environment || "201", backend: "PostgreSQL" };
          } catch (_) {
            state.health = { status: "error", environment: "201", backend: "PostgreSQL" };
          }
          render();
        }

        function healthChip() {
          const cls = state.health.status === "ok" ? "" : state.health.status === "loading" ? "loading" : "error";
          const text = state.health.status === "ok" ? `${state.health.environment} · PostgreSQL 已连接` : state.health.status === "loading" ? "正在检查 201 PostgreSQL" : "201 PostgreSQL · 未连接";
          return `<span class="health-chip ${cls}"><i class="health-dot"></i>${esc(text)}</span>`;
        }

        function topbar() {
          return `<header class="topbar">
            <a class="brand-button" href="/" aria-label="回到 Purslyx 首页"><span class="brand-mark">P</span><span>Purslyx</span></a>
            <span class="eyebrow">PURSLYX · 完整工作台</span>
            <div class="topbar-spacer"></div>
            ${healthChip()}
            <div class="top-actions">
              <a class="button outline" href="/docs" target="_blank" rel="noreferrer">查看 API</a>
              ${state.screen === "formal" ? (state.session.account ? `<button class="button soft" data-action="logout">退出工作台</button>` : `<a class="button primary" href="/">返回产品首页</a>`) : `<a class="button primary" href="/app/login">进入完整工作台</a>`}
            </div>
          </header>`;
        }

        function appFrame(content) {
          return `${topbar()}${content}${state.toast ? `<div class="toast">${esc(state.toast)}</div>` : ""}`;
        }

        function demoPage() {
          const demo = state.demo;
          return `<main class="public-main">
            <section class="hero">
              <div class="hero-copy">
                <div class="eyebrow">201 POSTGRESQL · DEMO SLICE</div>
                <h1>从真实经历，<span>走到下一步。</span></h1>
                <p>求职、招聘与授权管理端已经接入同一套真实数据。你可以从完整工作台浏览全部能力，也可以在这里快速跑一条可复核的 SDD 纵向链路。</p>
                <div class="hero-actions">
                  <button class="button primary" data-action="demo-scroll">开始最短闭环 ↓</button>
                  <a class="button" href="/health" target="_blank" rel="noreferrer">打开健康检查</a>
                </div>
                <div class="proof-row">
                  <div><strong>固定规则</strong>分数由代码计算</div>
                  <div><strong>可复核</strong>报告保存输入版本</div>
                  <div><strong>可落地</strong>直连 201 PostgreSQL</div>
                </div>
              </div>
              <div class="hero-visual" aria-hidden="true">
                <div class="visual-window">
                  <div class="window-top"><strong>匹配报告</strong><span class="tag success">有依据</span></div>
                  <div class="window-lines"><i class="window-line blue"></i><i class="window-line"></i><i class="window-line short"></i><i class="window-line"></i><i class="window-line blue short"></i></div>
                  <div class="visual-score"><div><strong>82</strong><small>综合分</small></div></div>
                </div>
              </div>
            </section>

            <section id="demo-flow" class="demo-flow">
              <div class="section-heading"><div><div class="eyebrow">SHORTEST PATH</div><h2>把需求现场跑出来</h2></div><p>页面只调用隔离的 <code>/api/v1/demo</code> 短入口；正式账号、CSRF、幂等和用量链路可从“正式工作台”进入。</p></div>
              <div class="step-strip">
                <div class="step"><b class="step-no">01</b><div><strong>输入资料</strong><span>简历和 JD 先生成可检查草稿</span></div></div>
                <div class="step"><b class="step-no">02</b><div><strong>确认版本</strong><span>草稿确认后冻结为 version 1</span></div></div>
                <div class="step"><b class="step-no">03</b><div><strong>读取报告</strong><span>能力分、覆盖率、依据和条件状态</span></div></div>
              </div>
              <form id="demo-form" class="panel">
                <div class="panel-head"><div><h3>最短演示输入</h3><p>使用页面内的虚构资料即可；提交不会要求登录，也不会把密码写入前端。</p></div><span class="tag opportunity">文字输入</span></div>
                <div class="input-grid">
                  <div class="field-group"><label for="demo-resume-title">简历名称</label><input id="demo-resume-title" class="field" name="resume_title" value="${esc(demo.resumeTitle)}" /><label for="demo-resume-text">简历正文</label><textarea id="demo-resume-text" class="textarea" name="resume_text">${esc(demo.resumeText)}</textarea></div>
                  <div class="field-group"><label for="demo-job-title">岗位名称</label><input id="demo-job-title" class="field" name="job_title" value="${esc(demo.jobTitle)}" /><label for="demo-job-text">岗位 JD</label><textarea id="demo-job-text" class="textarea" name="job_text">${esc(demo.jobText)}</textarea></div>
                </div>
                <div class="form-foot"><span class="micro">当前口径：本地开发环境.md → 201 PostgreSQL；SQLite 已禁止。</span><button class="button primary" type="submit" ${demo.running ? "disabled" : ""}>${demo.running ? "正在生成报告…" : "确认版本并生成报告"}</button></div>
                ${demo.error ? `<div class="callout attention">${esc(demo.error)}</div>` : ""}
                ${demo.running ? `<div class="progress-line"><i class="spinner"></i><span>${esc(demo.phase)}</span></div>` : ""}
                ${demo.logs.map((item) => `<div class="log-line">${esc(item)}</div>`).join("")}
              </form>
              <section class="demo-result panel">
                <div class="panel-head"><div><h3>可复核输出</h3><p>报告会从 PostgreSQL 重新落库读取所需的事实；演示时可展开原始 JSON 对照接口返回。</p></div>${demo.result ? `<span class="tag success">已落库</span>` : `<span class="tag neutral">等待执行</span>`}</div>
                ${demo.result ? demoReport(demo.result) : `<div class="result-empty"><div class="empty-stage"><strong>资料草稿</strong><p>系统先提取结构化内容，避免直接把原文当成结论。</p></div><div class="empty-stage"><strong>冻结版本</strong><p>确认动作产生带 version_no 的不可变输入。</p></div><div class="empty-stage"><strong>匹配报告</strong><p>每条要求回到简历证据，未知不会被写成不具备。</p></div>`}
              </section>
            </section>
            <p class="footer-note">Purslyx · 完整工作台覆盖首版业务页面；本页短流程用于快速复跑 SDD 证据链。真实模型、生产队列、邮件和外部平台投递仍是当前演示边界。</p>
          </main>`;
        }

        function demoReport(result) {
          const report = result.analysis?.report || {};
          const score = Number(report.ability_score);
          const scoreValue = Number.isFinite(score) ? score : 0;
          const advice = report.overall_advice || {};
          const dimensions = Array.isArray(report.dimensions) ? report.dimensions : [];
          const conditions = Array.isArray(report.conditions) ? report.conditions : [];
          return `<div class="report-top">
            <div class="score-block" style="--score:${Math.max(0, Math.min(scoreValue, 100))}"><div class="score-copy"><strong>${Number.isFinite(score) ? esc(number(score)) : "—"}</strong><span>能力综合分</span></div></div>
            <div class="report-summary"><span class="tag ${statusClass(advice.status)}">${esc(labelStatus(advice.status))}</span><h3>${esc(advice.text || "报告已生成")}</h3><p>${esc((advice.next_steps || []).join(" ") || "请结合证据和条件对照做人工判断。")}</p><div class="report-metrics"><div class="metric-pill"><strong>${esc(percent(report.evidence_coverage))}</strong><span>证据覆盖率</span></div><div class="metric-pill"><strong>${esc(String(dimensions.filter((item) => item.score !== null && item.score !== undefined).length))}</strong><span>可评分维度</span></div><div class="metric-pill"><strong>${esc(String(result.analysis?.scoring_rule_version || report.scoring_rule_version || "ability-v0.1"))}</strong><span>评分规则</span></div></div></div>
          </div>
          <div class="report-grid">
            <div class="report-card"><h3>能力维度与逐条依据</h3>${dimensions.map((dimension) => demoDimension(dimension)).join("") || `<div class="empty">暂无维度数据</div>`}</div>
            <div class="report-card"><h3>岗位条件对照</h3><div style="margin-top:8px">${conditions.map((item) => `<div class="condition-row"><strong>${esc({ job_title: "岗位方向", location: "地点", work_mode: "办公方式", salary: "薪资" }[item.condition] || item.condition)}</strong><span class="tag ${statusClass(item.status)}">${esc(labelStatus(item.status))}</span><p>${esc(item.explanation)}</p></div>`).join("") || `<div class="empty">没有条件数据</div>`}</div></div>
            <div class="report-card full"><h3>输入版本</h3><div class="version-strip"><span class="version-chip">简历 · ${esc(result.resume?.version?.id || "—")} · v${esc(result.resume?.version?.version_no || "—")}</span><span class="version-chip">岗位 · ${esc(result.job?.version?.id || "—")} · v${esc(result.job?.version?.version_no || "—")}</span><span class="version-chip">分析 · ${esc(result.analysis?.id || "—")}</span></div><details class="raw-report"><summary>展开报告 JSON（用于讲接口与落库）</summary><pre>${esc(json(result))}</pre></details></div>
          </div>
          ${reportFollowupSections(report)}`;
        }

        function demoDimension(dimension) {
          const score = Number(dimension.score);
          const width = Number.isFinite(score) ? Math.max(0, Math.min(score, 100)) : 0;
          return `<div class="dimension"><div class="dimension-head"><strong>${esc(dimension.label)}</strong><span>${Number.isFinite(score) ? `${esc(number(score))} 分` : "不适用"}</span></div>${Number.isFinite(score) ? `<div class="bar"><i style="width:${width}%"></i></div>` : ""}${(dimension.requirements || []).map((item) => `<div class="requirement"><div class="requirement-top"><span class="tag ${statusClass(item.status)}">${esc(labelStatus(item.status))}</span><p>${esc(item.job_quote)}</p></div>${item.evidence?.length ? `<div class="evidence">依据：${esc(item.evidence.map((evidence) => evidence.quote).join("；"))}</div>` : `<div class="evidence missing">暂无对应原文依据：保留为待确认，不直接判定为不具备。</div>`}</div>`).join("")}</div>`;
        }

        function reportFollowupSections(report) {
          const verificationItems = Array.isArray(report?.verification_items) ? report.verification_items : [];
          const interviewQuestions = Array.isArray(report?.interview_questions) ? report.interview_questions : [];
          const verification = verificationItems.length
            ? verificationItems.map((item) => `<article class="followup-item"><div class="item-title"><strong>${esc(item.label || item.job_quote || item.condition || "待核实事项")}</strong><span class="tag ${statusClass(item.status)}">${esc(labelStatus(item.status))}</span></div><p>${esc(item.reason || "需要进一步确认")}</p><small>建议核实：${esc(item.question || "请补充具体事实和结果")}</small></article>`).join("")
            : `<div class="empty"><div><strong>暂无待核实事项</strong><p>当前报告没有发现需要单独列出的证据或条件缺口。</p></div></div>`;
          const questions = interviewQuestions.length
            ? interviewQuestions.map((item, index) => `<article class="followup-item"><div class="item-title"><strong>问题 ${esc(String(index + 1))}</strong><span class="tag ${item.priority === "high" ? "attention" : "neutral"}">${esc(item.priority === "high" ? "重点核实" : "常规核实")}</span></div><p>${esc(item.question_text || "")}</p><small>依据：${esc(item.basis?.requirement_id || item.basis?.dimension_key || "岗位要求")}</small></article>`).join("")
            : `<div class="empty"><div><strong>暂无针对性问题</strong><p>请先补充可评估的岗位要求。</p></div></div>`;
          return `<div class="report-card full report-followup-card"><h3>待核实事项与针对性面试问题</h3><div class="followup-columns"><div><div class="eyebrow">VERIFICATION ITEMS</div><div class="followup-list">${verification}</div></div><div><div class="eyebrow">INTERVIEW QUESTIONS</div><div class="followup-list">${questions}</div></div></div></div>`;
        }

        function authPage() {
          const auth = state.auth;
          const credentialMode = ["login", "register"].includes(auth.mode);
          const title = { login: "欢迎回来", register: "创建正式账号", verify: "完成邮箱验证", forgot: "找回密码", reset: "设置新密码", recovery: "申请账号恢复", recover: "恢复账号" }[auth.mode] || "欢迎回来";
          const description = {
            login: "登录后继续本账号的资料、岗位和历史任务。",
            register: "注册时选择固定身份；本地演示会自动完成验证。",
            verify: "输入邮件中的一次性验证码，验证完成后再登录。",
            forgot: "提交后如果账号存在，会收到重置说明。",
            reset: "重置成功后，之前的 Web 会话会全部失效。",
            recovery: "提交后如果账号处于暂停状态，会收到恢复说明。",
            recover: "使用恢复令牌解除账号暂停状态。",
          }[auth.mode] || "登录后继续本账号的资料、岗位和历史任务。";
          let fields = "";
          if (credentialMode) {
            fields = `${auth.mode === "register" ? `<fieldset class="field-group identity-fieldset"><legend class="field-label">注册身份</legend><div class="identity-grid"><label class="identity-card ${auth.role === "seeker" ? "active" : ""}"><input type="radio" name="registration_role" value="seeker" ${auth.role === "seeker" ? "checked" : ""} /><span><strong>我要找工作</strong><small>简历、匹配池、面试练习</small></span></label><label class="identity-card ${auth.role === "recruiter" ? "active" : ""}"><input type="radio" name="registration_role" value="recruiter" ${auth.role === "recruiter" ? "checked" : ""} /><span><strong>我要招人</strong><small>岗位与候选人单人分析</small></span></label></div></fieldset>` : ""}<div class="field-group"><label for="auth-email">邮箱</label><input id="auth-email" class="field" type="email" name="email" autocomplete="email" value="${esc(auth.email)}" placeholder="name@example.com" required /></div><div class="field-group"><label for="auth-password">密码</label><input id="auth-password" class="field" type="password" name="password" autocomplete="current-password" value="${esc(auth.password)}" placeholder="至少 12 个字符" required /></div><button class="button primary full" type="submit" ${auth.running ? "disabled" : ""}>${auth.running ? "正在处理…" : auth.mode === "register" ? "注册并进入工作台" : "登录"}</button>`;
          } else if (auth.mode === "verify") {
            fields = `<div class="field-group"><label for="auth-email">邮箱</label><input id="auth-email" class="field" type="email" name="email" value="${esc(auth.email)}" required /></div><div class="field-group"><label for="auth-token">验证令牌</label><input id="auth-token" class="field" type="text" name="token" value="${esc(auth.token)}" autocomplete="one-time-code" required /></div><button class="button primary full" type="submit" ${auth.running ? "disabled" : ""}>${auth.running ? "正在验证…" : "验证邮箱并登录"}</button>`;
          } else if (auth.mode === "forgot" || auth.mode === "recovery") {
            fields = `<div class="field-group"><label for="auth-email">邮箱</label><input id="auth-email" class="field" type="email" name="email" value="${esc(auth.email)}" required /></div><button class="button primary full" type="submit" ${auth.running ? "disabled" : ""}>${auth.running ? "正在提交…" : auth.mode === "forgot" ? "发送重置说明" : "发送恢复说明"}</button>`;
          } else {
            fields = `<div class="field-group"><label for="auth-token">${auth.mode === "reset" ? "重置令牌" : "恢复令牌"}</label><input id="auth-token" class="field" type="text" name="token" value="${esc(auth.token)}" required /></div>${auth.mode === "reset" ? `<div class="field-group"><label for="auth-new-password">新密码</label><input id="auth-new-password" class="field" type="password" name="new_password" autocomplete="new-password" placeholder="至少 12 个字符" required /></div>` : ""}<button class="button primary full" type="submit" ${auth.running ? "disabled" : ""}>${auth.running ? "正在处理…" : auth.mode === "reset" ? "保存新密码" : "恢复账号"}</button>`;
          }
          return `<main class="public-main"><section class="auth-layout">
            <div class="auth-story"><div class="eyebrow">FORMAL WORKBENCH</div><h1>让每一次判断，<span>都回到依据。</span></h1><p>求职、招聘和授权管理页面都走完整的账号、CSRF、幂等与用量链路。登录后可以从任意模块独立进入，并恢复保存在数据库中的业务状态。</p><div class="auth-points"><div class="auth-point"><div><strong>资料与版本分开</strong><small>草稿可检查，确认后才进入冻结版本。</small></div></div><div class="auth-point"><div><strong>岗位期望单独成组</strong><small>未知、不限和面议保持不同语义。</small></div></div><div class="auth-point"><div><strong>操作可重试</strong><small>写请求带 CSRF 和 Idempotency-Key。</small></div></div></div></div>
            <form id="auth-form" class="auth-card"><h2>${title}</h2><p>${description}</p>${credentialMode ? `<div class="auth-tabs"><button class="auth-tab ${auth.mode === "login" ? "active" : ""}" type="button" data-action="auth-mode" data-mode="login">登录</button><button class="auth-tab ${auth.mode === "register" ? "active" : ""}" type="button" data-action="auth-mode" data-mode="register">注册</button></div>` : ""}<div class="auth-fields">${fields}</div>${auth.notice ? `<div class="callout success">${esc(auth.notice)}</div>` : ""}${auth.error ? `<div class="callout attention">${esc(auth.error)}</div>` : ""}<div class="auth-links">${auth.mode === "login" ? `<button class="button link-button small" type="button" data-action="auth-mode" data-mode="forgot">忘记密码</button><button class="button link-button small" type="button" data-action="auth-mode" data-mode="recovery">账号恢复</button>` : ""}${auth.mode === "register" ? `<button class="button link-button small" type="button" data-action="auth-mode" data-mode="verify">已有验证令牌？去验证</button>` : ""}${auth.mode === "verify" ? `<button class="button link-button small" type="button" data-action="resend-verification">重新发送验证</button>` : ""}${["forgot", "reset", "recovery", "recover", "verify"].includes(auth.mode) ? `<button class="button link-button small" type="button" data-action="auth-mode" data-mode="login">返回登录</button>` : ""}</div><div class="auth-foot">正式工作台使用当前进程的 201 PostgreSQL。这里不会显示或保存本地开发环境文件中的密码。</div></form>
          </section></main>`;
        }

        function workspaceShell() {
          const account = state.session.account || {};
          const seeker = account.registration_role === "seeker";
          const nav = seeker
            ? [["dashboard", "工作台", "总览"], ["resume", "简历与期望", "资料"], ["pool", "匹配池", "岗位"], ["variants", "岗位版简历", "成品"], ["interview", "面试练习", "复盘"], ["usage", "用量", "次数"], ["stats", "统计", "反馈"]]
            : [["dashboard", "工作台", "总览"], ["resume", "候选人资料", "资料"], ["usage", "用量", "次数"], ["stats", "统计", "反馈"]];
          const permissions = new Set(account.admin_permissions || []);
          const adminNav = [];
          if (permissions.has("admin.stats.read")) adminNav.push(["admin-metrics", "站点概况", "管理"]);
          if (permissions.has("admin.users.read")) adminNav.push(["admin-users", "用户管理", "管理"]);
          if (permissions.has("admin.roles.manage")) adminNav.push(["admin-roles", "角色权限", "管理"]);
          if (permissions.has("admin.usage.grant")) adminNav.push(["admin-usage", "次数管理", "管理"]);
          if (permissions.has("admin.logs.operations.read") || permissions.has("admin.logs.security.read") || permissions.has("admin.logs.tasks.read")) adminNav.push(["admin-logs", "日志管理", "管理"]);
          const allNav = adminNav.length ? [...nav, ["admin-separator", "管理端", ""], ...adminNav] : nav;
          return `<div class="workspace-layout" data-workspace-loaded="${state.workspace.loaded ? "true" : "false"}"><aside class="sidebar"><div class="side-brand"><a class="brand-button" href="${routeFor("dashboard")}"><span class="brand-mark">P</span><span>Purslyx</span></a><div><small>${seeker ? "求职工作台" : "招聘工作台"}</small></div></div><nav class="side-nav">${allNav.map((item) => item[0] === "admin-separator" ? `<div class="eyebrow" style="padding:12px 12px 3px;color:var(--muted)">${item[1]}</div>` : `<a class="nav-button ${state.workspace.page === item[0] ? "active" : ""}" href="${routeFor(item[0])}" data-page="${item[0]}"><span>${item[1]}</span><small>${item[2]}</small></a>`).join("")}</nav><div class="side-note"><strong>今天只做下一步</strong><p>页面是业务入口，结论仍回到已确认资料、冻结版本和可解释规则。</p></div></aside><main class="workspace-content">${workspaceContent()}</main></div>`;
        }

        function workspaceContent() {
          if (state.workspace.loading && !state.workspace.loaded) return `<div class="empty" style="min-height:260px"><div><i class="spinner" style="display:inline-block"></i><p style="margin-top:12px">正在从 201 PostgreSQL 读取工作台…</p></div></div>`;
          const page = state.workspace.page;
          const requiredPermission = {
            "admin-metrics": "admin.stats.read",
            "admin-users": "admin.users.read",
            "admin-roles": "admin.roles.manage",
            "admin-usage": "admin.usage.grant",
          }[page];
          const hasLogPermission = (state.session.account?.admin_permissions || []).some((item) => item.startsWith("admin.logs."));
          if ((requiredPermission && !(state.session.account?.admin_permissions || []).includes(requiredPermission)) || (page === "admin-logs" && !hasLogPermission)) {
            const pageTitle = { "admin-metrics": "站点概况", "admin-users": "用户管理", "admin-roles": "角色权限", "admin-usage": "次数管理", "admin-logs": "日志管理" }[page] || "管理端";
            return `${heading("ADMIN ACCESS", pageTitle, "当前账号没有此页面所需的后台权限。页面入口不会返回管理数据，所有接口仍会由服务端逐次校验。", `<a class="button outline small" href="${routeFor("dashboard")}">回到工作台</a>`)}<div class="empty"><div><strong>没有访问权限</strong><p>如需演示该页面，请使用部署者显式配置并授权的管理员账号。</p></div></div>`;
          }
          const content = page === "dashboard" ? dashboardPage() : page === "resume" ? resumePage() : page === "pool" ? enhancedPoolPage() : page === "report" ? formalReportPage() : page === "rewrite" ? rewritePage() : page === "variants" ? variantsPage() : page === "interview" ? interviewPage() : page === "tasks" ? tasksPage() : page === "usage" ? usagePage() : page === "stats" ? statsPage() : page === "admin-metrics" ? adminMetricsPage() : page === "admin-users" ? enhancedAdminUsersPage() : page === "admin-roles" ? adminRolesPage() : page === "admin-usage" ? adminUsagePage() : page === "admin-logs" ? adminLogsPageEnhanced() : dashboardPage();
          return `${state.workspace.error ? `<div class="callout attention" style="margin-bottom:18px">${esc(state.workspace.error)}</div>` : ""}${content}`;
        }

        function heading(eyebrow, title, description, actions = "") {
          return `<div class="page-heading"><div><div class="eyebrow">${esc(eyebrow)}</div><h1>${esc(title)}</h1><p>${esc(description)}</p></div>${actions ? `<div class="page-actions">${actions}</div>` : ""}</div>`;
        }

        function localDateEnd(value) {
          if (!value) return "";
          const parsed = new Date(`${value}T00:00:00+08:00`);
          if (Number.isNaN(parsed.getTime())) return "";
          parsed.setUTCDate(parsed.getUTCDate() + 1);
          return parsed.toISOString();
        }

        function adminLogQuery(type, source = state.workspace.adminLogFilters) {
          const params = new URLSearchParams();
          const filters = source || {};
          if (filters.created_from) params.set("created_from", `${filters.created_from}T00:00:00+08:00`);
          if (filters.created_to) params.set("created_to", localDateEnd(filters.created_to));
          if (filters.account_id) params.set("account_id", filters.account_id.trim());
          if (filters.result) params.set("result", filters.result);
          if (filters.request_id) params.set("request_id", filters.request_id.trim());
          if (type === "operations" && filters.action) params.set("action", filters.action.trim());
          if (type === "security" && filters.event_type) params.set("event_type", filters.event_type.trim());
          if (type === "tasks") {
            if (filters.task_type) params.set("task_type", filters.task_type.trim());
            if (filters.task_status) params.set("task_status", filters.task_status);
            if (filters.retry_count_min !== "" && filters.retry_count_min !== undefined) params.set("retry_count_min", filters.retry_count_min);
          }
          return params;
        }

        function adminMetricQuery(source = state.workspace.adminMetricFilters) {
          const params = new URLSearchParams();
          const filters = source || {};
          if (filters.date_from) params.set("date_from", filters.date_from);
          if (filters.date_to) params.set("date_to", filters.date_to);
          if (filters.registration_role && filters.registration_role !== "all") params.set("registration_role", filters.registration_role);
          if (filters.feature && filters.feature !== "all") params.set("feature", filters.feature);
          return params;
        }

        function personalStatsPath() {
          const dateValue = state.workspace.statsDate;
          return dateValue ? `/api/v1/stats/me?date=${encodeURIComponent(dateValue)}` : "/api/v1/stats/me";
        }

        function missingFieldLabel(value) {
          return {
            job_title: "岗位名称",
            job_location: "地点",
            job_salary: "薪资",
            job_requirements: "任职要求",
            work_mode: "办公方式",
            job_description: "岗位正文",
            resume_experience: "简历经历",
          }[value] || value || "未命名字段";
        }

        function browserDraftCard(draft, resumes, preferences) {
          if (!draft) return "";
          const missing = draft.missing_field_codes || [];
          const hasRequirements = resumes.length && preferences.length;
          const selectedResume = draft.resume_version_id || resumes[0]?.latest_version?.id || "";
          const selectedPreference = draft.preference_version_id || preferences[0]?.version?.id || "";
          return `<section class="card draft-review" style="margin-bottom:18px"><div class="card-head"><div><div class="eyebrow">BROWSER DRAFT</div><h3>待确认的浏览器岗位</h3><p>来自 ${esc(draft.platform === "liepin" ? "猎聘" : "BOSS 直聘")} 的岗位草稿，先检查采集字段，再决定是否入池和分析。</p></div><span class="tag opportunity">待确认</span></div><div class="browser-summary"><div><strong>${esc(draft.job_title || "未识别岗位")}</strong><span>岗位名称</span></div><div><strong>${esc(draft.company_name || "未识别公司")}</strong><span>公司</span></div><div><strong>${esc(draft.location_text || "未识别地点")}</strong><span>地点 · ${esc(draft.work_mode || "办公方式待确认")}</span></div><div><strong>${esc(draft.salary_text || "未识别薪资")}</strong><span>薪资 · ${esc(date(draft.captured_at))} 采集</span></div></div>${missing.length ? `<div class="callout attention">缺少字段：${esc(missing.map(missingFieldLabel).join("、"))}。可以继续入池，但分析结论会保留待确认。</div>` : `<div class="callout success">关键字段已识别。仍请人工核对 JD 正文和原岗位链接。</div>`}<details class="raw-report"><summary>查看采集原文与字段</summary><pre>${esc(json({ job_fields: draft.job_fields || {}, job_description_text: draft.job_description_text || "", source_url: draft.source_url || "" }))}</pre></details><form id="browser-draft-form" class="form-card" style="margin-top:14px"><input type="hidden" name="browser_draft_id" value="${esc(draft.id)}" /><div class="form-row"><div class="field-group"><label>本次使用的简历版本</label><select class="select" name="resume_version_id" ${resumes.length ? "" : "disabled"}>${resumes.map((item) => `<option value="${esc(item.latest_version.id)}" ${item.latest_version.id === selectedResume ? "selected" : ""}>${esc(item.title)} · v${esc(item.latest_version.version_no)}</option>`).join("") || `<option>请先确认一份简历</option>`}</select></div><div class="field-group"><label>本次使用的岗位期望</label><select class="select" name="preference_version_id" ${preferences.length ? "" : "disabled"}>${preferences.map((item) => `<option value="${esc(item.version.id)}" ${item.version.id === selectedPreference ? "selected" : ""}>${esc(item.display_name)} · v${esc(item.version.version_no)}</option>`).join("") || `<option>请先保存岗位期望</option>`}</select></div></div><label class="micro" style="display:flex;gap:8px;align-items:flex-start;margin-top:14px"><input type="checkbox" name="start_now" ${hasRequirements ? "checked" : ""} style="margin-top:3px" /> 确认后立即开始分析（消耗 1 次分析；不勾选则只入池）</label><div class="form-foot"><span class="micro">草稿本身不计次；确认入池后才生成正式岗位版本。</span><button class="button primary small" type="submit">确认并入池</button></div></form></section>`;
        }

        function documentDraftReview() {
          const draft = state.workspace.pendingDocumentDraft;
          if (!draft || !draft.draft_content || draft.latest_draft?.status === "confirmed") return "";
          const missing = draft.latest_draft?.missing_field_codes || [];
          return `<section class="card draft-review" style="margin-bottom:18px"><div class="card-head"><div><div class="eyebrow">DRAFT CHECK</div><h3>检查资料解析草稿</h3><p>${esc(draft.title)} · 当前 draft revision ${esc(String(draft.revision || draft.latest_draft?.revision || 1))}。确认后会生成不可变 version。</p></div><span class="tag opportunity">草稿未确认</span></div>${missing.length ? `<div class="callout opportunity">待补充字段：${esc(missing.map(missingFieldLabel).join("、"))}。可以直接在 JSON 中修正，也可以保留未知。</div>` : `<div class="callout success">解析结构完整，请核对字段和段落后确认。</div>`}<form id="document-draft-review" class="form-card"><input type="hidden" name="document_id" value="${esc(draft.id)}" /><input type="hidden" name="draft_id" value="${esc(draft.latest_draft?.id || "")}" /><input type="hidden" name="base_revision" value="${esc(String(draft.revision || draft.latest_draft?.revision || 1))}" /><div class="field-group"><label for="draft-review-title">版本名称</label><input id="draft-review-title" class="field" name="title" value="${esc(draft.title)}" required /></div><div class="field-group" style="margin-top:13px"><label for="draft-review-json">结构化内容 JSON</label><textarea id="draft-review-json" class="textarea" name="draft_json" required>${esc(json(draft.draft_content))}</textarea><span class="micro">只允许 document-content-v1 结构；简历使用 sections，岗位使用 job_fields。提交前会再次由服务端校验。</span></div><div class="form-foot"><button class="button outline small" type="button" data-action="discard-document-draft" data-id="${esc(draft.id)}">放弃这份草稿</button><button class="button primary small" type="submit">确认并生成 version ${esc(String((draft.latest_version?.version_no || 0) + 1))}</button></div></form></section>`;
        }

        function dashboardPage() {
          const data = state.workspace.data;
          const stats = data.stats || {};
          const account = state.session.account || {};
          const tasks = data.tasks || [];
          const adminReady = (account.admin_permissions || []).length > 0;
          return `${heading(account.registration_role === "seeker" ? "SEEKER WORKSPACE" : "RECRUITER WORKSPACE", "工作台", "这里保留最近输入、当前报告和下一步动作；刷新页面仍从数据库恢复。", `<button class="button outline small" data-action="refresh">刷新数据</button>`)}<div class="metric-grid"><div class="metric-card"><div class="eyebrow">资料</div><strong>${esc(String(data.documents.length))}</strong><span>已导入资料</span></div><div class="metric-card"><div class="eyebrow">分析</div><strong>${esc(String(stats.completed_analyses ?? data.analyses.length))}</strong><span>已完成报告</span></div><div class="metric-card"><div class="eyebrow">面试</div><strong>${esc(String(stats.interviews ?? data.interviews.length))}</strong><span>练习会话</span></div><div class="metric-card"><div class="eyebrow">去投递</div><strong>${esc(String(stats.apply_clicks ?? 0))}</strong><span>点击记录，不等于已投递</span></div></div><div class="content-grid"><section class="card"><div class="card-head"><div><h3>继续下一步</h3><p>从当前账号的真实状态进入，不另造一套演示数据。</p></div></div><div class="card-body"><div class="card-list">${account.registration_role === "seeker" ? `<div class="list-row"><div><strong>补齐简历与岗位期望</strong><small>先确认输入，后续分析才有稳定版本。</small></div><button class="button soft small" data-page="resume">去准备</button></div><div class="list-row"><div><strong>保存岗位并生成报告</strong><small>选择一条期望，确认消耗 1 次分析机会。</small></div><button class="button soft small" data-page="pool">去匹配</button></div><div class="list-row"><div><strong>恢复面试练习</strong><small>已开始的会话可以从当前题目继续。</small></div><button class="button soft small" data-page="interview">去面试</button></div>` : `<div class="list-row"><div><strong>导入候选人资料</strong><small>岗位和候选人简历确认后，再发起单人分析。</small></div><button class="button soft small" data-page="resume">去准备</button></div>`}</div></div></section><section class="card"><div class="card-head"><div><h3>最近分析</h3><p>报告只读本账号拥有的输入版本。</p></div></div><div class="card-body">${data.analyses.length ? `<div class="card-list">${data.analyses.slice(0, 4).map((item) => `<div class="list-row"><div><strong>${esc(item.job_category || "岗位分析")}</strong><small>${esc(labelStatus(item.status))} · ${esc(number(item.ability_score))} 分 · ${esc(date(item.completed_at))}</small></div><a class="button link-button" href="${routeFor("report", { analysis_id: item.id })}">查看</a></div>`).join("")}</div>` : `<div class="empty"><div><strong>还没有报告</strong><p>从“简历与期望”开始，准备一份可复核的输入。</p></div></div>`}</div></section></div><section class="card" style="margin-top:18px"><div class="card-head"><div><h3>最近任务</h3><p>排队、失败和可恢复输入都保留在原任务里；轮询不会产生新的任务。</p></div><div class="item-actions"><button class="button outline small" data-page="tasks">查看全部</button></div></div><div class="card-body">${tasks.length ? `<div class="task-list">${tasks.slice(0, 4).map(taskRow).join("")}</div>` : `<div class="empty"><div><strong>暂无任务</strong><p>发起解析、分析、改写、面试或 PDF 导出后，这里会显示状态。</p></div></div>`}</div></section>${adminReady ? `<div class="callout opportunity" style="margin-top:18px">当前账号拥有后台权限。管理端菜单已按权限显示，操作仍会在服务端逐次复核。</div>` : ""}`;
        }

        function taskRow(item) {
          const progress = Number(item.progress?.display_percent);
          const width = Number.isFinite(progress) ? Math.max(0, Math.min(progress, 100)) : 0;
          const retry = item.retryable ? `<button class="button outline small" data-action="retry-task" data-id="${esc(item.id)}">重试</button>` : "";
          return `<article class="task-row"><div style="min-width:0;flex:1"><strong>${esc(item.task_type || "任务")}</strong><small>${esc(labelStatus(item.status))} · ${esc(item.current_step || "等待调度")} · ${esc(date(item.updated_at))}</small>${Number.isFinite(progress) && item.status !== "succeeded" ? `<div class="progress-track"><i style="width:${width}%"></i></div>` : ""}${item.failure ? `<small style="color:#964016">${esc(item.failure.message || item.failure.code || "执行失败")}</small>` : ""}</div><div class="item-actions">${retry}<button class="button link-button small" data-action="open-task" data-id="${esc(item.id)}">查看</button></div></article>`;
        }

        function resumePage() {
          const seeker = state.session.account?.registration_role === "seeker";
          const documents = state.workspace.data.documents.filter((item) => seeker ? item.document_type === "resume" : ["resume", "job_description"].includes(item.document_type));
          const preferences = state.workspace.data.preferences;
          return `${heading(seeker ? "RESUME" : "CANDIDATE MATERIALS", seeker ? "简历与岗位期望" : "候选人资料", seeker ? "把简历和每条岗位期望分别确认；分析时只引用本次选中的版本。" : "岗位描述和候选人简历分别确认，再创建一份单人分析。", `<button class="button outline small" data-action="refresh">刷新</button>`)}${documentDraftReview()}<div class="two-col"><section class="card"><div class="card-head"><div><h3>${seeker ? "我的确认资料" : "导入资料"}</h3><p>${seeker ? "文字或文件都会先进入草稿审核；确认后才生成不可变版本。" : "招聘身份可分别导入岗位描述与候选人简历，确认前可检查解析结构。"}</p></div></div><div class="card-body"><form id="formal-document-form" class="form-card">${!seeker ? `<div class="field-group"><label for="formal-document-type">资料类型</label><select id="formal-document-type" class="select" name="document_type"><option value="resume">候选人简历</option><option value="job_description">岗位 JD</option></select></div>` : `<input type="hidden" name="document_type" value="resume" />`}<div class="field-group"><label for="formal-document-title">名称</label><input id="formal-document-title" class="field" name="title" value="${seeker ? "我的确认简历" : "候选人资料"}" required /></div><div class="field-group"><label for="formal-document-text">正文</label><textarea id="formal-document-text" class="textarea" name="text" placeholder="粘贴文字内容……" required>${seeker ? esc(DEFAULT_RESUME) : ""}</textarea></div><div class="form-foot"><span class="micro">提交后先生成草稿；检查结构化内容后再确认版本。</span><button class="button primary small" type="submit">导入并检查草稿</button></div></form><div style="margin-top:16px">${documents.length ? documents.map((item) => documentItem(item)).join("") : `<div class="empty"><div><strong>还没有资料</strong><p>从这里粘贴文字或上传文件，完成后会出现待检查草稿。</p></div></div>`}</div></div></section>${seeker ? `<section class="card"><div class="card-head"><div><h3>岗位期望</h3><p>未填写不等于不限；每组条件独立保存为版本。</p></div></div><div class="card-body"><form id="preference-form" class="form-card"><div class="field-group"><label for="preference-title">期望岗位</label><input id="preference-title" class="field" name="job_title" value="前端工程师" required /></div><div class="form-row"><div class="field-group"><label for="preference-location">期望地点</label><input id="preference-location" class="field" name="location" value="杭州" required /></div><div class="field-group"><label for="preference-mode">办公方式</label><select id="preference-mode" class="select" name="work_mode"><option value="onsite">现场办公</option><option value="hybrid">混合办公</option><option value="remote">远程</option></select></div></div><div class="form-row"><div class="field-group"><label for="preference-min">月薪下限（K）</label><input id="preference-min" class="field" name="min_salary" inputmode="decimal" value="18" required /></div><div class="field-group"><label for="preference-max">月薪上限（K）</label><input id="preference-max" class="field" name="max_salary" inputmode="decimal" value="28" required /></div></div><div class="form-foot"><span class="micro">示例按税前月薪 CNY 保存。</span><button class="button primary small" type="submit">保存这条期望</button></div></form><div style="margin-top:16px">${preferences.length ? preferences.map((item) => preferenceItem(item)).join("") : `<div class="empty"><div><strong>还没有岗位期望</strong><p>保存后，匹配池会要求明确选择一条。</p></div></div>`}</div></div></section>` : `${recruiterAnalysisSection(documents)}${recruiterPreferenceSection(documents)}${recruiterHistorySection()}`}</div>`;
        }

        function documentItem(item) {
          const version = item.latest_version;
          const draft = item.latest_draft;
          const draftPending = draft && draft.status === "unconfirmed";
          return `<article class="document-item"><div class="item-title"><strong>${esc(item.title)}</strong><span class="tag ${draftPending ? "opportunity" : statusClass(item.status)}">${draftPending ? "待确认草稿" : esc(labelStatus(item.status))}</span></div><div class="item-meta"><span>${esc(item.document_type === "job_description" ? "岗位 JD" : "简历")}</span><span>revision ${esc(item.revision)}</span><span>${version ? `version ${esc(version.version_no)}` : "待确认版本"}</span>${draftPending ? `<span>草稿 revision ${esc(String(draft.revision || 1))}</span>` : ""}</div>${version ? `<div class="micro" style="margin-top:9px">冻结版本：${esc(version.id)}</div>` : ""}<div class="item-actions" style="margin-top:11px">${draftPending ? `<button class="button primary small" data-action="open-document-draft" data-id="${esc(item.id)}">检查草稿</button>` : ""}${item.source_type && item.source_type !== "text" ? `<a class="button soft small" href="/api/v1/documents/${esc(item.id)}/file" target="_blank" rel="noreferrer">下载原文件</a>` : ""}<button class="button outline small" data-action="delete-document" data-id="${esc(item.id)}">删除前查看影响</button></div></article>`;
        }

        function preferenceItem(item) {
          const content = item.version?.content || {};
          const location = content.locations?.values?.join("、") || labelStatus(content.locations?.status);
          const minK = Number(content.salary?.min) / 1000;
          const maxK = Number(content.salary?.max) / 1000;
          const salary = content.salary?.status === "specified" && Number.isFinite(minK) && Number.isFinite(maxK) ? `${minK}–${maxK}K` : labelStatus(content.salary?.status);
          return `<article class="document-item"><div class="item-title"><strong>${esc(item.display_name)}</strong>${item.is_default ? `<span class="tag success">默认</span>` : `<span class="tag neutral">备用</span>`}</div><div class="item-meta"><span>${esc(content.job_title?.value || labelStatus(content.job_title?.status))}</span><span>${esc(location)}</span><span>${esc(salary)}</span><span>强度：${esc(labelStatus(content.job_title?.strength || "prefer"))}</span></div><div class="micro" style="margin-top:9px">期望版本 v${esc(item.version?.version_no || "—")} · ${esc(item.version?.id || "—")}</div><div class="item-actions" style="margin-top:11px"><button class="button soft small" data-action="edit-preference" data-id="${esc(item.id)}">编辑</button>${item.is_default ? "" : `<button class="button outline small" data-action="default-preference" data-id="${esc(item.id)}">设为默认</button>`}<button class="button link-button small" data-action="delete-preference" data-id="${esc(item.id)}">删除</button></div></article>`;
        }

        function preferenceControls(item = null) {
          const content = item?.version?.content || {};
          const option = (value, textValue, current) => `<option value="${value}" ${current === value ? "selected" : ""}>${textValue}</option>`;
          const statusOptions = (current, includeNegotiable = false) => `${option("specified", "已明确", current)}${option("unknown", "未知", current)}${option("unrestricted", "不限", current)}${includeNegotiable ? option("negotiable", "面议", current) : ""}`;
          const strength = content.job_title?.strength || "prefer";
          return `<div class="preference-controls callout" data-preference-controls><div class="field-label">条件语义</div><div class="form-row" style="margin-top:9px"><div class="field-group"><label>岗位状态</label><select class="select" name="job_title_status" data-preference-status="job_title">${statusOptions(content.job_title?.status || "specified")}</select></div><div class="field-group"><label>地点状态</label><select class="select" name="location_status" data-preference-status="location">${statusOptions(content.locations?.status || "specified")}</select></div></div><div class="form-row" style="margin-top:9px"><div class="field-group"><label>办公方式状态</label><select class="select" name="work_mode_status" data-preference-status="work_mode">${statusOptions(content.work_mode?.status || "specified")}</select></div><div class="field-group"><label>薪资状态</label><select class="select" name="salary_status" data-preference-status="salary">${statusOptions(content.salary?.status || "specified", true)}</select></div></div><div class="form-row" style="margin-top:9px"><div class="field-group"><label>条件强度</label><select class="select" name="condition_strength">${option("prefer", "倾向", strength)}${option("important", "重要", strength)}${option("required", "必须", strength)}</select></div><label class="micro" style="display:flex;gap:8px;align-items:center;margin-top:27px"><input type="checkbox" name="is_default" ${item?.is_default || !item ? "checked" : ""} /> 设为默认期望</label></div>${item ? `<input type="hidden" name="preference_id" value="${esc(item.id)}" /><input type="hidden" name="base_revision" value="${esc(String(item.revision || 1))}" />` : ""}<p class="micro" style="margin-top:10px">“未知”表示尚未提供，“不限”表示明确不限制，“面议”只适用于薪资；三者不会互相替代。</p></div>`;
        }

        function syncPreferenceStatus(form) {
          const fields = { job_title: ["job_title"], location: ["location"], work_mode: ["work_mode"], salary: ["min_salary", "max_salary"] };
          Object.entries(fields).forEach(([keyName, names]) => {
            const status = form.elements.namedItem(`${keyName}_status`)?.value || "specified";
            names.forEach((name) => {
              const field = form.elements.namedItem(name);
              if (field) { field.disabled = status !== "specified"; field.required = false; }
            });
          });
        }

        function decoratePreferenceForms() {
          document.querySelectorAll("#preference-form, #recruiter-preference-form").forEach((form) => {
            if (!form.querySelector("[data-preference-controls]")) {
              const subjectId = form.elements.namedItem("subject_document_id")?.value || "";
              const selectedPreference = state.workspace.selectedPreference;
              const selected = (selectedPreference && (!subjectId || selectedPreference.subject_document_id === subjectId))
                ? selectedPreference
                : state.workspace.data.preferences.find((item) => subjectId && item.subject_document_id === subjectId) || null;
              form.insertAdjacentHTML("afterbegin", preferenceControls(selected));
              if (selected) {
                // 编辑时恢复服务端版本；新建时保留页面提供的可直接提交示例值。
                const content = selected.version?.content || {};
                const setValue = (name, value) => { const field = form.elements.namedItem(name); if (field && value !== undefined && value !== null) field.value = value; };
                setValue("job_title", content.job_title?.value || "");
                setValue("location", content.locations?.values?.join("、") || "");
                setValue("work_mode", content.work_mode?.value || "onsite");
                if (content.salary?.min !== null && content.salary?.min !== undefined) setValue("min_salary", Number(content.salary.min) / 1000);
                if (content.salary?.max !== null && content.salary?.max !== undefined) setValue("max_salary", Number(content.salary.max) / 1000);
              }
              syncPreferenceStatus(form);
            }
          });
        }

        function syncRecruiterAnalysisPreferences(form) {
          const resumeSelect = form?.elements.namedItem("resume_version_id");
          const preferenceSelect = form?.elements.namedItem("preference_version_id");
          if (!resumeSelect || !preferenceSelect) return;
          const selectedOption = resumeSelect.selectedOptions?.[0];
          const subjectId = selectedOption?.dataset.subjectId || "";
          [...preferenceSelect.options].forEach((option) => {
            if (!option.value) { option.hidden = false; option.disabled = false; return; }
            const available = option.dataset.subjectId === subjectId;
            option.hidden = !available;
            option.disabled = !available;
          });
          if (preferenceSelect.selectedOptions?.[0]?.disabled) preferenceSelect.value = "";
        }

        function decorateRecruiterAnalysisForm() {
          const form = document.querySelector("#recruiter-analysis-form");
          if (form) syncRecruiterAnalysisPreferences(form);
        }

        function decorateDocumentForms() {
          document.querySelectorAll("#formal-document-form").forEach((form) => {
            if (form.querySelector("input[type=file]")) return;
            const group = document.createElement("div");
            group.className = "field-group upload-field";
            group.innerHTML = `<label for="formal-document-file">上传文件（PDF / DOC / DOCX）</label><input id="formal-document-file" class="field" type="file" name="file" accept=".pdf,.doc,.docx,application/pdf,application/msword,application/vnd.openxmlformats-officedocument.wordprocessingml.document" /><span class="micro">上传后服务端先解析为草稿；失败时保留此页面的文件和文字输入，便于改用粘贴。</span>`;
            const text = form.querySelector("textarea[name=text]");
            if (text) {
              text.required = false;
              const textGroup = text.closest(".field-group") || text;
              textGroup.after(group);
            }
            else form.append(group);
            const pending = state.workspace.pendingDocument;
            if (pending) {
              const title = form.elements.namedItem("title");
              const textInput = form.elements.namedItem("text");
              const type = form.elements.namedItem("document_type");
              if (title && pending.title) title.value = pending.title;
              if (textInput && pending.text) textInput.value = pending.text;
              if (type && pending.type && type.tagName === "SELECT") type.value = pending.type;
              if (pending.file && window.DataTransfer) {
                const transfer = new DataTransfer();
                transfer.items.add(pending.file);
                form.querySelector("input[type=file]").files = transfer.files;
              }
            }
          });
        }

        function recruiterAnalysisSection(documents) {
          const resumes = documents.filter((item) => item.document_type === "resume" && item.subject_type === "candidate_resume" && item.latest_version);
          const jobs = documents.filter((item) => item.document_type === "job_description" && item.latest_version);
          const preferences = state.workspace.data.preferences.filter((item) => item.version && item.subject_document_id);
          return `<section class="card" style="margin-top:18px"><div class="card-head"><div><h3>创建单人分析</h3><p>招聘身份不进入求职者匹配池；报告只绑定一位候选人的简历版本、岗位版本和可选的候选人期望。</p></div><span class="tag opportunity">招聘端</span></div><div class="card-body"><form id="recruiter-analysis-form" class="form-card"><div class="field-group"><label>候选人简历版本</label><select class="select" name="resume_version_id" ${resumes.length ? "" : "disabled"}>${resumes.map((item) => `<option value="${esc(item.latest_version.id)}" data-subject-id="${esc(item.id)}">${esc(item.title)} · v${esc(item.latest_version.version_no)}</option>`).join("") || `<option>请先导入候选人简历</option>`}</select></div><div class="field-group"><label>岗位版本</label><select class="select" name="job_version_id" ${jobs.length ? "" : "disabled"}>${jobs.map((item) => `<option value="${esc(item.latest_version.id)}">${esc(item.title)} · v${esc(item.latest_version.version_no)}</option>`).join("") || `<option>请先导入岗位 JD</option>`}</select></div><div class="field-group"><label>候选人岗位期望（可选）</label><select class="select" name="preference_version_id" data-recruiter-analysis-preference ${preferences.length ? "" : "disabled"}><option value="">不引用候选人期望</option>${preferences.map((item) => `<option value="${esc(item.version.id)}" data-subject-id="${esc(item.subject_document_id)}">${esc(item.display_name)} · v${esc(item.version.version_no)}</option>`).join("")}</select><span class="micro">切换候选人后，只保留指向该候选人的期望版本。</span></div><div class="form-foot"><span class="micro">开始分析确认消耗 1 次分析机会。</span><button class="button primary small" type="submit" ${resumes.length && jobs.length ? "" : "disabled"}>开始分析</button></div></form></div></section>`;
        }

        function recruiterHistorySection() {
          const rows = (state.workspace.data.analyses || []).filter((item) => item.context_type === "recruiter_single");
          return `<section class="card" style="margin-top:18px"><div class="card-head"><div><h3>单人分析历史</h3><p>每次分析都保留当时使用的候选人简历、岗位和期望版本。</p></div><span class="tag neutral">${esc(String(rows.length))} 条</span></div><div class="card-body">${rows.length ? `<div class="history-list">${rows.map((item) => `<article class="history-row"><div><strong>${esc(item.job_category || "候选人岗位分析")}</strong><small>${esc(labelStatus(item.status))} · ${esc(number(item.ability_score))} 分 · ${esc(date(item.completed_at || item.created_at))}<br />${esc(item.id)}</small></div><a class="button soft small" href="${routeFor("report", { analysis_id: item.id })}">打开报告</a></article>`).join("")}</div>` : `<div class="empty"><div><strong>还没有单人分析</strong><p>导入候选人简历和岗位 JD 后，从上方开始一次分析。</p></div></div>`}</div></section>`;
        }

        function poolAnalysisForm(selected, resumes, preferences) {
          if (!selected || !["awaiting_requirements", "failed"].includes(selected.analysis_status)) return "";
          const selectedResume = selected.resume_version_id || resumes[0]?.latest_version?.id || "";
          return `<form id="pool-analyze-form" class="form-card" style="margin-top:14px"><div class="field-group"><label>分析使用的简历版本</label><select class="select" name="resume_version_id" ${resumes.length ? "" : "disabled"}>${resumes.map((item) => `<option value="${esc(item.latest_version.id)}" ${item.latest_version.id === selectedResume ? "selected" : ""}>${esc(item.title)} · v${esc(item.latest_version.version_no)}</option>`).join("") || `<option>请先确认简历</option>`}</select></div><div class="field-group"><label>分析使用的岗位期望版本</label><select class="select" name="preference_version_id" ${preferences.length ? "" : "disabled"}>${preferences.map((item) => `<option value="${esc(item.version.id)}">${esc(item.display_name)} · v${esc(item.version.version_no)}</option>`).join("") || `<option>请先保存岗位期望</option>`}</select></div><label class="micro" style="display:flex;gap:8px;align-items:flex-start;margin-top:13px"><input type="checkbox" name="confirm_usage" checked style="margin-top:3px" /> 我确认消耗 1 次分析机会</label><div class="form-foot"><span class="micro">岗位 revision ${esc(String(selected.revision || 1))}；若页面数据已变化，服务端会要求刷新。</span><button class="button primary small" type="submit" ${resumes.length && preferences.length ? "" : "disabled"}>${selected.analysis_status === "failed" ? "重试分析" : "开始分析"}</button></div></form>`;
        }

        function enhancedPoolPage() {
          const data = state.workspace.data;
          const resumes = data.documents.filter((item) => item.document_type === "resume" && item.latest_version);
          const preferences = data.preferences.filter((item) => item.version);
          const selected = state.workspace.selectedPool;
          const selectedDetail = selected ? `<section class="card pool-detail"><div class="card-head"><div><div class="eyebrow">POOL ITEM</div><h3>${esc(selected.job_title || "岗位详情")}</h3><p>${esc(selected.company_name || "未标注公司")} · ${esc(labelStatus(selected.analysis_status))} · revision ${esc(String(selected.revision || 1))}</p></div><button class="button link-button small" data-action="close-pool">收起</button></div>${selected.blocking_reasons?.length ? `<div class="callout opportunity">当前还不能分析：${esc(selected.blocking_reasons.join("；"))}</div>` : ""}${selected.source_url ? `<div class="callout opportunity">原岗位链接已由服务端校验；“去投递”只记录跳转意向，不代表外部平台已投递。</div><div class="item-actions" style="margin-top:12px"><button class="button primary small" data-action="go-apply" data-id="${esc(selected.id)}" data-token="${esc(selected.apply_action?.click_token || "")}">去投递</button></div>` : `<div class="callout">这是手动岗位，暂时没有可跳转的原平台链接。</div>`}<div class="item-meta"><span>来源：${esc(selected.source_type === "browser_capture" ? "浏览器采集" : "手动输入")}</span><span>岗位版本：${esc(selected.job_document_version?.id || "—")}</span><span>简历版本：${esc(selected.resume_version_id || "尚未绑定")}</span></div><details class="raw-report"><summary>查看岗位结构化内容</summary><pre>${esc(json(selected.job_content || selected.job_fields || {}))}</pre></details>${poolAnalysisForm(selected, resumes, preferences)}${selected.latest_analysis ? `<div class="item-actions"><a class="button primary small" href="${routeFor("report", { analysis_id: selected.latest_analysis.id })}">打开完整报告</a></div>` : ""}${selected.analysis_history?.length ? `<div class="interview-history" style="margin-top:18px"><div class="card-head"><div><h3>分析历史</h3><p>同一岗位可以按新的简历或期望版本重新分析。</p></div></div><div class="history-list" style="margin-top:10px">${selected.analysis_history.map((item) => `<div class="history-row"><div><strong>${esc(labelStatus(item.status))} · ${esc(number(item.ability_score))} 分</strong><small>${esc(item.id)} · ${esc(date(item.completed_at))}</small></div>${item.status === "available" ? `<a class="button link-button small" href="${routeFor("report", { analysis_id: item.id })}">查看</a>` : ""}</div>`).join("")}</div></div>` : ""}<div class="item-actions" style="margin-top:18px"><button class="button link-button small" data-action="delete-pool" data-id="${esc(selected.id)}">删除岗位（先查看影响）</button></div></section>` : "";
          return `${heading("MATCH POOL", "匹配池", "岗位先进入待确认状态；浏览器草稿和手动 JD 统一落到岗位池，分析可以立即开始，也可以稍后显式触发。", `<button class="button outline small" data-action="refresh">刷新</button>`)}${browserDraftCard(state.workspace.browserDraft, resumes, preferences)}${state.workspace.browserDraftError ? `<div class="callout attention" style="margin-bottom:18px">${esc(state.workspace.browserDraftError)}</div>` : ""}${selectedDetail}<div class="content-grid"><section class="card"><div class="card-head"><div><h3>保存一份手动岗位</h3><p>用于没有浏览器采集或需要现场补录的岗位；确认版本后再选择是否分析。</p></div><span class="tag opportunity">可选分析</span></div><div class="card-body"><form id="pool-form" class="form-card"><div class="field-group"><label for="pool-title">岗位名称</label><input id="pool-title" class="field" name="job_title" value="前端开发工程师" required /></div><div class="field-group"><label for="pool-company">公司</label><input id="pool-company" class="field" name="company_name" value="示例科技" /></div><div class="field-group"><label for="pool-text">岗位 JD</label><textarea id="pool-text" class="textarea" name="job_text" required>${esc(DEFAULT_JOB)}</textarea></div><div class="field-group"><label for="pool-resume">本次使用的简历版本</label><select id="pool-resume" class="select" name="resume_version_id" ${resumes.length ? "" : "disabled"}>${resumes.map((item) => `<option value="${esc(item.latest_version.id)}">${esc(item.title)} · v${esc(item.latest_version.version_no)}</option>`).join("") || `<option>请先去简历页确认版本</option>`}</select></div><div class="field-group"><label for="pool-preference">本次使用的岗位期望版本</label><select id="pool-preference" class="select" name="preference_version_id" ${preferences.length ? "" : "disabled"}>${preferences.map((item) => `<option value="${esc(item.version.id)}">${esc(item.display_name)} · v${esc(item.version.version_no)}</option>`).join("") || `<option>请先去简历页保存期望</option>`}</select></div><label class="micro" style="display:flex;gap:8px;align-items:flex-start;margin-top:14px"><input type="checkbox" name="start_now" checked style="margin-top:3px" /> 保存后立即生成报告（确认消耗 1 次分析机会）</label><div class="form-foot"><span class="micro">只保存岗位不会扣次；开始分析前会再次确认余额。</span><button class="button primary small" type="submit">保存岗位</button></div></form></div></section><section class="card"><div class="card-head"><div><h3>我的岗位</h3><p>每个岗位有独立的分析状态、历史和去投递入口。</p></div><span class="tag neutral">${esc(String(data.pool.length))} 条</span></div><div class="card-body">${data.pool.length ? data.pool.map((item) => poolItem(item)).join("") : `<div class="empty"><div><strong>匹配池还是空的</strong><p>先在左侧保存岗位，或从浏览器草稿确认入池。</p></div></div>`}</div></section></div>`;
        }

        function poolItem(item) {
          const latest = item.latest_analysis;
          return `<article class="pool-item"><div class="item-title"><strong>${esc(item.job_title || "未命名岗位")}</strong><span class="tag ${statusClass(item.analysis_status)}">${esc(labelStatus(item.analysis_status))}</span></div><div class="item-meta"><span>${esc(item.company_name || "未标注公司")}</span><span>${esc(item.source_type === "browser_capture" ? "浏览器采集" : "手动输入")}</span><span>${latest ? `综合分 ${esc(number(latest.ability_score))}` : "尚无报告"}</span><span>${esc(date(item.updated_at))}</span></div><div class="item-actions"><button class="button soft small" data-action="open-pool" data-id="${esc(item.id)}">查看岗位</button>${latest ? `<a class="button outline small" href="${routeFor("report", { analysis_id: latest.id })}">完整报告</a>` : item.analysis_status === "awaiting_requirements" || item.analysis_status === "failed" ? `<button class="button outline small" data-action="open-pool" data-id="${esc(item.id)}">开始分析</button>` : ""}<button class="button link-button small" data-action="delete-pool" data-id="${esc(item.id)}">删除</button></div></article>`;
        }

        function formalReportPage() {
          const analysis = state.workspace.selectedAnalysis;
          if (!analysis) return `${heading("REPORT", "匹配报告", "从匹配池或工作台选择一份报告。", `<button class="button outline small" data-page="pool">回到匹配池</button>`)}<div class="empty"><div><strong>还没有选中的报告</strong><p>报告会在生成后保存，并可从历史入口重新打开。</p></div></div>`;
          const report = analysis.report || {};
          const score = Number(analysis.ability_score ?? report.ability_score);
          const advice = report.overall_advice || {};
          const pool = state.workspace.selectedPool || findPoolForAnalysis(analysis.id);
          const resumeId = analysis.input_versions?.find((item) => item && item.type === "resume")?.id;
          return `${heading("MATCH REPORT", "匹配报告", "结果绑定到本次分析的简历版本、岗位版本和规则版本；未知信息仍保持未知。", `<button class="button outline small" data-page="dashboard">回到工作台</button>`)}<section class="card"><div class="report-top"><div class="score-block" style="--score:${Math.max(0, Math.min(Number.isFinite(score) ? score : 0, 100))}"><div class="score-copy"><strong>${Number.isFinite(score) ? esc(number(score)) : "—"}</strong><span>能力综合分</span></div></div><div class="report-summary"><span class="tag ${statusClass(advice.status)}">${esc(labelStatus(advice.status))}</span><h3>${esc(advice.text || "报告已生成")}</h3><p>${esc((advice.next_steps || []).join(" ") || "请结合证据和条件对照做人工判断。")}</p><div class="report-metrics"><div class="metric-pill"><strong>${esc(percent(analysis.evidence_coverage ?? report.evidence_coverage))}</strong><span>证据覆盖率</span></div><div class="metric-pill"><strong>${esc(analysis.job_category || report.job_category || "general")}</strong><span>岗位类别</span></div><div class="metric-pill"><strong>${esc(analysis.scoring_rule_version || report.scoring_rule_version || "ability-v0.1")}</strong><span>评分规则</span></div></div><div class="item-actions">${pool && resumeId ? `<button class="button primary small" data-action="create-variant">制作岗位版简历</button><button class="button soft small" data-action="open-rewrite">补充事实与改写</button><button class="button soft small" data-action="start-interview">开始面试练习</button>` : ""}</div></div></div><div class="report-grid"><div class="report-card"><h3>能力维度与逐条依据</h3>${(report.dimensions || []).map((item) => demoDimension(item)).join("") || `<div class="empty">暂无维度数据</div>`}</div><div class="report-card"><h3>岗位条件对照</h3><div style="margin-top:8px">${(report.conditions || []).map((item) => `<div class="condition-row"><strong>${esc({ job_title: "岗位方向", location: "地点", work_mode: "办公方式", salary: "薪资" }[item.condition] || item.condition)}</strong><span class="tag ${statusClass(item.status)}">${esc(labelStatus(item.status))}</span><p>${esc(item.explanation)}</p></div>`).join("") || `<div class="empty">没有条件数据</div>`}</div></div><div class="report-card full"><h3>输入版本与可追溯信息</h3><div class="version-strip">${(analysis.input_versions || []).filter(Boolean).map((item) => `<span class="version-chip">${esc(item.type)} · ${esc(item.id)}</span>`).join("")}<span class="version-chip">analysis · ${esc(analysis.id)}</span></div><div class="item-actions" style="margin-top:13px"><button class="button link-button small" data-action="delete-analysis" data-id="${esc(analysis.id)}">删除这份报告（先查看影响）</button></div><details class="raw-report"><summary>展开完整报告 JSON</summary><pre>${esc(json(analysis))}</pre></details></div></div></section>`;
        }

        function findPoolForAnalysis(analysisId) {
          return state.workspace.data.pool.find((item) => item.latest_analysis?.id === analysisId) || null;
        }

        function variantsPage() {
          const variants = state.workspace.data.variants;
          const pools = state.workspace.data.pool.filter((item) => item.resume_version_id);
          return `${heading("RESUME VARIANTS", "岗位版简历", "从冻结简历版本生成岗位版成品；后续导出仍绑定同一个版本。", `<button class="button outline small" data-action="refresh">刷新</button>`)}<div class="content-grid"><section class="card"><div class="card-head"><div><h3>生成岗位版</h3><p>可以从刚打开的报告直接进入，也可以选择一个已分析岗位。</p></div></div><div class="card-body"><form id="variant-form" class="form-card"><div class="field-group"><label for="variant-pool">目标岗位</label><select id="variant-pool" class="select" name="pool_id" ${pools.length ? "" : "disabled"}>${pools.map((item) => `<option value="${esc(item.id)}">${esc(item.job_title)} · ${esc(item.id.slice(0, 8))}</option>`).join("") || `<option>请先在匹配池生成报告</option>`}</select></div><div class="field-group"><label for="variant-title">成品名称</label><input id="variant-title" class="field" name="title" value="${esc(state.workspace.selectedAnalysis ? "前端开发工程师 · 岗位版" : "岗位版简历")}" required /></div><div class="form-foot"><span class="micro">岗位版内容只来自已确认简历和真实改写结果。</span><button class="button primary small" type="submit" ${pools.length ? "" : "disabled"}>创建版本</button></div></form>${state.workspace.lastExport ? `<div class="callout success">PDF 已生成：${esc(state.workspace.lastExport.id)} · <a href="/api/v1/exports/${esc(state.workspace.lastExport.id)}/file" target="_blank" rel="noreferrer">下载岗位版简历</a></div>` : ""}</div></section><section class="card"><div class="card-head"><div><h3>已生成成品</h3><p>每个岗位版可以继续编辑和导出。</p></div></div><div class="card-body">${variants.length ? variants.map((item) => variantItem(item)).join("") : `<div class="empty"><div><strong>还没有岗位版简历</strong><p>从匹配报告点击“制作岗位版简历”，或在左侧选择岗位创建。</p></div></div>`}</div></section></div>`;
        }

        function variantItem(item) {
          const version = item.versions?.[0];
          const isLast = state.workspace.lastExport && version && state.workspace.lastExport.version_id === version.id;
          return `<article class="variant-item"><div class="item-title"><strong>${esc(item.title)}</strong><span class="tag ${statusClass(item.status)}">${esc(labelStatus(item.status))}</span></div><div class="item-meta"><span>版本 ${esc(version?.version_no || "—")}</span><span>${esc(version?.template_version || "resume-template-v1")}</span><span>${esc(date(item.updated_at))}</span></div><div class="item-actions">${version ? `<button class="button soft small" data-action="open-variant" data-id="${esc(item.id)}">编辑内容</button><button class="button primary small" data-action="export-variant" data-version-id="${esc(version.id)}">导出 PDF</button>` : ""}${isLast ? `<a class="button soft small" href="/api/v1/exports/${esc(state.workspace.lastExport.id)}/file" target="_blank" rel="noreferrer">下载 PDF</a>` : ""}<button class="button link-button small" data-action="delete-variant" data-id="${esc(item.id)}">删除</button></div></article>`;
        }

        function variantEditor(item) {
          const version = item?.versions?.[0];
          if (!item || !version) return "";
          const sections = version.content?.sections || [];
          const layout = version.layout || {};
          const selectedBold = new Set(layout.bold_segment_keys || []);
          return `<section class="card variant-editor-card" style="margin-top:18px"><div class="card-head"><div><h3>编辑岗位版内容与排版</h3><p>保存会追加不可变版本 v${esc(String((version.version_no || 0) + 1))}；预览和 PDF 使用同一份内容与 layout。</p></div><button class="button link-button small" type="button" data-action="close-variant">收起</button></div><div class="card-body"><form id="variant-editor-form" class="variant-editor-form"><div class="variant-sections" data-variant-sections>${sections.map((section, index) => `<section class="variant-section" data-variant-section="${esc(section.section_key)}"><div class="item-title"><strong>${esc(section.title || section.section_key)}</strong><span class="item-actions"><button class="button outline small" type="button" data-action="move-variant-section" data-direction="up" data-index="${index}" ${index === 0 ? "disabled" : ""}>上移</button><button class="button outline small" type="button" data-action="move-variant-section" data-direction="down" data-index="${index}" ${index === sections.length - 1 ? "disabled" : ""}>下移</button></span></div>${(section.segments || []).map((segment) => `<div class="field-group" style="margin-top:10px"><label for="variant-segment-${esc(segment.segment_key)}">${esc(segment.segment_key)}</label><textarea id="variant-segment-${esc(segment.segment_key)}" class="textarea variant-segment" data-variant-segment="${esc(segment.segment_key)}">${esc(segment.text)}</textarea><label class="micro" style="display:flex;gap:7px;align-items:center;margin-top:6px"><input type="checkbox" data-variant-bold="${esc(segment.segment_key)}" ${selectedBold.has(String(segment.segment_key)) ? "checked" : ""} /> 加粗这一段</label></div>`).join("")}</section>`).join("") || `<div class="empty"><div><strong>没有可编辑段落</strong><p>请回到资料页确认简历版本。</p></div></div>`}</div><div class="form-row" style="margin-top:16px"><div class="field-group"><label>字体</label><select class="select" name="font_family"><option value="noto_sans_sc" selected>Noto Sans SC / 系统无衬线</option></select></div><div class="field-group"><label>字号（pt）</label><input class="field" name="font_size_pt" type="number" min="9" max="12" step="0.5" value="${esc(String(layout.font_size_pt ?? 10.5))}" /></div></div><div class="form-row" style="margin-top:10px"><div class="field-group"><label>行距</label><input class="field" name="line_height" type="number" min="1.2" max="1.8" step="0.1" value="${esc(String(layout.line_height ?? 1.4))}" /></div><div class="field-group"><label>模块间距（pt）</label><input class="field" name="section_spacing_pt" type="number" min="4" max="16" step="1" value="${esc(String(layout.section_spacing_pt ?? 8))}" /></div></div><div class="form-foot"><span class="micro">当前岗位版 revision ${esc(String(item.revision))}；并发保存冲突时请刷新。</span><button class="button primary small" type="submit">保存新版本</button></div></form></div></section>`;
        }

        function decorateVariantPage() {
          if (state.workspace.page !== "variants" || !state.workspace.selectedVariant) return;
          const content = document.querySelector(".workspace-content");
          if (!content || content.querySelector(".variant-editor-card")) return;
          content.insertAdjacentHTML("beforeend", variantEditor(state.workspace.selectedVariant));
        }

        function decorateReportPage() {
          if (state.workspace.page !== "report") return;
          const report = state.workspace.selectedAnalysis?.report;
          if (!report) return;
          const content = document.querySelector(".workspace-content");
          const reportGrid = content?.querySelector(".report-grid");
          if (!reportGrid || reportGrid.querySelector(".report-followup-card")) return;
          reportGrid.insertAdjacentHTML("beforeend", reportFollowupSections(report));
        }

        function interviewPage() {
          const interviews = state.workspace.data.interviews;
          const selected = state.workspace.selectedInterview;
          const pools = state.workspace.data.pool.filter((item) => item.latest_analysis && item.resume_version_id);
          return `${heading("INTERVIEW PRACTICE", "面试练习", "围绕一个岗位报告逐轮回答；刷新后仍从当前题目恢复。", `<button class="button outline small" data-action="refresh">刷新</button>`)}<div class="interview-layout"><aside class="interview-nav"><button class="button primary small" data-action="new-interview" ${pools.length ? "" : "disabled"}>从报告开始</button>${interviews.length ? interviews.map((item) => `<div class="interview-item ${selected?.id === item.id ? "active" : ""}" data-action="open-interview" data-id="${esc(item.id)}"><strong>${esc(item.title)}</strong><small>${esc(labelStatus(item.status))} · ${esc(date(item.updated_at))}</small></div>`).join("") : `<div class="empty" style="min-height:120px"><div><strong>还没有会话</strong><p>先在匹配池生成报告。</p></div></div>`}</aside><section>${selected ? interviewDetail(selected) : `<div class="empty" style="min-height:360px"><div><strong>选择一场练习</strong><p>系统会生成 3 道主问题，每题最多一次追问。</p></div></div>`}</section></div>`;
        }

        function interviewDetail(item) {
          const current = item.questions?.find((question) => question.id === item.current_question_id) || item.questions?.find((question) => question.status === "awaiting_answer");
          const history = (item.questions || []).map((question) => `<article class="interview-question-row"><div class="item-title"><strong>${question.question_type === "followup" ? `第 ${esc(question.main_no)} 题 · 追问` : `第 ${esc(question.main_no || question.position_no)} 题`}</strong><span class="tag ${statusClass(question.status)}">${esc(labelStatus(question.status))}</span></div><p class="micro" style="margin-top:7px">${esc(question.question_text)}</p>${question.answer ? `<div class="compare-pane original" style="margin-top:9px"><h4>我的回答</h4><p>${esc(question.answer.answer_text)}</p></div>` : ""}${question.feedback ? `<div class="feedback-box"><strong>本题反馈</strong><ul>${(question.feedback.content?.strengths || []).map((value) => `<li>${esc(value)}</li>`).join("")}${(question.feedback.content?.gaps || []).map((value) => `<li>${esc(value)}</li>`).join("")}${(question.feedback.content?.suggestions || []).map((value) => `<li>${esc(value)}</li>`).join("")}</ul>${question.feedback.needs_followup ? `<span class="tag opportunity">建议补充一次追问</span>` : ""}</div>` : ""}</article>`).join("");
          const summary = item.summary ? `<div class="summary-box" style="margin-top:18px"><strong>练习总结 · ${esc(item.summary.completion_type === "full" ? "完整完成" : "提前结束")}</strong><p>${esc(item.summary.content?.content?.summary || item.summary.content?.summary || "已根据实际提交的回答生成练习总结。")}</p><p>已回答主问题：${esc(String(item.summary.content?.answered_main_count ?? "—"))}；追问：${esc(String(item.summary.content?.answered_followup_count ?? "—"))}</p><p>下一步：${esc((item.summary.content?.content?.next_steps || item.summary.content?.next_steps || []).join("；"))}</p></div>` : "";
          return `<div class="question"><div class="item-title"><div><div class="eyebrow">${esc(item.title)}</div><h3>${item.status === "awaiting_answer" && current ? `第 ${esc(current.main_no || current.position_no)} 题：${esc(current.question_text)}` : esc(labelStatus(item.status))}</h3></div><span class="tag ${statusClass(item.status)}">${esc(labelStatus(item.status))}</span></div>${item.status === "awaiting_answer" && current ? `<form id="answer-form" class="answer-box"><input type="hidden" name="question_id" value="${esc(current.id)}" /><label class="field-label" for="interview-answer">你的回答</label><textarea id="interview-answer" class="textarea" name="answer_text" placeholder="按背景、行动、结果写下真实回答……" required></textarea><div class="form-foot"><span class="micro">本轮提交后会生成反馈；每个主问题最多一次追问。</span><button class="button primary small" type="submit">提交回答</button></div></form><button class="button outline small" style="margin-top:12px" data-action="finish-interview" data-id="${esc(item.id)}">提前结束并生成总结</button>` : item.status === "processing" ? `<div class="callout opportunity" style="margin-top:18px"><i class="spinner" style="display:inline-block;vertical-align:middle;margin-right:7px"></i>正在生成本轮反馈，刷新后继续。</div>` : summary || `<div class="callout opportunity" style="margin-top:18px">当前没有可回答题目，刷新后可继续。</div>`}${history ? `<section class="interview-history" style="margin-top:22px"><div class="card-head"><div><h3>逐题记录</h3><p>主问题和最多一次追问均保留回答与反馈。</p></div></div><div class="card-list" style="margin-top:12px">${history}</div></section>` : ""}<div class="item-actions" style="margin-top:18px"><button class="button link-button small" data-action="delete-interview" data-id="${esc(item.id)}">删除这场练习（先查看影响）</button></div><details class="raw-report"><summary>查看题目与状态 JSON</summary><pre>${esc(json(item))}</pre></details></div>`;
        }

        function usagePage() {
          const usage = state.workspace.data.usage || { balances: [], entries: [] };
          return `${heading("USAGE", "用量", "所有计次操作都会在开始前说明消耗；失败释放预留，重复请求回到原任务。", `<button class="button outline small" data-action="refresh">刷新</button>`)}<section class="card"><div class="balance-grid">${(usage.balances || []).map((item) => `<div class="balance"><div class="eyebrow">${esc(item.feature)}</div><strong>${esc(String(item.available))}</strong><small>可用${esc(item.unit)} · 已消耗 ${esc(String(item.settled_total))}</small></div>`).join("") || `<div class="empty" style="grid-column:1/-1"><div><strong>暂无用量记录</strong><p>注册本地账号后会获得对应身份的试用次数。</p></div></div>`}</div></section><section class="card" style="margin-top:18px"><div class="card-head"><div><h3>最近流水</h3><p>这里不展示简历、JD、回答或令牌正文。</p></div></div><div class="card-body">${usage.entries?.length ? usage.entries.slice(0, 12).map((item) => `<div class="usage-entry"><span>${esc(date(item.created_at))}</span><div>${esc(item.feature)} · ${esc(item.entry_type)}<br /><span>${esc(item.reason || item.source_type || "系统记录")}</span></div><strong>${esc(String(item.count > 0 ? `+${item.count}` : item.count))}</strong></div>`).join("") : `<div class="empty"><div><strong>还没有流水</strong><p>完成一次分析后，这里会显示预留、结算或释放。</p></div></div>`}</div></section>`;
        }

        function statsPage() {
          const stats = state.workspace.data.stats || {};
          return `${heading("PERSONAL STATS", "统计与反馈", "去投递点击只表示 Purslyx 发起过一次跳转，不代表外部平台已经收到简历。", `<button class="button outline small" data-action="refresh">刷新</button>`)}<div class="metric-grid"><div class="metric-card"><div class="eyebrow">岗位</div><strong>${esc(String(stats.job_pool_items ?? 0))}</strong><span>匹配池岗位</span></div><div class="metric-card"><div class="eyebrow">报告</div><strong>${esc(String(stats.completed_analyses ?? 0))}</strong><span>完成分析</span></div><div class="metric-card"><div class="eyebrow">采用</div><strong>${esc(String(stats.adopted_rewrites ?? 0))}</strong><span>已采用改写</span></div><div class="metric-card"><div class="eyebrow">点击</div><strong>${esc(String(stats.apply_clicks ?? 0))}</strong><span>去投递点击</span></div></div><section class="card" style="margin-top:18px"><div class="card-head"><div><h3>给 Purslyx 一句话反馈</h3><p>反馈和使用统计分开记录，帮助判断产品下一步。</p></div></div><div class="card-body"><form id="feedback-form" class="form-card"><div class="form-row"><div class="field-group"><label>评分</label><select class="select" name="rating"><option value="5">5 · 很有帮助</option><option value="4">4 · 有帮助</option><option value="3">3 · 一般</option><option value="2">2 · 需要改进</option><option value="1">1 · 不符合预期</option></select></div><div class="field-group"><label>反馈类型</label><select class="select" name="feedback_type"><option value="experience">使用体验</option><option value="accuracy">分析依据</option><option value="workflow">流程建议</option></select></div></div><div class="field-group"><label>内容</label><textarea class="textarea" name="content" style="min-height:110px" placeholder="哪一步最有帮助？还有哪里需要更清楚？" required></textarea></div><div class="form-foot"><span class="micro">反馈不会改变已经生成的报告分数。</span><button class="button primary small" type="submit">提交反馈</button></div></form></div></section>`;
        }

        function taskTypeLabel(value) {
          return { document_parse: "资料解析", analysis: "岗位分析", rewrite: "简历改写", resume_export: "PDF 导出", interview_opening: "面试开场", interview_feedback: "面试反馈" }[value] || value || "任务";
        }

        function accountStatusLabel(value) {
          return { active: "正常", suspended: "已暂停", pending_verification: "待验证" }[value] || value || "未知";
        }

        function taskDetail(item) {
          if (!item) return "";
          const refs = (item.input_versions || []).map((ref) => `<span class="version-chip">${esc(ref.resource_type)} · ${esc(ref.resource_id)}${ref.version_no ? ` · v${esc(ref.version_no)}` : ""}</span>`).join("");
          return `<section class="card pool-detail"><div class="card-head"><div><div class="eyebrow">TASK DETAIL</div><h3>${esc(taskTypeLabel(item.task_type))}</h3><p>${esc(item.id)} · ${esc(labelStatus(item.status))} · ${esc(item.current_step || "等待调度")}</p></div><button class="button link-button small" data-action="close-task">收起</button></div><div class="item-meta"><span>重试 ${esc(String(item.retry_count || 0))} 次</span><span>更新时间 ${esc(date(item.updated_at))}</span>${item.usage_reservation ? `<span>预留 ${esc(String(item.usage_reservation.count))} ${esc(item.usage_reservation.feature)}</span>` : ""}</div>${item.failure ? `<div class="callout attention">${esc(item.failure.message || item.failure.code || "任务失败")}</div>` : ""}${refs ? `<div class="version-strip">${refs}</div>` : `<div class="micro" style="margin-top:14px">该任务没有可展示的输入版本引用。</div>`}${item.result ? `<details class="raw-report"><summary>查看结果摘要</summary><pre>${esc(json(item.result))}</pre></details>` : ""}${item.required_actions?.length ? `<div class="callout opportunity">${esc(item.required_actions.map((action) => action.label || action.code || "需要下一步").join("；"))}</div>` : ""}</section>`;
        }

        function tasksPage() {
          const tasks = state.workspace.data.tasks || [];
          const selected = state.workspace.selectedTask;
          const counts = tasks.reduce((result, item) => { result[item.status] = (result[item.status] || 0) + 1; return result; }, {});
          return `${heading("TASK CENTER", "任务中心", "每个任务都保留状态、进度、输入版本引用和可恢复动作；任务详情不复制简历或岗位正文。", `<button class="button outline small" data-action="refresh">刷新</button>`)}${selected ? taskDetail(selected) : ""}<section class="card"><div class="card-head"><div><h3>最近任务</h3><p>${esc(String(tasks.length))} 条记录 · 排队 ${esc(String(counts.queued || 0))} · 失败 ${esc(String(counts.failed || 0))} · 已完成 ${esc(String(counts.succeeded || 0))}</p></div></div><div class="card-body">${tasks.length ? `<div class="task-list">${tasks.map(taskRow).join("")}</div>` : `<div class="empty"><div><strong>暂无任务</strong><p>发起分析、改写、面试或 PDF 导出后，任务会显示在这里。</p></div></div>`}</div></section>`;
        }

        function recruiterPreferenceSection(documents) {
          const candidates = documents.filter((item) => item.document_type === "resume" && item.subject_type === "candidate_resume" && item.latest_version);
          const selectedId = state.workspace.recruiterSubjectId || candidates[0]?.id || "";
          const existing = state.workspace.data.preferences.find((item) => item.subject_document_id === selectedId);
          const content = existing?.version?.content || {};
          const locations = content.locations?.values?.join("、") || "杭州";
          const salary = content.salary || {};
          if (!candidates.length) return `<section class="card" style="margin-top:18px"><div class="card-head"><div><h3>候选人岗位期望（可选）</h3><p>先导入候选人简历，才能把明确的期望挂到该候选人名下。</p></div></div><div class="card-body"><div class="empty"><div><strong>还没有候选人简历</strong><p>这一步不是分析前置条件；有明确需求时再补充即可。</p></div></div></div></section>`;
          state.workspace.recruiterSubjectId = selectedId;
          return `<section class="card" style="margin-top:18px"><div class="card-head"><div><h3>候选人岗位期望（可选）</h3><p>只保存候选人明确提供的条件；每个候选人单独成组，不和其他资料串值。</p></div><span class="tag neutral">可选</span></div><div class="card-body"><form id="recruiter-preference-form" class="form-card"><div class="field-group"><label for="recruiter-preference-subject">候选人简历</label><select id="recruiter-preference-subject" class="select" name="subject_document_id" data-recruiter-subject>${candidates.map((item) => `<option value="${esc(item.id)}" ${item.id === selectedId ? "selected" : ""}>${esc(item.title)} · ${esc(item.latest_version.id.slice(0, 8))}</option>`).join("")}</select></div><div class="field-group"><label for="recruiter-preference-title">期望岗位</label><input id="recruiter-preference-title" class="field" name="job_title" value="${esc(content.job_title?.value || "前端工程师")}" required /></div><div class="form-row"><div class="field-group"><label>期望地点</label><input class="field" name="location" value="${esc(locations)}" required /></div><div class="field-group"><label>办公方式</label><select class="select" name="work_mode"><option value="onsite" ${content.work_mode?.value === "onsite" ? "selected" : ""}>现场办公</option><option value="hybrid" ${content.work_mode?.value === "hybrid" ? "selected" : ""}>混合办公</option><option value="remote" ${content.work_mode?.value === "remote" ? "selected" : ""}>远程</option></select></div></div><div class="form-row"><div class="field-group"><label>月薪下限（K）</label><input class="field" name="min_salary" inputmode="decimal" value="${esc(salary.min ? Number(salary.min) / 1000 : 18)}" required /></div><div class="field-group"><label>月薪上限（K）</label><input class="field" name="max_salary" inputmode="decimal" value="${esc(salary.max ? Number(salary.max) / 1000 : 28)}" required /></div></div><div class="form-foot"><span class="micro">候选人期望不影响求职者匹配池。</span><button class="button primary small" type="submit">保存候选人期望</button></div></form></div></section>`;
        }

        function resumeSegments(source) {
          return (source?.content?.sections || []).flatMap((section) => (section.segments || []).map((segment) => ({ ...segment, section_title: section.title || section.section_key })));
        }

        function rewriteSegmentItem(item) {
          const decision = item.current_decision || "未决定";
          const editId = `rewrite-edit-${item.id}`;
          return `<article class="rewrite-row"><div style="min-width:0;flex:1"><div class="item-title"><strong>${esc(item.source_segment_key)}</strong><span class="tag ${decision === "adopt" ? "success" : decision === "keep_original" ? "neutral" : "opportunity"}">${esc(decision === "adopt" ? "已采用" : decision === "keep_original" ? "保留原文" : decision)}</span></div><div class="rewrite-compare"><div class="compare-pane original"><h4>原文</h4><p>${esc(item.original_text)}</p></div><div class="compare-pane suggested"><h4>建议</h4><p>${esc(item.suggested_text || "暂无建议")}</p></div></div><div class="field-group" style="margin-top:12px"><label for="${esc(editId)}">编辑后采用（可选）</label><textarea id="${esc(editId)}" class="textarea" style="min-height:88px">${esc(item.suggested_text || item.original_text || "")}</textarea></div><div class="item-actions"><button class="button primary small" data-action="decide-rewrite" data-decision="adopt" data-rewrite-id="${esc(state.workspace.selectedRewrite?.id || "")}" data-segment-id="${esc(item.id)}" data-base-decision-no="${esc(String(item.decision_no || 0))}">采用建议</button><button class="button soft small" data-action="decide-rewrite" data-decision="edited" data-edit-id="${esc(editId)}" data-rewrite-id="${esc(state.workspace.selectedRewrite?.id || "")}" data-segment-id="${esc(item.id)}" data-base-decision-no="${esc(String(item.decision_no || 0))}">采用编辑</button><button class="button outline small" data-action="decide-rewrite" data-decision="keep_original" data-rewrite-id="${esc(state.workspace.selectedRewrite?.id || "")}" data-segment-id="${esc(item.id)}" data-base-decision-no="${esc(String(item.decision_no || 0))}">保留原文</button></div>${item.evidence?.length ? `<div class="evidence">依据：${esc(item.evidence.map((evidence) => evidence.quote).join("；"))}</div>` : `<div class="evidence missing">当前建议没有额外事实依据，采用前请人工核对。</div>`}</div></article>`;
        }

        function rewritePage() {
          const analysis = state.workspace.selectedAnalysis;
          const source = state.workspace.rewriteSource;
          if (!analysis || !source) return `${heading("REWRITE", "事实与改写", "从一份已完成的匹配报告开始。", `<button class="button outline small" data-page="report">回到报告</button>`)}<div class="empty"><div><strong>还没有准备好的简历版本</strong><p>请从匹配报告进入改写。</p></div></div>`;
          const segments = resumeSegments(source.version);
          const selectedKeys = new Set(state.workspace.rewriteSegmentKeys || []);
          const facts = state.workspace.data.facts || [];
          const rewrite = state.workspace.selectedRewrite;
          return `${heading("FACTS & REWRITE", "事实与逐段改写", "先补充可核对事实，再选择需要改写的简历段落；每段都可以单独采用、编辑采用或保留原文。", `<button class="button outline small" data-page="report">回到报告</button>`)}<div class="callout opportunity">本次改写固定引用简历版本 ${esc(source.version.id)}；任务和日志只保留版本引用，不复制正文。</div><div class="editor-grid"><section class="card"><div class="card-head"><div><h3>选择简历段落</h3><p>当前版本共 ${esc(String(segments.length))} 个可用段落。</p></div></div><div class="card-body"><div class="fact-list">${segments.map((segment) => `<label class="fact-row" style="cursor:pointer"><div><strong>${esc(segment.section_title)} · ${esc(segment.segment_key)}</strong><small>${esc(segment.text)}</small></div><input type="checkbox" data-rewrite-segment="${esc(segment.segment_key)}" ${selectedKeys.has(segment.segment_key) ? "checked" : ""} /></label>`).join("") || `<div class="empty"><div><strong>版本没有可改写段落</strong><p>请确认简历解析结果包含正文段落。</p></div></div>`}</div><form id="rewrite-form" class="form-card" style="margin-top:16px"><div class="field-group"><label>本次引用的已确认事实</label><div class="fact-list">${facts.length ? facts.map((fact) => `<label class="permission-option"><input type="checkbox" name="fact_version_id" value="${esc(fact.version?.id || "")}" data-rewrite-fact /> <span>${esc(fact.fact_category)}：${esc(fact.fact_text)}</span></label>`).join("") : `<div class="micro">还没有补充事实；可以先只基于已确认简历生成建议。</div>`}</div></div><div class="form-foot"><span class="micro">开始改写确认消耗 1 次改写。</span><button class="button primary small" type="submit" ${segments.length ? "" : "disabled"}>生成逐段建议</button></div></form></div></section><aside><section class="card"><div class="card-head"><div><h3>补充一个事实</h3><p>事实需挂在当前简历版本和真实段落上。</p></div></div><div class="card-body"><form id="fact-form" class="form-card"><div class="field-group"><label>事实类别</label><input class="field" name="fact_category" value="项目结果" required /></div><div class="field-group"><label>来源段落</label><select class="select" name="source_segment_key">${segments.map((segment) => `<option value="${esc(segment.segment_key)}">${esc(segment.segment_key)}</option>`).join("")}</select></div><div class="field-group"><label>事实内容</label><textarea class="textarea" name="fact_text" style="min-height:100px" placeholder="例如：上线后首屏耗时下降 30%" required></textarea></div><div class="form-foot"><span class="micro">保存后成为事实 version 1。</span><button class="button soft small" type="submit" ${segments.length ? "" : "disabled"}>保存事实</button></div></form></div></section></aside></div>${rewrite ? `<section class="card" style="margin-top:18px"><div class="card-head"><div><h3>改写结果</h3><p>${esc(rewrite.id)} · ${esc(labelStatus(rewrite.status))} · 采用决定会追加流水。</p></div><div class="item-actions"><button class="button primary small" data-action="create-variant-from-rewrite" ${rewrite.status === "available" ? "" : "disabled"}>用采用结果制作岗位版</button></div></div><div class="card-body"><div class="rewrite-list">${(rewrite.segments || []).map(rewriteSegmentItem).join("") || `<div class="empty"><div><strong>还没有段落建议</strong><p>返回上方选择段落并生成建议。</p></div></div>`}</div></div></section>` : ""}`;
        }

        function adminMetricsFilterForm() {
          const filters = state.workspace.adminMetricFilters || {};
          return `<section class="card" style="margin-bottom:18px"><form id="admin-metric-filters" class="form-card"><div class="filter-grid"><div class="field-group"><label>开始日期</label><input class="field" type="date" name="date_from" value="${esc(filters.date_from || "")}" /></div><div class="field-group"><label>结束日期（含当天）</label><input class="field" type="date" name="date_to" value="${esc(filters.date_to || "")}" /></div><div class="field-group"><label>注册身份</label><select class="select" name="registration_role"><option value="all" ${filters.registration_role === "all" ? "selected" : ""}>全部身份</option><option value="seeker" ${filters.registration_role === "seeker" ? "selected" : ""}>求职</option><option value="recruiter" ${filters.registration_role === "recruiter" ? "selected" : ""}>招聘</option></select></div><div class="field-group"><label>功能</label><select class="select" name="feature"><option value="all" ${filters.feature === "all" ? "selected" : ""}>全部功能</option><option value="analysis" ${filters.feature === "analysis" ? "selected" : ""}>分析</option><option value="rewrite" ${filters.feature === "rewrite" ? "selected" : ""}>改写</option><option value="interview" ${filters.feature === "interview" ? "selected" : ""}>面试</option><option value="import" ${filters.feature === "import" ? "selected" : ""}>资料导入</option></select></div></div><div class="form-foot"><span class="micro">统计按 Asia/Shanghai 自然日计算，最多查询 93 天。</span><button class="button soft small" type="submit">应用筛选</button></div></form></section>`;
        }

        function adminFeedbackDetail() {
          const item = state.workspace.selectedFeedback;
          if (!item) return "";
          return `<section class="card admin-user-detail" style="margin-bottom:18px"><div class="card-head"><div><div class="eyebrow">FEEDBACK DETAIL</div><h3>${esc(item.feedback_type || "产品反馈")} · ${esc(String(item.rating || "—"))} 分</h3><p>${esc(item.id)} · ${esc(date(item.created_at))}</p></div><button class="button link-button small" data-action="close-feedback">收起</button></div><div class="card-body"><div class="callout">${esc(item.content || "（未填写文字）")}</div><div class="item-meta"><span>类型：${esc(item.feedback_type || "—")}</span><span>状态：${esc(labelStatus(item.status))}</span><span>上下文：${esc(item.context_type || "general")}</span>${item.context_id ? `<span>关联：${esc(item.context_id)}</span>` : ""}</div></div></section>`;
        }

        function adminMetricsPage() {
          const metrics = state.workspace.data.admin.metrics;
          const costs = state.workspace.data.admin.costs;
          const feedback = state.workspace.data.admin.feedback || [];
          if (!metrics) return `${heading("ADMIN", "站点概况", "当前账号没有读到站点指标；点击刷新重试。", `<button class="button outline small" data-action="refresh">刷新</button>`)}<div class="empty"><div><strong>指标暂不可用</strong><p>服务端会按权限返回管理数据。</p></div></div>`;
          return `${heading("ADMIN OVERVIEW", "站点概况", "按日期、身份和功能筛选聚合指标；成本、反馈和账号操作均留有审计入口。", `<button class="button outline small" data-action="refresh">刷新</button>`)}${adminMetricsFilterForm()}${adminFeedbackDetail()}<div class="admin-grid"><div class="admin-stat"><div class="eyebrow">账号</div><strong>${esc(String(metrics.accounts?.total ?? 0))}</strong><span>求职 ${esc(String(metrics.accounts?.seeker ?? 0))} · 招聘 ${esc(String(metrics.accounts?.recruiter ?? 0))}</span></div><div class="admin-stat"><div class="eyebrow">岗位</div><strong>${esc(String(metrics.job_pool_items ?? 0))}</strong><span>当前未删除岗位</span></div><div class="admin-stat"><div class="eyebrow">报告</div><strong>${esc(String(metrics.analyses ?? 0))}</strong><span>筛选范围内完成分析</span></div><div class="admin-stat"><div class="eyebrow">面试</div><strong>${esc(String(metrics.interviews ?? 0))}</strong><span>筛选范围内会话</span></div><div class="admin-stat"><div class="eyebrow">去投递</div><strong>${esc(String(metrics.apply_clicks_today ?? 0))}</strong><span>统计截至 ${esc(metrics.metric_date)}</span></div><div class="admin-stat"><div class="eyebrow">模型成本</div><strong>${costs?.known_cost_usd == null ? "未知" : `$${esc(Number(costs.known_cost_usd).toFixed(4))}`}</strong><span>未知成本调用 ${esc(String(costs?.unknown_cost_calls ?? 0))}</span></div></div><section class="card" style="margin-top:18px"><div class="card-head"><div><h3>产品反馈</h3><p>列表先展示脱敏元数据；打开详情才读取反馈正文，状态变更会写入审计日志。</p></div></div><div class="card-body">${feedback.length ? `<div class="admin-list">${feedback.map((item) => `<article class="admin-row"><div><strong>${esc(item.feedback_type)} · ${esc(String(item.rating || "—"))} 分</strong><small>${esc(item.id)} · ${esc(item.account_id || "未知账号")} · ${esc(date(item.created_at))}</small></div><div class="item-actions"><span class="tag ${item.status === "closed" ? "success" : item.status === "reviewed" ? "opportunity" : "neutral"}">${esc(labelStatus(item.status))}</span><button class="button link-button small" data-action="open-feedback" data-id="${esc(item.id)}">查看</button>${new Set(state.session.account?.admin_permissions || []).has("admin.stats.read") && item.status !== "closed" ? `<button class="button link-button small" data-action="cycle-feedback" data-id="${esc(item.id)}" data-status="${esc(item.status)}" data-revision="${esc(String(item.revision || 1))}">${item.status === "new" ? "标记已查看" : "关闭反馈"}</button>` : ""}</div></article>`).join("")}</div>` : `<div class="empty"><div><strong>暂无反馈</strong><p>用户反馈会在这里汇总。</p></div></div>`}</div></section>`;
        }

        function adminUserDetailCard() {
          const selected = state.workspace.selectedAdminUser;
          if (!selected?.account) return "";
          const account = selected.account;
          const usage = selected.usage || {};
          const roles = state.workspace.data.admin.roles || [];
          const currentRoleIds = new Set((selected.admin_roles || []).map((item) => item.id));
          const permissions = new Set(state.session.account?.admin_permissions || []);
          const canAssign = permissions.has("admin.roles.manage") && account.id !== state.session.account?.id;
          const roleChips = (selected.admin_roles || []).map((role) => `<span class="role-chip">${esc(role.name)} · ${esc(String(role.permissions?.length || 0))} 项权限</span>`).join("");
          return `<section class="card admin-user-detail" style="margin-bottom:18px"><div class="card-head"><div><div class="eyebrow">USER DETAIL</div><h3>${esc(account.email)}</h3><p>${esc(account.id)} · ${esc(account.registration_role === "seeker" ? "求职身份" : "招聘身份")} · revision ${esc(String(account.revision || 1))}</p></div><button class="button link-button small" data-action="close-admin-user">收起</button></div><div class="item-meta"><span>状态：${esc(accountStatusLabel(account.status))}</span><span>${account.email_verified ? "邮箱已验证" : "邮箱未验证"}</span><span>注册于 ${esc(date(account.created_at))}</span><span>最近登录 ${esc(date(account.last_login_at))}</span></div><div class="role-chip-list">${roleChips || `<span class="micro">当前没有后台角色</span>`}</div><div class="balance-grid" style="margin-top:16px">${(usage.balances || []).map((item) => `<div class="balance"><div class="eyebrow">${esc(item.feature)}</div><strong>${esc(String(item.available))}</strong><small>可用 · 已消耗 ${esc(String(item.settled_total))}</small></div>`).join("") || `<div class="micro">暂无用量余额</div>`}</div>${permissions.has("admin.roles.manage") ? `<form id="admin-user-roles-form" class="form-card" style="margin-top:16px"><div class="field-group"><label>后台角色分配</label><div class="permission-grid">${roles.filter((role) => role.status === "active").map((role) => `<label class="permission-option"><input type="checkbox" name="role_ids" value="${esc(role.id)}" ${currentRoleIds.has(role.id) ? "checked" : ""} ${canAssign ? "" : "disabled"} /> <span><strong>${esc(role.name)}</strong><br />${esc(role.description || "无描述")}</span></label>`).join("") || `<span class="micro">当前还没有可用的角色。</span>`}</div></div><div class="form-foot"><span class="micro">${canAssign ? "保存会替换该账号的后台角色，并增加账号 revision。" : "不能修改当前登录管理员自己的角色。"}</span><button class="button primary small" type="submit" ${canAssign && roles.some((role) => role.status === "active") ? "" : "disabled"}>保存角色</button></div></form>` : `<div class="callout opportunity" style="margin-top:16px">当前账号没有角色分配权限，只能查看账号详情。</div>`}</section>`;
        }

        function enhancedAdminUsersPage() {
          const users = state.workspace.data.admin.users || [];
          const canManage = new Set(state.session.account?.admin_permissions || []).has("admin.users.manage_status");
          return `${heading("ADMIN USERS", "用户管理", "按邮箱、注册身份和状态筛选；打开详情可以查看用量和后台角色。", `<button class="button outline small" data-action="refresh">刷新</button>`)}${adminUserDetailCard()}<section class="card"><form id="admin-user-search" class="form-card"><div class="filter-grid"><div class="field-group"><label>邮箱搜索</label><input class="field" name="search" placeholder="可留空" /></div><div class="field-group"><label>注册身份</label><select class="select" name="registration_role"><option value="">全部</option><option value="seeker">求职</option><option value="recruiter">招聘</option></select></div><div class="field-group"><label>账号状态</label><select class="select" name="status"><option value="">全部</option><option value="active">正常</option><option value="suspended">已暂停</option><option value="pending_verification">待验证</option></select></div></div><div class="form-foot"><span class="micro">列表只返回必要账号元数据；邮箱详情仍受管理员权限保护。</span><button class="button soft small" type="submit">筛选</button></div></form></section><section class="card" style="margin-top:18px"><div class="card-head"><div><h3>账号列表</h3><p>${esc(String(users.length))} 个账号</p></div></div><div class="card-body">${users.length ? `<div class="admin-list">${users.map((item) => `<article class="admin-row"><div><strong>${esc(item.email)}</strong><small>${esc(item.registration_role === "seeker" ? "求职" : "招聘")} · ${esc(accountStatusLabel(item.status))} · ${item.email_verified ? "已验证" : "未验证"} · ${esc(date(item.created_at))}</small></div><div class="item-actions"><span class="tag ${statusClass(item.status === "active" ? "available" : item.status === "suspended" ? "failed" : "unknown")}">${esc(accountStatusLabel(item.status))}</span><button class="button soft small" data-action="open-admin-user" data-id="${esc(item.id)}">查看详情</button>${canManage && item.id !== state.session.account?.id ? `<button class="button link-button small" data-action="toggle-user-status" data-id="${esc(item.id)}" data-status="${esc(item.status)}">${item.status === "suspended" ? "恢复" : "暂停"}</button>` : ""}</div></article>`).join("")}</div>` : `<div class="empty"><div><strong>没有匹配账号</strong><p>调整筛选条件后再试。</p></div></div>`}</div></section>`;
        }

        function permissionOptions(selected = []) {
          const allowed = new Set(state.session.account?.admin_permissions || []);
          return [...allowed].sort().map((keyName) => `<label class="permission-option"><input type="checkbox" name="permission_keys" value="${esc(keyName)}" ${selected.includes(keyName) ? "checked" : ""} /> <span>${esc(keyName)}</span></label>`).join("");
        }

        function adminRolesPage() {
          const roles = state.workspace.data.admin.roles || [];
          const selected = state.workspace.selectedRole;
          const permissions = selected?.permissions || [];
          return `${heading("ADMIN ROLES", "角色权限", "内置超级管理员只读；自定义角色只能授予当前管理员已有的权限。", `<button class="button outline small" data-action="refresh">刷新</button>`)}<div class="two-col"><section class="card"><div class="card-head"><div><h3>角色列表</h3><p>点击自定义角色进入编辑。</p></div></div><div class="card-body">${roles.length ? `<div class="admin-list">${roles.map((item) => `<article class="admin-row"><div><strong>${esc(item.name)} ${item.is_builtin ? "· 内置" : ""}</strong><small>${esc(item.description || "无描述")} · ${esc(item.status)} · ${esc(String(item.permissions?.length || 0))} 项权限</small></div><div class="item-actions">${item.is_builtin ? `<span class="tag neutral">只读</span>` : `<button class="button link-button small" data-action="select-role" data-id="${esc(item.id)}">编辑</button>`}</div></article>`).join("")}</div>` : `<div class="empty"><div><strong>暂无角色</strong><p>刷新后重试。</p></div></div>`}</div></section><section class="card"><div class="card-head"><div><h3>${selected ? "编辑自定义角色" : "创建自定义角色"}</h3><p>保存会增加角色 revision 并记录后台审计。</p></div>${selected ? `<button class="button link-button small" data-action="clear-role">新建</button>` : ""}</div><div class="card-body"><form id="admin-role-form" class="form-card"><input type="hidden" name="role_id" value="${esc(selected?.id || "")}" /><div class="field-group"><label>角色名称</label><input class="field" name="name" value="${esc(selected?.name || "演示运营")}" required /></div><div class="field-group"><label>描述</label><input class="field" name="description" value="${esc(selected?.description || "用于明天演示的受限后台角色")}" /></div><div class="field-group"><label>状态</label><select class="select" name="status"><option value="active" ${selected?.status !== "archived" ? "selected" : ""}>启用</option><option value="archived" ${selected?.status === "archived" ? "selected" : ""}>归档</option></select></div><div class="field-group"><label>可授予权限</label><div class="permission-grid">${permissionOptions(permissions)}</div></div><div class="form-foot"><span class="micro">当前账号可授予 ${esc(String((state.session.account?.admin_permissions || []).length))} 项权限。</span><button class="button primary small" type="submit">${selected ? "保存角色" : "创建角色"}</button></div></form></div></section></div>`;
        }

        function adminUsagePage() {
          const grants = state.workspace.data.admin.grants || [];
          const users = state.workspace.data.admin.users || [];
          return `${heading("ADMIN USAGE", "次数管理", "给指定账号追加分析、改写或面试次数；每笔发放都要求原因，并支持幂等重试。", `<button class="button outline small" data-action="refresh">刷新</button>`)}<section class="card"><div class="card-head"><div><h3>发放次数</h3><p>只允许发放当前账号角色允许使用的功能。</p></div></div><div class="card-body"><form id="admin-grant-form" class="form-card"><div class="field-group"><label>目标账号</label>${users.length ? `<select class="select" name="user_id">${users.filter((item) => item.status === "active").map((item) => `<option value="${esc(item.id)}">${esc(item.email)} · ${esc(item.registration_role)}</option>`).join("")}</select>` : `<input class="field" name="user_id" placeholder="粘贴账号 ID" required />`}</div><div class="form-row"><div class="field-group"><label>功能</label><select class="select" name="feature"><option value="analysis">分析</option><option value="rewrite">改写</option><option value="interview">面试</option></select></div><div class="field-group"><label>次数</label><input class="field" name="count" type="number" min="1" max="10000" value="5" required /></div></div><div class="field-group"><label>发放原因</label><input class="field" name="reason" value="明日 SDD 演示补充次数" required /></div><div class="form-foot"><span class="micro">写入用量流水和管理员审计。</span><button class="button primary small" type="submit">确认发放</button></div></form></div></section><section class="card" style="margin-top:18px"><div class="card-head"><div><h3>最近发放</h3><p>共 ${esc(String(grants.length))} 笔。</p></div></div><div class="card-body">${grants.length ? `<div class="admin-list">${grants.slice(0, 30).map((item) => `<article class="admin-row"><div><strong>${esc(item.feature)} +${esc(String(item.count))}</strong><small>${esc(item.account_id || "未知账号")} · ${esc(item.reason)} · ${esc(date(item.created_at))}</small></div><span class="tag success">${esc(String(item.after_available))} 可用</span></article>`).join("")}</div>` : `<div class="empty"><div><strong>暂无发放记录</strong><p>提交一次发放后会在这里留下记录。</p></div></div>`}</div></section>`;
        }

        function logRow(type, item) {
          const title = type === "operations" ? item.action : type === "security" ? item.event_type : taskTypeLabel(item.task_type);
          const detail = type === "operations" ? `${item.outcome || ""} · ${item.reason || "无原因"}` : type === "security" ? `${item.outcome || ""} · ${item.reason_code || "无原因码"}` : `${labelStatus(item.status)} · 重试 ${item.retry_count || 0} 次`;
          return `<article class="admin-row"><div><strong>${esc(title)}</strong><small>${esc(item.id)} · ${esc(detail)} · ${esc(date(item.created_at))}</small></div><button class="button link-button small" data-action="open-log" data-log-type="${esc(type)}" data-id="${esc(item.id)}">查看</button></article>`;
        }

        function adminLogsPageEnhanced() {
          const accountPermissions = new Set(state.session.account?.admin_permissions || []);
          const type = state.workspace.adminLogType || "operations";
          const permission = { operations: "admin.logs.operations.read", security: "admin.logs.security.read", tasks: "admin.logs.tasks.read" }[type];
          const rows = state.workspace.data.admin.logs?.[type] || [];
          const filters = state.workspace.adminLogFilters || {};
          const canExport = accountPermissions.has("admin.logs.export") && accountPermissions.has(permission);
          const selectedLog = state.workspace.selectedLog;
          const lastExport = state.workspace.lastLogExport;
          const exportNotice = lastExport ? `<div class="callout ${lastExport.status === "downloadable" ? "success" : lastExport.status === "failed" ? "attention" : "opportunity"}" style="margin-bottom:18px">导出 ${esc(lastExport.id)}：${esc(labelStatus(lastExport.status))}${lastExport.row_count !== null && lastExport.row_count !== undefined ? ` · ${esc(String(lastExport.row_count))} 行` : ""}${lastExport.status === "downloadable" ? ` · <a href="/api/v1/admin/log-exports/${esc(lastExport.id)}/file" target="_blank" rel="noreferrer">下载 ${esc(String(lastExport.format || "jsonl").toUpperCase())}</a>` : lastExport.status === "failed" ? ` · ${esc(lastExport.failure_code || "导出失败")}` : " · 刷新查看状态"}</div>` : "";
          const resultOptions = type === "tasks" ? `<option value="">全部任务状态</option><option value="queued">排队中</option><option value="running">处理中</option><option value="retry_wait">等待重试</option><option value="needs_input">待补充</option><option value="succeeded">已完成</option><option value="failed">失败</option><option value="cancelled">已取消</option>` : `<option value="">全部结果</option><option value="succeeded">成功</option><option value="denied">拒绝</option><option value="failed">失败</option>`;
          const specific = type === "operations" ? `<div class="field-group"><label>操作类型</label><input class="field" name="action" value="${esc(filters.action || "")}" placeholder="如 role.update" /></div>` : type === "security" ? `<div class="field-group"><label>安全事件类型</label><input class="field" name="event_type" value="${esc(filters.event_type || "")}" placeholder="如 login_failed" /></div>` : `<div class="field-group"><label>任务类型</label><input class="field" name="task_type" value="${esc(filters.task_type || "")}" placeholder="如 analysis" /></div><div class="field-group"><label>任务状态</label><select class="select" name="task_status"><option value="">全部</option><option value="queued" ${filters.task_status === "queued" ? "selected" : ""}>排队中</option><option value="running" ${filters.task_status === "running" ? "selected" : ""}>处理中</option><option value="succeeded" ${filters.task_status === "succeeded" ? "selected" : ""}>已完成</option><option value="failed" ${filters.task_status === "failed" ? "selected" : ""}>失败</option><option value="cancelled" ${filters.task_status === "cancelled" ? "selected" : ""}>已取消</option></select></div><div class="field-group"><label>最小重试次数</label><input class="field" name="retry_count_min" type="number" min="0" value="${esc(filters.retry_count_min ?? "")}" /></div>`;
          const detail = selectedLog ? `<section class="card" style="margin-bottom:18px"><div class="card-head"><div><h3>日志详情</h3><p>${esc(selectedLog.type)} · ${esc(selectedLog.item?.id || "")}</p></div><button class="button link-button small" data-action="close-log">收起</button></div><div class="card-body"><pre class="raw-report">${esc(json(selectedLog.item))}</pre></div></section>` : "";
          return `${heading("ADMIN LOGS", "日志管理", "按日期、账号、结果和类型筛选；导出会冻结当前筛选并生成私有文件。", `${canExport ? `<button class="button outline small" data-action="export-logs">导出当前筛选</button>` : ""}<button class="button outline small" data-action="refresh">刷新</button>`)}${exportNotice}<section class="card" style="margin-bottom:18px"><form id="admin-log-filters" class="form-card"><div class="filter-grid"><div class="field-group"><label>开始日期</label><input class="field" type="date" name="created_from" value="${esc(filters.created_from || "")}" /></div><div class="field-group"><label>结束日期（含当天）</label><input class="field" type="date" name="created_to" value="${esc(filters.created_to || "")}" /></div><div class="field-group"><label>账号 ID</label><input class="field" name="account_id" value="${esc(filters.account_id || "")}" placeholder="可留空" /></div><div class="field-group"><label>请求 ID</label><input class="field" name="request_id" value="${esc(filters.request_id || "")}" placeholder="可留空" /></div><div class="field-group"><label>结果</label><select class="select" name="result">${resultOptions.replace(`value="${esc(filters.result || "")}"`, `value="${esc(filters.result || "")}" selected`)}</select></div>${specific}<div class="field-group"><label>导出格式</label><select class="select" name="export_format"><option value="csv" ${state.workspace.adminLogFormat === "csv" ? "selected" : ""}>CSV</option><option value="jsonl" ${state.workspace.adminLogFormat === "jsonl" ? "selected" : ""}>JSONL</option></select></div></div><div class="form-foot"><span class="micro">日期按 Asia/Shanghai 转换；列表最多显示最近 20 条。</span><button class="button soft small" type="submit">应用筛选</button></div></form></section><div class="admin-tabs">${["operations", "security", "tasks"].map((item) => `<button class="button admin-tab ${type === item ? "active" : ""}" data-action="admin-log-type" data-log-type="${item}" ${accountPermissions.has({ operations: "admin.logs.operations.read", security: "admin.logs.security.read", tasks: "admin.logs.tasks.read" }[item]) ? "" : "disabled"}>${item === "operations" ? "操作" : item === "security" ? "安全" : "任务"}</button>`).join("")}</div>${detail}${accountPermissions.has(permission) ? `<section class="card"><div class="card-head"><div><h3>${type === "operations" ? "操作" : type === "security" ? "安全" : "任务"}日志</h3><p>${esc(String(rows.length))} 条当前筛选结果。</p></div></div><div class="card-body">${rows.length ? `<div class="admin-list">${rows.map((item) => logRow(type, item)).join("")}</div>` : `<div class="empty"><div><strong>暂无匹配日志</strong><p>调整筛选条件或完成一次操作后再试。</p></div></div>`}</div></section>` : `<div class="empty"><div><strong>没有该日志类型的权限</strong><p>请切换到当前管理员可以读取的日志。</p></div></div>`}`;
        }

        function render() {
          if (state.screen === "formal" && !state.session.account) {
            app.innerHTML = appFrame(authPage());
          } else if (state.screen === "formal") {
            app.innerHTML = `${topbar()}${workspaceShell()}${state.toast ? `<div class="toast">${esc(state.toast)}</div>` : ""}`;
          } else {
            app.innerHTML = appFrame(demoPage());
          }
          // 多页面 Web 端在每次局部刷新后重新补上语义化表单控件。
          decoratePreferenceForms();
          decorateDocumentForms();
          decorateRecruiterAnalysisForm();
          decorateReportPage();
          decorateVariantPage();
          scheduleWorkspacePoll();
        }

        function workspaceHasPendingWork() {
          const pendingTask = (state.workspace.data.tasks || []).some((item) => ["queued", "running", "retry_wait", "exporting", "processing"].includes(item.status));
          const selectedTaskPending = ["queued", "running", "retry_wait", "exporting", "processing"].includes(state.workspace.selectedTask?.status);
          const exportPending = ["queued", "exporting", "processing"].includes(state.workspace.lastLogExport?.status);
          return pendingTask || selectedTaskPending || exportPending;
        }

        function scheduleWorkspacePoll() {
          if (state.workspace.pollTimer) window.clearTimeout(state.workspace.pollTimer);
          state.workspace.pollTimer = null;
          if (state.workspace.pollInFlight || state.screen !== "formal" || !state.session.account || !state.workspace.loaded || !workspaceHasPendingWork()) return;
          state.workspace.pollTimer = window.setTimeout(() => {
            state.workspace.pollTimer = null;
            void pollWorkspace();
          }, 2000);
        }

        async function pollWorkspace() {
          if (state.workspace.pollInFlight || state.screen !== "formal" || !state.session.account) return;
          state.workspace.pollInFlight = true;
          try {
            await loadWorkspace(true);
          } finally {
            state.workspace.pollInFlight = false;
            scheduleWorkspacePoll();
          }
        }

        function setToast(message) {
          state.toast = message;
          render();
          window.clearTimeout(setToast.timer);
          setToast.timer = window.setTimeout(() => { state.toast = ""; render(); }, 3600);
        }

        async function runDemo(form) {
          const values = new FormData(form);
          state.demo.resumeTitle = String(values.get("resume_title") || "演示简历").trim();
          state.demo.resumeText = String(values.get("resume_text") || "").trim();
          state.demo.jobTitle = String(values.get("job_title") || "演示岗位").trim();
          state.demo.jobText = String(values.get("job_text") || "").trim();
          state.demo.running = true;
          state.demo.result = null;
          state.demo.error = "";
          state.demo.logs = [];
          state.demo.phase = "提交简历并生成解析草稿…";
          render();
          try {
            const resume = await request("/api/v1/demo/documents", { method: "POST", body: { document_type: "resume", title: state.demo.resumeTitle, text: state.demo.resumeText } });
            state.demo.logs.push("简历草稿已生成");
            state.demo.phase = "确认简历 version 1…";
            render();
            const resumeConfirmed = await request(`/api/v1/demo/documents/${encodeURIComponent(resume.id)}/confirm`, { method: "POST" });
            state.demo.logs.push("简历已确认并冻结");
            state.demo.phase = "提交岗位 JD 并生成解析草稿…";
            render();
            const job = await request("/api/v1/demo/documents", { method: "POST", body: { document_type: "job", title: state.demo.jobTitle, text: state.demo.jobText } });
            state.demo.logs.push("岗位草稿已生成");
            state.demo.phase = "确认岗位 version 1…";
            render();
            const jobConfirmed = await request(`/api/v1/demo/documents/${encodeURIComponent(job.id)}/confirm`, { method: "POST" });
            state.demo.logs.push("岗位已确认并冻结");
            state.demo.phase = "根据两个冻结版本计算匹配报告…";
            render();
            const analysis = await request("/api/v1/demo/matches", { method: "POST", body: { resume_version_id: resumeConfirmed.version.id, job_version_id: jobConfirmed.version.id } });
            state.demo.logs.push("报告已写入 201 PostgreSQL");
            state.demo.result = { resume: resumeConfirmed, job: jobConfirmed, analysis };
            state.demo.phase = "演示完成";
          } catch (error) {
            state.demo.error = error instanceof ApiError ? `${error.code}：${error.message}` : `演示失败：${error.message || error}`;
            state.demo.phase = "可重试";
          } finally {
            state.demo.running = false;
            render();
          }
        }

        async function openFormal() {
          state.screen = "formal";
          state.workspace.error = "";
          render();
          if (!state.session.token) return;
          try {
            const me = await request("/api/v1/me", { formal: true });
            state.session.account = me.account;
            saveSession();
            await loadWorkspace(true);
          } catch (error) {
            clearSession();
            state.auth.error = error instanceof ApiError ? error.message : "登录状态已失效，请重新登录";
            render();
          }
        }

        async function handleBrowserSync() {
          const params = new URLSearchParams(window.location.search);
          if (params.get("purslyx_browser_sync") !== "1") return;
          const platformOrigin = params.get("origin") || "";
          const nonce = params.get("nonce") || "";
          const allowedOrigins = ["https://www.zhipin.com", "https://zhipin.com", "https://www.liepin.com", "https://liepin.com"];
          window.history.replaceState({}, document.title, window.location.pathname);
          if (!allowedOrigins.includes(platformOrigin) || !/^[A-Za-z0-9_-]{32,128}$/.test(nonce) || !window.opener) {
            state.screen = "formal";
            state.auth.error = "浏览器同步请求无效，请从受支持的平台详情页重新发起。";
            render();
            return;
          }
          try {
            const data = await request("/api/v1/auth/browser-codes", { method: "POST", formal: true, body: { origin: platformOrigin, nonce } });
            window.opener.postMessage({ type: "PURSLYX_BROWSER_CODE", origin: platformOrigin, nonce, authorization_code: data.authorization_code, expires_at: data.expires_at }, platformOrigin);
            state.screen = "formal";
            state.auth.notice = "浏览器同步请求已发送，可以关闭此窗口。";
            render();
            window.setTimeout(() => window.close(), 450);
          } catch (error) {
            state.screen = "formal";
            state.auth.error = error instanceof ApiError ? `${error.code}：${error.message}` : "请先在此浏览器登录 Purslyx，再重试同步。";
            render();
          }
        }

        async function login(email, password) {
          const data = await request("/api/v1/auth/login", { method: "POST", body: { email, password } });
          state.session = { token: data.access_token, csrf: data.csrf_token, account: data.account };
          state.auth.running = false;
          saveSession();
          if (currentRouteIsAuth()) {
            // 登录页完成认证后进入与注册身份对应的独立工作台首页。
            window.location.assign(routeFor("dashboard"));
            return;
          }
          state.screen = "formal";
          state.workspace.page = "dashboard";
          state.workspace.loaded = false;
          state.auth.error = "";
          render();
          await loadWorkspace(true);
        }

        async function registerAndLogin(email, password, role) {
          const data = await request("/api/v1/auth/register", { method: "POST", body: { email, password, registration_role: role } });
          if (data.verification_token) await request("/api/v1/auth/verify-email", { method: "POST", body: { token: data.verification_token } });
          await login(email, password);
        }

        async function submitAuth(form) {
          const values = new FormData(form);
          const email = String(values.get("email") || state.auth.email || "").trim();
          const password = String(values.get("password") || state.auth.password || "");
          const registrationRole = String(values.get("registration_role") || state.auth.role || "seeker");
          state.auth.email = email;
          if (password) state.auth.password = password;
          if (["seeker", "recruiter"].includes(registrationRole)) state.auth.role = registrationRole;
          state.auth.running = true;
          state.auth.error = "";
          state.auth.notice = "";
          render();
          try {
            if (state.auth.mode === "register") {
              await registerAndLogin(email, password, state.auth.role);
            } else if (state.auth.mode === "login") {
              await login(email, password);
            } else if (state.auth.mode === "verify") {
              const token = String(values.get("token") || state.auth.token || "").trim();
              state.auth.token = token;
              await request("/api/v1/auth/verify-email", { method: "POST", body: { token } });
              if (state.auth.password) await login(email, state.auth.password);
              else { state.auth.mode = "login"; state.auth.notice = "邮箱已验证，请输入密码登录。"; }
            } else if (state.auth.mode === "forgot") {
              const data = await request("/api/v1/auth/forgot-password", { method: "POST", body: { email } });
              if (data.reset_token) {
                state.auth.token = data.reset_token;
                state.auth.mode = "reset";
                state.auth.notice = "本地演示已生成重置令牌，请确认后设置新密码。";
              } else state.auth.notice = data.message || "如果账号存在，重置说明会发送到注册邮箱。";
              state.auth.running = false;
              render();
            } else if (state.auth.mode === "reset") {
              const token = String(values.get("token") || state.auth.token || "").trim();
              const newPassword = String(values.get("new_password") || "");
              await request("/api/v1/auth/reset-password", { method: "POST", body: { token, new_password: newPassword } });
              state.auth.mode = "login";
              state.auth.token = "";
              state.auth.password = "";
              state.auth.notice = "密码已重置，请使用新密码登录。";
              state.auth.running = false;
              render();
            } else if (state.auth.mode === "recovery") {
              const data = await request("/api/v1/auth/request-account-recovery", { method: "POST", body: { email } });
              if (data.recovery_token) {
                state.auth.token = data.recovery_token;
                state.auth.mode = "recover";
                state.auth.notice = "本地演示已生成恢复令牌，请确认恢复账号。";
              } else state.auth.notice = data.message || "如果账号存在，恢复说明会发送到注册邮箱。";
              state.auth.running = false;
              render();
            } else if (state.auth.mode === "recover") {
              const token = String(values.get("token") || state.auth.token || "").trim();
              await request("/api/v1/auth/recover-account", { method: "POST", body: { token } });
              state.auth.mode = "login";
              state.auth.token = "";
              state.auth.notice = "账号已恢复，请重新登录。";
              state.auth.running = false;
              render();
            }
          } catch (error) {
            state.auth.error = error instanceof ApiError ? `${error.code}：${error.message}` : `请求失败：${error.message || error}`;
            if (error instanceof ApiError && error.code === "AUTH_EMAIL_UNVERIFIED") {
              state.auth.mode = "verify";
              state.auth.notice = "该账号尚未完成邮箱验证；本地演示可使用验证令牌继续。";
            }
            state.auth.running = false;
            render();
          }
        }

        async function resendVerification() {
          const email = state.auth.email.trim();
          if (!email) { state.auth.error = "请先填写邮箱"; render(); return; }
          state.auth.running = true;
          state.auth.error = "";
          state.auth.notice = "";
          render();
          try {
            const data = await request("/api/v1/auth/resend-verification", { method: "POST", body: { email } });
            state.auth.mode = "verify";
            state.auth.token = data.verification_token || state.auth.token;
            state.auth.notice = data.verification_token ? "本地演示已生成新的验证令牌。" : (data.message || "验证说明已发送，请查收邮箱。");
          } catch (error) {
            state.auth.error = error instanceof ApiError ? `${error.code}：${error.message}` : error.message || "重新发送失败";
          } finally {
            state.auth.running = false;
            render();
          }
        }

        async function loadAdminLogs(type = state.workspace.adminLogType) {
          const permission = { operations: "admin.logs.operations.read", security: "admin.logs.security.read", tasks: "admin.logs.tasks.read" }[type];
          if (!(state.session.account?.admin_permissions || []).includes(permission)) return;
          const params = adminLogQuery(type);
          const path = `/api/v1/admin/logs/${type}${params.toString() ? `?${params.toString()}` : ""}`;
          const result = await request(path, { formal: true });
          state.workspace.data.admin.logs[type] = result?.items || [];
          return result;
        }

        async function hydrateRouteSelection() {
          // 多页面之间只通过短 ID 传递当前对象，正文仍从受保护接口读取。
          const params = new URLSearchParams(window.location.search);
          const loaders = [];
          const analysisId = params.get("analysis_id");
          const poolId = params.get("pool_id");
          const variantId = params.get("variant_id");
          const interviewId = params.get("interview_id");
          const taskId = params.get("task_id");
          const rewriteId = params.get("rewrite_id");
          if (analysisId && ["report", "rewrite"].includes(state.workspace.page)) {
            loaders.push(request(`/api/v1/analyses/${encodeURIComponent(analysisId)}`, { formal: true }).then((value) => {
              state.workspace.selectedAnalysis = value;
              state.workspace.selectedPool = findPoolForAnalysis(analysisId);
              if (state.workspace.page === "rewrite") return resolveRewriteSource(value);
              return null;
            }));
          }
          if (poolId && state.workspace.page === "pool") {
            loaders.push(request(`/api/v1/job-pool/items/${encodeURIComponent(poolId)}`, { formal: true }).then((value) => { state.workspace.selectedPool = value; }));
          }
          if (variantId && state.workspace.page === "variants") {
            loaders.push(request(`/api/v1/resumes/${encodeURIComponent(variantId)}`, { formal: true }).then((value) => { state.workspace.selectedVariant = value; }));
          }
          if (interviewId && state.workspace.page === "interview") {
            loaders.push(request(`/api/v1/interviews/${encodeURIComponent(interviewId)}`, { formal: true }).then((value) => { state.workspace.selectedInterview = value; }));
          }
          if (taskId && state.workspace.page === "tasks") {
            loaders.push(request(`/api/v1/tasks/${encodeURIComponent(taskId)}`, { formal: true }).then((value) => { state.workspace.selectedTask = value; }));
          }
          if (rewriteId && state.workspace.page === "rewrite") {
            loaders.push(request(`/api/v1/rewrites/${encodeURIComponent(rewriteId)}`, { formal: true }).then((value) => { state.workspace.selectedRewrite = value; }));
          }
          if (loaders.length) await Promise.all(loaders);
        }

        async function loadWorkspace(force = false) {
          if (!state.session.account || (!force && state.workspace.loaded)) return;
          const requestNo = ++state.workspace.requestNo;
          state.workspace.loading = true;
          state.workspace.error = "";
          render();
          const seeker = state.session.account.registration_role === "seeker";
          const queryDraftId = new URLSearchParams(window.location.search).get("browser_draft_id");
          const failures = [];
          if (seeker && queryDraftId && (!state.workspace.browserDraft || state.workspace.browserDraft.id !== queryDraftId)) {
            try {
              state.workspace.browserDraft = await request(`/api/v1/browser/job-drafts/${encodeURIComponent(queryDraftId)}/web`, { formal: true });
              state.workspace.browserDraftError = "";
            } catch (error) {
              state.workspace.browserDraft = null;
              state.workspace.browserDraftError = error instanceof ApiError ? `${error.code}：${error.message}` : "浏览器岗位草稿读取失败";
              failures.push(state.workspace.browserDraftError);
            }
          }
          const jobs = [
            ["documents", "/api/v1/documents"],
            ["analyses", "/api/v1/analyses"],
            ["tasks", "/api/v1/tasks"],
            ["usage", "/api/v1/usage"],
            ["stats", personalStatsPath()],
          ];
          if (seeker) jobs.push(["preferences", "/api/v1/preferences"], ["facts", "/api/v1/facts"], ["pool", "/api/v1/job-pool/items"], ["interviews", "/api/v1/interviews"], ["variants", "/api/v1/resumes"]);
          else state.workspace.data.preferences = [];
          const permissions = new Set(state.session.account.admin_permissions || []);
          if (permissions.has("admin.stats.read")) {
            const metricParams = adminMetricQuery();
            const metricQuery = metricParams.toString() ? `?${metricParams.toString()}` : "";
            jobs.push(["admin_metrics", `/api/v1/admin/metrics${metricQuery}`], ["admin_costs", `/api/v1/admin/costs${metricQuery}`], ["admin_feedback", "/api/v1/admin/feedback"]);
          }
          if (permissions.has("admin.users.read")) jobs.push(["admin_users", "/api/v1/admin/users"]);
          if (permissions.has("admin.roles.manage")) jobs.push(["admin_roles", "/api/v1/admin/roles"]);
          if (permissions.has("admin.usage.grant")) jobs.push(["admin_grants", "/api/v1/admin/usage-grants"]);
          if (permissions.has("admin.logs.operations.read")) { const params = adminLogQuery("operations"); jobs.push(["admin_log_operations", `/api/v1/admin/logs/operations${params.toString() ? `?${params.toString()}` : ""}`]); }
          if (permissions.has("admin.logs.security.read")) { const params = adminLogQuery("security"); jobs.push(["admin_log_security", `/api/v1/admin/logs/security${params.toString() ? `?${params.toString()}` : ""}`]); }
          if (permissions.has("admin.logs.tasks.read")) { const params = adminLogQuery("tasks"); jobs.push(["admin_log_tasks", `/api/v1/admin/logs/tasks${params.toString() ? `?${params.toString()}` : ""}`]); }
          if (state.workspace.lastLogExport && permissions.has("admin.logs.export")) jobs.push(["admin_log_export", `/api/v1/admin/log-exports/${encodeURIComponent(state.workspace.lastLogExport.id)}`]);
          const results = await Promise.all(jobs.map(async ([name, path]) => {
            try { return { name, value: await request(path, { formal: true }) }; }
            catch (error) { return { name, error }; }
          }));
          if (requestNo !== state.workspace.requestNo) return;
          for (const item of results) {
            if (item.error) { failures.push(item.error instanceof ApiError ? item.error.message : "部分数据读取失败"); continue; }
            if (item.name === "usage" || item.name === "stats") state.workspace.data[item.name] = item.value;
            else if (item.name.startsWith("admin_")) {
              const admin = state.workspace.data.admin;
              if (item.name === "admin_metrics" || item.name === "admin_costs") admin[item.name.slice(6)] = item.value;
              else if (item.name === "admin_log_operations" || item.name === "admin_log_security" || item.name === "admin_log_tasks") admin.logs[item.name.slice(10)] = item.value?.items || [];
              else if (item.name === "admin_log_export") state.workspace.lastLogExport = item.value;
              else admin[item.name.slice(6)] = item.value?.items || [];
            } else state.workspace.data[item.name] = item.value?.items || [];
          }
          if (!seeker) {
            const candidate = state.workspace.data.documents.find((item) => item.document_type === "resume" && item.subject_type === "candidate_resume" && item.latest_version);
            if (candidate) {
              try {
                const preferenceResult = await request(`/api/v1/preferences?subject_document_id=${encodeURIComponent(candidate.id)}`, { formal: true });
                state.workspace.data.preferences = preferenceResult?.items || [];
              } catch (error) {
                failures.push(error instanceof ApiError ? error.message : "候选人岗位期望读取失败");
              }
            }
          }
          try {
            await hydrateRouteSelection();
          } catch (error) {
            failures.push(error instanceof ApiError ? error.message : "页面对象读取失败");
          }
          state.workspace.loaded = true;
          state.workspace.loading = false;
          if (failures.length) state.workspace.error = "工作台已打开，但有部分模块暂未读取完成；点击刷新可重试。";
          render();
        }

        async function refreshWorkspace() {
          await loadWorkspace(true);
        }

        async function createFormalDocument(form) {
          const values = new FormData(form);
          const seeker = state.session.account.registration_role === "seeker";
          const type = String(values.get("document_type") || "resume");
          const subject = type === "resume" ? (seeker ? "self_resume" : "candidate_resume") : "job_description";
          const title = String(values.get("title") || "未命名资料").trim();
          const textValue = String(values.get("text") || "").trim();
          const file = form.querySelector("input[type=file]")?.files?.[0];
          const documentOptions = { method: "POST", formal: true, idempotencyKey: key("document") };
          if (file) {
            const formData = new FormData();
            formData.append("document_type", type);
            formData.append("subject_type", subject);
            formData.append("title", title);
            formData.append("file", file, file.name);
            documentOptions.formData = formData;
          } else {
            documentOptions.body = { document_type: type, subject_type: subject, title, text: textValue };
          }
          const created = await request("/api/v1/documents", documentOptions);
          const detail = await request(`/api/v1/documents/${encodeURIComponent(created.id)}`, { formal: true });
          state.workspace.pendingDocumentDraft = detail;
          state.workspace.page = "resume";
          return { document: created, draft: detail };
        }

        async function confirmDocumentDraft(form) {
          const values = new FormData(form);
          const documentId = String(values.get("document_id") || "").trim();
          const draftId = String(values.get("draft_id") || "").trim();
          const baseRevision = Number(values.get("base_revision"));
          const title = String(values.get("title") || "").trim();
          let content;
          try {
            content = JSON.parse(String(values.get("draft_json") || ""));
          } catch (_) {
            throw new Error("结构化内容不是有效 JSON，请检查后再确认");
          }
          if (!documentId || !draftId || !Number.isFinite(baseRevision)) throw new Error("草稿信息不完整，请刷新后重试");
          const version = await request(`/api/v1/documents/${encodeURIComponent(documentId)}/versions`, { method: "POST", formal: true, idempotencyKey: key("document-version"), body: { draft_id: draftId, base_revision: baseRevision, content, title } });
          state.workspace.pendingDocumentDraft = null;
          // 确认后先进入恢复态，避免用户在旧列表尚未刷新时连续提交下一份资料。
          state.workspace.loaded = false;
          setToast(`资料已确认，已生成 version ${version.version_no}`);
          await loadWorkspace(true);
        }

        async function discardDocumentDraft(id) {
          const deleted = await deleteResource(`/api/v1/documents/${encodeURIComponent(id)}`, "资料草稿");
          if (!deleted) return;
          state.workspace.pendingDocumentDraft = null;
          setToast("资料草稿已放弃");
          await loadWorkspace(true);
        }

        function preferenceContentFromForm(values) {
          const fieldStatus = (name, fallback = "unknown") => String(values.get(`${name}_status`) || fallback);
          const strength = String(values.get("condition_strength") || "prefer");
          const titleStatus = fieldStatus("job_title", "specified");
          const locationStatus = fieldStatus("location", "specified");
          const modeStatus = fieldStatus("work_mode", "specified");
          const salaryStatus = fieldStatus("salary", "specified");
          const title = String(values.get("job_title") || "").trim();
          const locations = String(values.get("location") || "").split(/[、,，/／|]/).map((item) => item.trim()).filter(Boolean);
          const mode = String(values.get("work_mode") || "onsite");
          if (titleStatus === "specified" && !title) throw new Error("请填写期望岗位，或将岗位状态改为未知/不限");
          if (locationStatus === "specified" && !locations.length) throw new Error("请填写期望地点，或将地点状态改为未知/不限");
          const minK = Number(values.get("min_salary"));
          const maxK = Number(values.get("max_salary"));
          if (salaryStatus === "specified" && (!Number.isFinite(minK) || !Number.isFinite(maxK) || minK < 0 || maxK < minK)) throw new Error("请检查薪资上下限，或选择未知/不限/面议");
          return {
            job_title: { status: titleStatus, value: titleStatus === "specified" ? title : null, strength },
            locations: { status: locationStatus, values: locationStatus === "specified" ? locations : [], strength },
            work_mode: { status: modeStatus, value: modeStatus === "specified" ? mode : null, strength },
            salary: { status: salaryStatus, min: salaryStatus === "specified" ? minK * 1000 : null, max: salaryStatus === "specified" ? maxK * 1000 : null, currency: "CNY", period: "monthly", tax_basis: "pre_tax", salary_months: 12, strength },
          };
        }

        async function savePreference(form) {
          const values = new FormData(form);
          const content = preferenceContentFromForm(values);
          const subjectId = String(values.get("subject_document_id") || "").trim();
          const preferenceId = String(values.get("preference_id") || "").trim();
          const displayTitle = content.job_title.value || "未指定岗位";
          const displayLocation = content.locations.values.length ? content.locations.values.join("、") : labelStatus(content.locations.status);
          const payload = { display_name: `${displayTitle} · ${displayLocation}`, context: subjectId ? "candidate" : "self", ...(subjectId ? { subject_document_id: subjectId } : {}), is_default: values.get("is_default") === "on", preference: content };
          let data;
          if (preferenceId) {
            payload.base_revision = Number(values.get("base_revision"));
            payload.status = "active";
            data = await request(`/api/v1/preferences/${encodeURIComponent(preferenceId)}`, { method: "PUT", formal: true, idempotencyKey: key("preference-update"), body: payload });
          } else {
            data = await request("/api/v1/preferences", { method: "POST", formal: true, idempotencyKey: key("preference"), body: payload });
          }
          state.workspace.recruiterSubjectId = subjectId || state.workspace.recruiterSubjectId;
          state.workspace.data.preferences = [...state.workspace.data.preferences.filter((item) => item.id !== data.id), data];
          state.workspace.selectedPreference = null;
        }

        async function setDefaultPreference(id) {
          const item = state.workspace.data.preferences.find((value) => value.id === id);
          if (!item?.version) throw new Error("岗位期望版本不可用，请刷新后重试");
          const payload = {
            display_name: item.display_name,
            context: item.subject_document_id ? "candidate" : "self",
            ...(item.subject_document_id ? { subject_document_id: item.subject_document_id } : {}),
            is_default: true,
            status: "active",
            base_revision: item.revision,
            preference: item.version.content,
          };
          const updated = await request(`/api/v1/preferences/${encodeURIComponent(id)}`, { method: "PUT", formal: true, idempotencyKey: key("preference-default"), body: payload });
          state.workspace.data.preferences = [...state.workspace.data.preferences.filter((value) => value.id !== id), updated];
          setToast("已设为默认岗位期望");
          await loadWorkspace(true);
        }

        async function deletePreference(id) {
          const item = state.workspace.data.preferences.find((value) => value.id === id);
          if (!item) throw new Error("岗位期望不存在，请刷新后重试");
          if (!window.confirm(`确认删除岗位期望“${item.display_name}”？\n\n已保存的历史分析仍保留本次快照，后续新分析不能再选择它。`)) return;
          await request(`/api/v1/preferences/${encodeURIComponent(id)}`, { method: "DELETE", formal: true, idempotencyKey: key("preference-delete") });
          state.workspace.selectedPreference = null;
          setToast("岗位期望已删除");
          await loadWorkspace(true);
        }

        async function saveFact(form) {
          const source = state.workspace.rewriteSource;
          if (!source?.document?.id || !source?.version?.id) throw new Error("当前简历版本不可用，请刷新报告后重试");
          const values = new FormData(form);
          const data = await request("/api/v1/facts", { method: "POST", formal: true, idempotencyKey: key("fact"), body: { document_id: source.document.id, source_document_version_id: source.version.id, source_segment_key: String(values.get("source_segment_key") || ""), fact_category: String(values.get("fact_category") || "事实").trim(), fact_text: String(values.get("fact_text") || "").trim(), source_type: "user_added" } });
          state.workspace.data.facts = [data, ...state.workspace.data.facts.filter((item) => item.id !== data.id)];
        }

        async function createRewrite(form) {
          const analysis = state.workspace.selectedAnalysis;
          const source = state.workspace.rewriteSource;
          const segmentKeys = [...document.querySelectorAll("input[data-rewrite-segment]:checked")].map((node) => node.dataset.rewriteSegment).filter(Boolean);
          state.workspace.rewriteSegmentKeys = segmentKeys;
          if (!analysis || !source?.version?.id || !segmentKeys.length) throw new Error("至少选择一个简历段落");
          const factVersionIds = [...form.querySelectorAll("input[data-rewrite-fact]:checked")].map((node) => node.value).filter(Boolean);
          const data = await request("/api/v1/rewrites", { method: "POST", formal: true, idempotencyKey: key("rewrite"), body: { analysis_id: analysis.id, source_resume_version_id: source.version.id, segment_keys: segmentKeys, fact_version_ids: factVersionIds, confirm_usage: true } });
          state.workspace.selectedRewrite = data.rewrite;
        }

        async function savePool(form) {
          const values = new FormData(form);
          const resumeVersionId = String(values.get("resume_version_id") || "");
          const preferenceVersionId = String(values.get("preference_version_id") || "");
          const startNow = values.get("start_now") === "on";
          if (startNow && (!resumeVersionId || !preferenceVersionId)) throw new Error("请先确认简历版本和岗位期望，再开始分析");
          const job = await createFormalDocumentFromPool(values);
          const payload = { source: { type: "document_version", job_document_version_id: job.version.id }, preference_version_id: preferenceVersionId || null, analysis: startNow ? { start_now: true, resume_document_version_id: resumeVersionId, confirm_usage: true } : {} };
          const data = await request("/api/v1/job-pool/items", { method: "POST", formal: true, idempotencyKey: key("pool"), body: payload });
          if (data.analysis) state.workspace.selectedAnalysis = data.analysis;
          state.workspace.selectedPool = data.job_pool_item || data;
        }

        async function saveBrowserDraftPool(form) {
          const values = new FormData(form);
          const draftId = String(values.get("browser_draft_id") || state.workspace.browserDraft?.id || "").trim();
          const resumeVersionId = String(values.get("resume_version_id") || "").trim();
          const preferenceVersionId = String(values.get("preference_version_id") || "").trim();
          const startNow = values.get("start_now") === "on";
          if (!draftId) throw new Error("浏览器岗位草稿不存在，请刷新后重试");
          if (startNow && (!resumeVersionId || !preferenceVersionId)) throw new Error("立即分析需要先选择简历版本和岗位期望版本");
          const data = await request("/api/v1/job-pool/items", { method: "POST", formal: true, idempotencyKey: key("browser-pool"), body: { source: { type: "browser_draft", browser_draft_id: draftId }, preference_version_id: preferenceVersionId || null, analysis: startNow ? { start_now: true, resume_document_version_id: resumeVersionId, confirm_usage: true } : {} } });
          state.workspace.browserDraft = null;
          state.workspace.browserDraftError = "";
          replaceBrowserDraftUrl();
          if (data.analysis) state.workspace.selectedAnalysis = data.analysis;
          state.workspace.selectedPool = data.job_pool_item || data;
          state.workspace.page = "pool";
          setToast(startNow ? "浏览器岗位已确认入池，分析已开始" : "浏览器岗位已确认入池，可稍后开始分析");
        }

        async function analyzePoolItem(form) {
          const values = new FormData(form);
          const selected = state.workspace.selectedPool;
          if (!selected) throw new Error("没有选中的岗位，请刷新后重试");
          const resumeVersionId = String(values.get("resume_version_id") || "").trim();
          const preferenceVersionId = String(values.get("preference_version_id") || "").trim();
          if (!resumeVersionId || !preferenceVersionId) throw new Error("开始分析需要选择简历和岗位期望版本");
          const data = await request(`/api/v1/job-pool/items/${encodeURIComponent(selected.id)}/analyze`, { method: "POST", formal: true, idempotencyKey: key("pool-analysis"), body: { resume_document_version_id: resumeVersionId, preference_version_id: preferenceVersionId, base_revision: Number(selected.revision || 1), confirm_usage: values.get("confirm_usage") === "on" } });
          state.workspace.selectedPool = data.job_pool_item || selected;
          state.workspace.selectedAnalysis = data.analysis || null;
          setToast("分析已开始，任务状态可在任务中心查看");
          await loadWorkspace(true);
        }

        function replaceBrowserDraftUrl() {
          const url = new URL(window.location.href);
          url.searchParams.delete("browser_draft_id");
          window.history.replaceState({}, document.title, `${url.pathname}${url.search}${url.hash}`);
        }

        async function createFormalDocumentFromPool(values) {
          const title = String(values.get("job_title") || "未命名岗位").trim();
          const textValue = String(values.get("job_text") || "").trim();
          return createDocumentAndConfirm({ document_type: "job_description", subject_type: "job_description", title, text: textValue });
        }

        async function createDocumentAndConfirm(payload) {
          const created = await request("/api/v1/documents", { method: "POST", formal: true, idempotencyKey: key("document"), body: payload });
          const detail = await request(`/api/v1/documents/${encodeURIComponent(created.id)}`, { formal: true });
          const version = await request(`/api/v1/documents/${encodeURIComponent(created.id)}/versions`, { method: "POST", formal: true, idempotencyKey: key("document-version"), body: { draft_id: detail.latest_draft.id, base_revision: detail.revision, content: detail.draft_content } });
          return { document: created, version };
        }

        async function createRecruiterAnalysis(form) {
          const values = new FormData(form);
          const preferenceVersionId = String(values.get("preference_version_id") || "").trim();
          const data = await request("/api/v1/analyses", { method: "POST", formal: true, idempotencyKey: key("analysis"), body: { context_type: "recruiter_single", resume_document_version_id: String(values.get("resume_version_id")), job_document_version_id: String(values.get("job_version_id")), ...(preferenceVersionId ? { preference_version_id: preferenceVersionId } : {}), confirm_usage: true } });
          if (data.analysis) {
            // 单人分析完成后进入可复制、可刷新恢复的独立报告 URL。
            state.workspace.selectedAnalysis = data.analysis;
            window.location.assign(routeFor("report", { analysis_id: data.analysis.id }));
          }
        }

        async function openPool(id) {
          state.workspace.loading = true;
          render();
          try {
            state.workspace.selectedPool = await request(`/api/v1/job-pool/items/${encodeURIComponent(id)}`, { formal: true });
            state.workspace.page = "pool";
          } catch (error) {
            state.workspace.error = error instanceof ApiError ? error.message : "岗位读取失败";
          } finally {
            state.workspace.loading = false;
            render();
          }
        }

        async function openDocumentDraft(id) {
          state.workspace.loading = true;
          state.workspace.error = "";
          render();
          try {
            state.workspace.pendingDocumentDraft = await request(`/api/v1/documents/${encodeURIComponent(id)}`, { formal: true });
            state.workspace.page = "resume";
          } catch (error) {
            state.workspace.error = error instanceof ApiError ? `${error.code}：${error.message}` : error.message || "资料草稿读取失败";
          } finally {
            state.workspace.loading = false;
            render();
          }
        }

        async function openAnalysis(id) {
          state.workspace.loading = true;
          render();
          try {
            state.workspace.selectedAnalysis = await request(`/api/v1/analyses/${encodeURIComponent(id)}`, { formal: true });
            state.workspace.selectedPool = findPoolForAnalysis(id);
            state.workspace.page = "report";
          } catch (error) {
            state.workspace.error = error instanceof ApiError ? error.message : "报告读取失败";
          } finally {
            state.workspace.loading = false;
            render();
          }
        }

        async function openRewrite() {
          const analysis = state.workspace.selectedAnalysis;
          if (!analysis) throw new Error("当前报告没有可读取的简历版本");
          state.workspace.loading = true;
          state.workspace.error = "";
          render();
          try {
            await resolveRewriteSource(analysis);
            state.workspace.rewriteSegmentKeys = [];
            state.workspace.selectedRewrite = null;
            state.workspace.page = "rewrite";
          } catch (error) {
            state.workspace.error = error instanceof ApiError ? error.message : error.message || "改写入口读取失败";
          } finally {
            state.workspace.loading = false;
            render();
          }
        }

        async function resolveRewriteSource(analysis) {
          const resumeRef = analysis?.input_versions?.find((item) => item && item.type === "resume");
          const candidates = state.workspace.data.documents.filter((item) => item.document_type === "resume");
          if (!analysis || !resumeRef || !candidates.length) throw new Error("当前报告没有可读取的简历版本");
          let document = candidates.find((item) => item.latest_version?.id === resumeRef.id) || null;
          let detail = null;
          for (const candidate of document ? [document] : candidates) {
            try {
              const candidateDetail = await request(`/api/v1/documents/${encodeURIComponent(candidate.id)}?version_id=${encodeURIComponent(resumeRef.id)}`, { formal: true });
              if (candidateDetail.version?.id === resumeRef.id) { document = candidate; detail = candidateDetail; break; }
            } catch (error) {
              if (!(error instanceof ApiError) || error.status !== 404) throw error;
            }
          }
          if (!document || !detail?.version) throw new Error("当前报告引用的简历版本已不可用，请刷新后重试");
          state.workspace.rewriteSource = { document, version: detail.version };
          return state.workspace.rewriteSource;
        }

        async function openTask(id) {
          state.workspace.loading = true;
          state.workspace.error = "";
          render();
          try {
            const sameTask = state.workspace.selectedTask?.id === id;
            const result = await requestDetailed(`/api/v1/tasks/${encodeURIComponent(id)}`, { formal: true, headers: sameTask && state.workspace.selectedTaskEtag ? { "If-None-Match": state.workspace.selectedTaskEtag } : {} });
            if (result.response.status !== 304) {
              state.workspace.selectedTask = result.data;
              state.workspace.selectedTaskEtag = result.response.headers.get("ETag") || "";
            }
            state.workspace.page = "tasks";
          } catch (error) {
            state.workspace.error = error instanceof ApiError ? error.message : error.message || "任务读取失败";
          } finally {
            state.workspace.loading = false;
            render();
          }
        }

        async function retryTask(id) {
          const data = await request(`/api/v1/tasks/${encodeURIComponent(id)}/retry`, { method: "POST", formal: true, idempotencyKey: key("task-retry"), body: { reason: "演示现场重试" } });
          state.workspace.selectedTask = data.task;
          state.workspace.selectedTaskEtag = "";
          state.workspace.page = "tasks";
          await loadWorkspace(true);
        }

        async function deleteResource(path, label) {
          const impactResult = await requestDetailed(`${path}/deletion-impact`, { formal: true });
          const impact = impactResult.data || {};
          const affected = Object.entries(impact.affected || {}).map(([name, count]) => `${name}: ${count}`).join("，") || "无关联记录";
          const confirmed = window.confirm(`确认删除${label}？\n\n删除影响快照：${affected}\n\n删除后旧入口将不可读取。`);
          if (!confirmed) return false;
          const etag = impactResult.response.headers.get("ETag");
          if (!etag) throw new Error("删除影响快照缺少 ETag，请刷新后重试");
          await requestDetailed(path, { method: "DELETE", formal: true, headers: { "If-Match": etag } });
          return true;
        }

        async function goToApply(id, clickToken) {
          if (!clickToken) throw new Error("去投递令牌已失效，请刷新岗位详情");
          const targetUrl = state.workspace.selectedPool?.id === id ? state.workspace.selectedPool.source_url : "";
          if (!targetUrl) throw new Error("岗位原始链接不可用，请刷新岗位详情");
          const tab = window.open("about:blank", "_blank");
          try {
            const response = await fetch(`/api/v1/job-pool/items/${encodeURIComponent(id)}/go-to-apply`, { ...requestInit({ method: "POST", formal: true, idempotencyKey: key("apply"), body: { click_token: clickToken } }), redirect: "manual" });
            if (response.status !== 303 && response.type !== "opaqueredirect") {
              let payload = null;
              try { payload = await response.json(); } catch (_) {}
              const error = payload?.error || {};
              throw new ApiError(response.status, error.code || "APPLY_FAILED", error.message || "去投递失败", error.action);
            }
            if (tab && !tab.closed) tab.location.href = targetUrl;
            else window.location.assign(targetUrl);
          } catch (error) {
            if (tab && !tab.closed) tab.close();
            throw error;
          }
        }

        async function decideRewrite(actionNode) {
          const rewriteId = actionNode.dataset.rewriteId;
          const segmentId = actionNode.dataset.segmentId;
          const decision = actionNode.dataset.decision;
          const payload = { decision, base_decision_no: Number(actionNode.dataset.baseDecisionNo || 0) };
          if (decision === "edited") payload.edited_text = String(document.getElementById(actionNode.dataset.editId)?.value || "").trim();
          const data = await request(`/api/v1/rewrites/${encodeURIComponent(rewriteId)}/segments/${encodeURIComponent(segmentId)}/decisions`, { method: "POST", formal: true, idempotencyKey: key("rewrite-decision"), body: payload });
          state.workspace.selectedRewrite = data.rewrite;
          setToast("改写决定已保存");
        }

        async function createVariantFromRewrite() {
          const rewrite = state.workspace.selectedRewrite;
          if (!rewrite || rewrite.status !== "available") throw new Error("改写结果尚未完成");
          const variant = await createVariant(rewrite.id);
          window.location.assign(routeFor("variants", { variant_id: variant.id }));
        }

        async function createVariant(rewriteId = null) {
          const analysis = state.workspace.selectedAnalysis;
          const pool = state.workspace.selectedPool || findPoolForAnalysis(analysis?.id);
          const resumeId = analysis?.input_versions?.find((item) => item && item.type === "resume")?.id || pool?.resume_version_id;
          if (!analysis || !pool || !resumeId) throw new Error("当前报告缺少可用的岗位或简历版本");
          const data = await request("/api/v1/resumes", { method: "POST", formal: true, idempotencyKey: key("variant"), body: { job_pool_item_id: pool.id, source_resume_version_id: resumeId, title: `${pool.job_title || "岗位"} · 岗位版简历`, ...(rewriteId ? { rewrite_id: rewriteId } : {}) } });
          state.workspace.selectedVariant = data;
          state.workspace.page = "variants";
          return data;
        }

        async function exportVariant(versionId) {
          const data = await request("/api/v1/exports", { method: "POST", formal: true, idempotencyKey: key("export"), body: { resume_variant_version_id: versionId } });
          state.workspace.lastExport = { ...(data.export || {}), id: data.export?.id, version_id: versionId };
        }

        async function openVariant(id) {
          state.workspace.loading = true;
          state.workspace.error = "";
          render();
          try {
            state.workspace.selectedVariant = await request(`/api/v1/resumes/${encodeURIComponent(id)}`, { formal: true });
            state.workspace.page = "variants";
          } catch (error) {
            state.workspace.error = error instanceof ApiError ? `${error.code}：${error.message}` : error.message || "岗位版读取失败";
          } finally {
            state.workspace.loading = false;
            render();
          }
        }

        async function saveVariantVersion(form) {
          const item = state.workspace.selectedVariant;
          const version = item?.versions?.[0];
          if (!item || !version) throw new Error("没有选中的岗位版简历");
          const content = JSON.parse(JSON.stringify(version.content));
          const sectionNodes = [...form.querySelectorAll("[data-variant-section]")];
          const sectionsByKey = new Map((content.sections || []).map((section) => [String(section.section_key), section]));
          sectionNodes.forEach((sectionNode, position) => {
            const section = sectionsByKey.get(String(sectionNode.dataset.variantSection));
            if (!section) return;
            section.position = position + 1;
            [...sectionNode.querySelectorAll("[data-variant-segment]")].forEach((field) => {
              const segment = (section.segments || []).find((value) => String(value.segment_key) === String(field.dataset.variantSegment));
              if (segment) segment.text = field.value.trim();
            });
          });
          content.sections = sectionNodes.map((node) => sectionsByKey.get(String(node.dataset.variantSection))).filter(Boolean);
          const values = new FormData(form);
          const layout = { ...version.layout, schema_version: "resume-layout-v1", section_order: sectionNodes.map((node) => String(node.dataset.variantSection)), font_family: "noto_sans_sc", font_size_pt: Number(values.get("font_size_pt")), line_height: Number(values.get("line_height")), section_spacing_pt: Number(values.get("section_spacing_pt")), bold_segment_keys: [...form.querySelectorAll("[data-variant-bold]:checked")].map((node) => String(node.dataset.variantBold)) };
          if (![layout.font_size_pt, layout.line_height, layout.section_spacing_pt].every(Number.isFinite)) throw new Error("请检查排版参数");
          const data = await request(`/api/v1/resumes/${encodeURIComponent(item.id)}/versions`, { method: "POST", formal: true, idempotencyKey: key("variant-version"), body: { base_revision: item.revision, content, layout, template_version: "resume-template-v1" } });
          state.workspace.selectedVariant = data;
          state.workspace.data.variants = [...state.workspace.data.variants.filter((value) => value.id !== data.id), data];
          setToast("岗位版新版本已保存");
        }

        function renumberVariantSections(container) {
          [...container.querySelectorAll(":scope > [data-variant-section]")].forEach((node, index, nodes) => {
            node.querySelectorAll("[data-action=move-variant-section]").forEach((button) => {
              button.dataset.index = String(index);
              button.disabled = button.dataset.direction === "up" ? index === 0 : index === nodes.length - 1;
            });
          });
        }

        function moveVariantSection(actionNode) {
          const container = document.querySelector("[data-variant-sections]");
          const current = actionNode.closest("[data-variant-section]");
          if (!container || !current) return;
          const sibling = actionNode.dataset.direction === "up" ? current.previousElementSibling : current.nextElementSibling;
          if (!sibling) return;
          if (actionNode.dataset.direction === "up") container.insertBefore(current, sibling);
          else container.insertBefore(sibling, current);
          renumberVariantSections(container);
        }

        async function deleteVariant(id) {
          const deleted = await deleteResource(`/api/v1/resumes/${encodeURIComponent(id)}`, "岗位版简历");
          if (!deleted) return;
          state.workspace.selectedVariant = null;
          setToast("岗位版简历已删除");
          await loadWorkspace(true);
        }

        async function startInterview() {
          const analysis = state.workspace.selectedAnalysis;
          const pool = state.workspace.selectedPool || findPoolForAnalysis(analysis?.id);
          const resumeId = analysis?.input_versions?.find((item) => item && item.type === "resume")?.id || pool?.resume_version_id;
          if (!analysis || !pool || !resumeId) throw new Error("当前报告缺少面试所需的岗位或简历版本");
          const data = await request("/api/v1/interviews", { method: "POST", formal: true, idempotencyKey: key("interview"), body: { job_pool_item_id: pool.id, analysis_id: analysis.id, resume_document_version_id: resumeId, title: `${pool.job_title || "岗位"} · 面试练习`, confirm_usage: true } });
          state.workspace.selectedInterview = data.interview;
          state.workspace.page = "interview";
          return data.interview;
        }

        async function openInterview(id) {
          try {
            state.workspace.selectedInterview = await request(`/api/v1/interviews/${encodeURIComponent(id)}`, { formal: true });
            state.workspace.page = "interview";
            render();
          } catch (error) {
            state.workspace.error = error instanceof ApiError ? error.message : "面试会话读取失败";
            render();
          }
        }

        async function answerInterview(form) {
          const values = new FormData(form);
          const item = state.workspace.selectedInterview;
          const data = await request(`/api/v1/interviews/${encodeURIComponent(item.id)}/answers`, { method: "POST", formal: true, idempotencyKey: key("answer"), body: { question_id: String(values.get("question_id")), answer_text: String(values.get("answer_text") || "").trim(), base_revision: item.revision } });
          state.workspace.selectedInterview = data.interview;
        }

        async function finishInterview(id) {
          const item = state.workspace.selectedInterview;
          const data = await request(`/api/v1/interviews/${encodeURIComponent(id)}/finish`, { method: "POST", formal: true, idempotencyKey: key("finish"), body: { base_revision: item.revision } });
          state.workspace.selectedInterview = data;
        }

        async function deleteInterview(id) {
          const deleted = await deleteResource(`/api/v1/interviews/${encodeURIComponent(id)}`, "面试练习");
          if (!deleted) return;
          state.workspace.selectedInterview = null;
          setToast("面试练习已删除");
          await loadWorkspace(true);
        }

        async function sendFeedback(form) {
          const values = new FormData(form);
          const type = { experience: "suggestion", accuracy: "issue", workflow: "suggestion" }[String(values.get("feedback_type"))] || "other";
          await request("/api/v1/feedback", { method: "POST", formal: true, idempotencyKey: key("feedback"), body: { feedback_type: type, content: String(values.get("content") || "").trim(), rating: Number(values.get("rating")) } });
        }

        async function searchAdminUsers(form) {
          const values = new FormData(form);
          const params = new URLSearchParams();
          const search = String(values.get("search") || "").trim();
          const registrationRole = String(values.get("registration_role") || "");
          const status = String(values.get("status") || "");
          if (search) params.set("search", search);
          if (registrationRole) params.set("registration_role", registrationRole);
          if (status) params.set("status", status);
          const result = await request(`/api/v1/admin/users${params.toString() ? `?${params.toString()}` : ""}`, { formal: true });
          state.workspace.data.admin.users = result?.items || [];
          state.workspace.error = "";
          render();
        }

        async function openAdminUser(id) {
          state.workspace.loading = true;
          state.workspace.error = "";
          render();
          try {
            state.workspace.selectedAdminUser = await request(`/api/v1/admin/users/${encodeURIComponent(id)}`, { formal: true });
            state.workspace.page = "admin-users";
          } finally {
            state.workspace.loading = false;
            render();
          }
        }

        async function saveAdminUserRoles(form) {
          const selected = state.workspace.selectedAdminUser;
          if (!selected?.account) throw new Error("没有选中的用户");
          const values = new FormData(form);
          const roleIds = values.getAll("role_ids").map((item) => String(item));
          const data = await request(`/api/v1/admin/users/${encodeURIComponent(selected.account.id)}/roles`, { method: "PUT", formal: true, idempotencyKey: key("admin-user-roles"), body: { role_ids: roleIds, base_revision: selected.account.revision } });
          selected.account = data.account;
          selected.admin_roles = (state.workspace.data.admin.roles || []).filter((role) => roleIds.includes(role.id));
          setToast("账号后台角色已更新");
          await loadWorkspace(true);
        }

        async function toggleUserStatus(id, currentStatus) {
          const detail = await request(`/api/v1/admin/users/${encodeURIComponent(id)}`, { formal: true });
          const account = detail.account || {};
          const nextStatus = currentStatus === "suspended" ? "active" : "suspended";
          if (!window.confirm(`确认将该账号${nextStatus === "suspended" ? "暂停" : "恢复"}？`)) return;
          await request(`/api/v1/admin/users/${encodeURIComponent(id)}/status`, { method: "PUT", formal: true, idempotencyKey: key("admin-user-status"), body: { status: nextStatus, reason: nextStatus === "suspended" ? "演示管理员操作" : "演示管理员恢复", base_revision: account.revision } });
          setToast(nextStatus === "suspended" ? "账号已暂停" : "账号已恢复");
          await loadWorkspace(true);
        }

        async function saveAdminRole(form) {
          const values = new FormData(form);
          const roleId = String(values.get("role_id") || "").trim();
          const payload = {
            name: String(values.get("name") || "").trim(),
            description: String(values.get("description") || "").trim(),
            status: String(values.get("status") || "active"),
            permission_keys: values.getAll("permission_keys").map((item) => String(item)),
            reason: roleId ? "更新演示后台角色" : "创建演示后台角色",
          };
          const selected = roleId ? state.workspace.data.admin.roles.find((item) => item.id === roleId) : null;
          if (roleId) payload.base_revision = selected?.revision;
          const data = await request(roleId ? `/api/v1/admin/roles/${encodeURIComponent(roleId)}` : "/api/v1/admin/roles", { method: roleId ? "PUT" : "POST", formal: true, idempotencyKey: key("admin-role"), body: payload });
          state.workspace.selectedRole = data;
          setToast(roleId ? "后台角色已更新" : "后台角色已创建");
          await loadWorkspace(true);
        }

        async function grantUsage(form) {
          const values = new FormData(form);
          const userId = String(values.get("user_id") || "").trim();
          if (!userId) throw new Error("请选择目标账号");
          const data = await request(`/api/v1/admin/users/${encodeURIComponent(userId)}/usage-grants`, { method: "POST", formal: true, idempotencyKey: key("admin-grant"), body: { feature: String(values.get("feature")), count: Number(values.get("count")), reason: String(values.get("reason") || "").trim() } });
          setToast(`已发放 ${data.grant?.count || 0} 次${data.grant?.feature || ""}`);
          await loadWorkspace(true);
        }

        async function cycleFeedback(id, currentStatus, revision) {
          const nextStatus = { new: "reviewed", reviewed: "closed" }[currentStatus];
          if (!nextStatus) return;
          const data = await request(`/api/v1/admin/feedback/${encodeURIComponent(id)}`, { method: "PUT", formal: true, idempotencyKey: key("feedback-status"), body: { status: nextStatus, base_revision: Number(revision || 1), reason: nextStatus === "reviewed" ? "已查看反馈" : "反馈处理完成" } });
          setToast(`反馈状态已更新为 ${labelStatus(data.status || nextStatus)}`);
          await loadWorkspace(true);
        }

        async function openLog(type, id) {
          const item = await request(`/api/v1/admin/logs/${encodeURIComponent(type)}/${encodeURIComponent(id)}`, { formal: true });
          state.workspace.selectedLog = { type, item };
          render();
        }

        async function applyAdminLogFilters(form) {
          const values = new FormData(form);
          state.workspace.adminLogFilters = {
            created_from: String(values.get("created_from") || ""),
            created_to: String(values.get("created_to") || ""),
            account_id: String(values.get("account_id") || "").trim(),
            result: String(values.get("result") || ""),
            request_id: String(values.get("request_id") || "").trim(),
            action: String(values.get("action") || "").trim(),
            event_type: String(values.get("event_type") || "").trim(),
            task_type: String(values.get("task_type") || "").trim(),
            task_status: String(values.get("task_status") || ""),
            retry_count_min: String(values.get("retry_count_min") || ""),
          };
          state.workspace.adminLogFormat = String(values.get("export_format") || "csv");
          state.workspace.selectedLog = null;
          await loadAdminLogs(state.workspace.adminLogType);
          setToast("日志筛选已应用");
        }

        async function exportLogs() {
          const type = state.workspace.adminLogType || "operations";
          const data = await request("/api/v1/admin/log-exports", { method: "POST", formal: true, idempotencyKey: key("log-export"), body: { log_type: type, export_format: state.workspace.adminLogFormat || "csv", filters: Object.fromEntries(adminLogQuery(type)) } });
          state.workspace.lastLogExport = data;
          setToast(data.status === "downloadable" ? "日志导出已生成，可下载" : "日志导出已排队");
        }

        async function handleFormalSubmit(form) {
          const button = form.querySelector("button[type=submit]");
          if (button) button.disabled = true;
          try {
            if (form.id === "auth-form") return await submitAuth(form);
            if (form.id === "formal-document-form") { const pendingFile = form.querySelector("input[type=file]")?.files?.[0] || null; state.workspace.pendingDocument = { type: String(form.elements.namedItem("document_type")?.value || "resume"), title: String(form.elements.namedItem("title")?.value || ""), text: String(form.elements.namedItem("text")?.value || ""), file: pendingFile }; await createFormalDocument(form); state.workspace.pendingDocument = null; setToast("资料草稿已生成，请检查结构化内容后确认"); }
            else if (form.id === "document-draft-review") await confirmDocumentDraft(form);
            else if (form.id === "preference-form") { await savePreference(form); setToast("岗位期望已保存为独立版本"); }
            else if (form.id === "recruiter-preference-form") { await savePreference(form); setToast("候选人岗位期望已保存"); }
            else if (form.id === "pool-form") { await savePool(form); setToast("岗位已入池，分析结果已可恢复"); }
            else if (form.id === "browser-draft-form") await saveBrowserDraftPool(form);
            else if (form.id === "pool-analyze-form") await analyzePoolItem(form);
            else if (form.id === "recruiter-analysis-form") { await createRecruiterAnalysis(form); setToast("单人分析已创建"); }
            else if (form.id === "fact-form") { await saveFact(form); form.reset(); setToast("事实已保存为 version 1"); }
            else if (form.id === "rewrite-form") { await createRewrite(form); setToast("逐段改写建议已生成"); }
            else if (form.id === "variant-form") { const values = new FormData(form); const pool = state.workspace.data.pool.find((item) => item.id === values.get("pool_id")); if (!pool) throw new Error("岗位不存在，请刷新后重试"); const resumeId = pool.resume_version_id; if (!resumeId) throw new Error("该岗位还没有绑定简历版本"); const data = await request("/api/v1/resumes", { method: "POST", formal: true, idempotencyKey: key("variant"), body: { job_pool_item_id: pool.id, source_resume_version_id: resumeId, title: String(values.get("title") || "岗位版简历") } }); state.workspace.selectedVariant = data; setToast("岗位版简历版本已创建"); }
            else if (form.id === "variant-editor-form") { await saveVariantVersion(form); }
            else if (form.id === "answer-form") { await answerInterview(form); setToast("回答已保存，反馈已返回"); }
            else if (form.id === "feedback-form") { await sendFeedback(form); form.reset(); setToast("感谢反馈，已单独记录"); }
            else if (form.id === "admin-user-search") return await searchAdminUsers(form);
            else if (form.id === "admin-log-filters") return await applyAdminLogFilters(form);
            else if (form.id === "admin-role-form") await saveAdminRole(form);
            else if (form.id === "admin-user-roles-form") await saveAdminUserRoles(form);
            else if (form.id === "admin-grant-form") await grantUsage(form);
            await loadWorkspace(true);
          } catch (error) {
            state.workspace.error = error instanceof ApiError ? `${error.code}：${error.message}` : error.message || "操作失败，请重试";
            render();
          } finally {
            if (button) button.disabled = false;
          }
        }

        document.addEventListener("submit", (event) => {
          const form = event.target.closest("form");
          if (!form) return;
          event.preventDefault();
          void handleFormalSubmit(form);
        });

        document.addEventListener("input", (event) => {
          if (event.target.id === "auth-email") state.auth.email = event.target.value;
          if (event.target.id === "auth-password") state.auth.password = event.target.value;
          if (event.target.id === "auth-token") state.auth.token = event.target.value;
        });

        document.addEventListener("change", (event) => {
          if (event.target.matches('input[name="registration_role"]')) {
            state.auth.role = event.target.value;
            document.querySelectorAll(".identity-card").forEach((card) => card.classList.toggle("active", card.contains(event.target)));
          }
          const form = event.target.closest("#preference-form, #recruiter-preference-form");
          if (form && event.target.matches("[data-preference-status]")) syncPreferenceStatus(form);
          const recruiterAnalysis = event.target.closest("#recruiter-analysis-form");
          if (recruiterAnalysis && event.target.name === "resume_version_id") syncRecruiterAnalysisPreferences(recruiterAnalysis);
          if (event.target.matches("[data-recruiter-subject]")) {
            state.workspace.recruiterSubjectId = event.target.value;
            state.workspace.selectedPreference = null;
            render();
          }
        });

        document.addEventListener("click", (event) => {
          const actionNode = event.target.closest("[data-action]");
          const pageNode = event.target.closest("[data-page]");
          if (pageNode) {
            // 菜单使用真实页面跳转；链接保留浏览器原生行为，按钮显式进入对应入口。
            if (pageNode.matches("a[href]")) return;
            window.location.assign(routeFor(pageNode.dataset.page));
            return;
          }
          if (!actionNode) return;
          const action = actionNode.dataset.action;
          if (action === "home") { state.screen = "demo"; state.toast = ""; render(); return; }
          if (action === "demo-scroll") { document.querySelector("#demo-flow")?.scrollIntoView({ behavior: "smooth" }); return; }
          if (action === "formal") { void openFormal(); return; }
          if (action === "auth-mode") { state.auth.mode = actionNode.dataset.mode; state.auth.error = ""; state.auth.notice = ""; render(); return; }
          if (action === "auth-role") { state.auth.role = actionNode.dataset.role; render(); return; }
          if (action === "resend-verification") { void resendVerification(); return; }
          if (action === "logout") { void (async () => { try { await request("/api/v1/auth/logout", { method: "POST", formal: true }); } catch (_) {} clearSession(); state.screen = "demo"; setToast("已退出正式工作台"); })(); return; }
          if (action === "refresh") { void refreshWorkspace(); return; }
          if (action === "open-task") { if (PAGE !== "tasks") { window.location.assign(routeFor("tasks", { task_id: actionNode.dataset.id })); return; } void openTask(actionNode.dataset.id); return; }
          if (action === "close-task") { state.workspace.selectedTask = null; state.workspace.selectedTaskEtag = ""; render(); return; }
          if (action === "retry-task") { void (async () => { try { await retryTask(actionNode.dataset.id); setToast("任务已重新排队"); render(); } catch (error) { state.workspace.error = error instanceof ApiError ? `${error.code}：${error.message}` : error.message || "任务重试失败"; render(); } })(); return; }
          if (action === "open-pool") { if (PAGE !== "pool") { window.location.assign(routeFor("pool", { pool_id: actionNode.dataset.id })); return; } void openPool(actionNode.dataset.id); return; }
          if (action === "close-pool") { state.workspace.selectedPool = null; render(); return; }
          if (action === "open-document-draft") { void openDocumentDraft(actionNode.dataset.id); return; }
          if (action === "discard-document-draft") { void (async () => { try { await discardDocumentDraft(actionNode.dataset.id); } catch (error) { state.workspace.error = error instanceof ApiError ? `${error.code}：${error.message}` : error.message || "草稿放弃失败"; render(); } })(); return; }
          if (action === "edit-preference") { state.workspace.selectedPreference = state.workspace.data.preferences.find((item) => item.id === actionNode.dataset.id) || null; state.workspace.page = "resume"; render(); return; }
          if (action === "default-preference") { void (async () => { try { await setDefaultPreference(actionNode.dataset.id); } catch (error) { state.workspace.error = error instanceof ApiError ? `${error.code}：${error.message}` : error.message || "设置默认期望失败"; render(); } })(); return; }
          if (action === "delete-preference") { void (async () => { try { await deletePreference(actionNode.dataset.id); } catch (error) { state.workspace.error = error instanceof ApiError ? `${error.code}：${error.message}` : error.message || "岗位期望删除失败"; render(); } })(); return; }
          if (action === "open-analysis") { if (PAGE !== "report") { window.location.assign(routeFor("report", { analysis_id: actionNode.dataset.id })); return; } void openAnalysis(actionNode.dataset.id); return; }
          if (action === "open-rewrite") { if (PAGE !== "rewrite") { const analysisId = state.workspace.selectedAnalysis?.id; if (analysisId) window.location.assign(routeFor("rewrite", { analysis_id: analysisId })); return; } void (async () => { try { await openRewrite(); } catch (error) { state.workspace.error = error instanceof ApiError ? `${error.code}：${error.message}` : error.message || "改写入口打开失败"; render(); } })(); return; }
          if (action === "decide-rewrite") { void (async () => { try { await decideRewrite(actionNode); } catch (error) { state.workspace.error = error instanceof ApiError ? `${error.code}：${error.message}` : error.message || "改写决定保存失败"; render(); } })(); return; }
          if (action === "create-variant-from-rewrite") { void (async () => { try { await createVariantFromRewrite(); render(); } catch (error) { state.workspace.error = error instanceof ApiError ? `${error.code}：${error.message}` : error.message || "岗位版创建失败"; render(); } })(); return; }
          if (action === "go-apply") { void (async () => { try { await goToApply(actionNode.dataset.id, actionNode.dataset.token); setToast("已记录去投递点击"); } catch (error) { state.workspace.error = error instanceof ApiError ? `${error.code}：${error.message}` : error.message || "去投递失败"; render(); } })(); return; }
          if (["delete-document", "delete-pool", "delete-analysis"].includes(action)) { void (async () => { const resource = action === "delete-document" ? "documents" : action === "delete-pool" ? "job-pool/items" : "analyses"; const label = action === "delete-document" ? "资料" : action === "delete-pool" ? "岗位" : "报告"; try { const deleted = await deleteResource(`/api/v1/${resource}/${encodeURIComponent(actionNode.dataset.id)}`, label); if (!deleted) return; state.workspace.selectedPool = null; state.workspace.selectedAnalysis = null; state.workspace.selectedRewrite = null; state.workspace.rewriteSource = null; setToast(`${label}已删除`); await loadWorkspace(true); } catch (error) { state.workspace.error = error instanceof ApiError ? `${error.code}：${error.message}` : error.message || `${label}删除失败`; render(); } })(); return; }
          if (action === "create-variant") { void (async () => { try { const variant = await createVariant(); window.location.assign(routeFor("variants", { variant_id: variant.id })); } catch (error) { state.workspace.error = error.message || "岗位版创建失败"; render(); } })(); return; }
          if (action === "open-variant") { if (PAGE !== "variants") { window.location.assign(routeFor("variants", { variant_id: actionNode.dataset.id })); return; } void openVariant(actionNode.dataset.id); return; }
          if (action === "close-variant") { state.workspace.selectedVariant = null; render(); return; }
          if (action === "move-variant-section") { moveVariantSection(actionNode); return; }
          if (action === "delete-variant") { void (async () => { try { await deleteVariant(actionNode.dataset.id); } catch (error) { state.workspace.error = error instanceof ApiError ? `${error.code}：${error.message}` : error.message || "岗位版删除失败"; render(); } })(); return; }
          if (action === "export-variant") { void (async () => { try { await exportVariant(actionNode.dataset.versionId); setToast("PDF 导出已完成，可下载"); render(); } catch (error) { state.workspace.error = error instanceof ApiError ? `${error.code}：${error.message}` : error.message; render(); } })(); return; }
          if (action === "start-interview") { void (async () => { try { const interview = await startInterview(); window.location.assign(routeFor("interview", { interview_id: interview.id })); } catch (error) { state.workspace.error = error.message || "面试创建失败"; render(); } })(); return; }
          if (action === "new-interview") { const candidate = state.workspace.data.pool.find((item) => item.latest_analysis && item.resume_version_id); if (candidate) void (async () => { try { await openAnalysis(candidate.latest_analysis.id); await startInterview(); setToast("面试会话已创建"); render(); } catch (error) { state.workspace.error = error.message || "面试创建失败"; render(); } })(); return; }
          if (action === "open-interview") { if (PAGE !== "interview") { window.location.assign(routeFor("interview", { interview_id: actionNode.dataset.id })); return; } void openInterview(actionNode.dataset.id); return; }
          if (action === "delete-interview") { void (async () => { try { await deleteInterview(actionNode.dataset.id); } catch (error) { state.workspace.error = error instanceof ApiError ? `${error.code}：${error.message}` : error.message || "面试删除失败"; render(); } })(); return; }
          if (action === "finish-interview") { void (async () => { try { await finishInterview(actionNode.dataset.id); setToast("已生成练习总结"); render(); await loadWorkspace(true); } catch (error) { state.workspace.error = error instanceof ApiError ? `${error.code}：${error.message}` : error.message; render(); } })(); }
          if (action === "cycle-feedback") { void (async () => { try { await cycleFeedback(actionNode.dataset.id, actionNode.dataset.status, actionNode.dataset.revision); } catch (error) { state.workspace.error = error instanceof ApiError ? `${error.code}：${error.message}` : error.message || "反馈状态更新失败"; render(); } })(); return; }
          if (action === "toggle-user-status") { void (async () => { try { await toggleUserStatus(actionNode.dataset.id, actionNode.dataset.status); } catch (error) { state.workspace.error = error instanceof ApiError ? `${error.code}：${error.message}` : error.message || "账号状态更新失败"; render(); } })(); return; }
          if (action === "open-admin-user") { void (async () => { try { await openAdminUser(actionNode.dataset.id); } catch (error) { state.workspace.error = error instanceof ApiError ? `${error.code}：${error.message}` : error.message || "用户详情读取失败"; render(); } })(); return; }
          if (action === "close-admin-user") { state.workspace.selectedAdminUser = null; render(); return; }
          if (action === "select-role") { state.workspace.selectedRole = state.workspace.data.admin.roles.find((item) => item.id === actionNode.dataset.id) || null; render(); return; }
          if (action === "clear-role") { state.workspace.selectedRole = null; render(); return; }
          if (action === "open-log") { void (async () => { try { await openLog(actionNode.dataset.logType, actionNode.dataset.id); } catch (error) { state.workspace.error = error instanceof ApiError ? `${error.code}：${error.message}` : error.message || "日志读取失败"; render(); } })(); return; }
          if (action === "close-log") { state.workspace.selectedLog = null; render(); return; }
          if (action === "admin-log-type") { void (async () => { try { state.workspace.adminLogType = actionNode.dataset.logType; state.workspace.selectedLog = null; await loadAdminLogs(state.workspace.adminLogType); render(); } catch (error) { state.workspace.error = error instanceof ApiError ? `${error.code}：${error.message}` : error.message || "日志读取失败"; render(); } })(); return; }
          if (action === "export-logs") { void (async () => { try { await exportLogs(); render(); } catch (error) { state.workspace.error = error instanceof ApiError ? `${error.code}：${error.message}` : error.message || "日志导出失败"; render(); } })(); return; }
        });

        function restoreAuthActionFromLink() {
          const params = new URLSearchParams(window.location.search);
          const token = params.get("token");
          const action = params.get("action");
          const mode = { verify: "verify", reset: "reset", recover: "recover" }[action];
          if (!token || !mode) return;
          state.screen = "formal";
          state.auth.mode = mode;
          state.auth.token = token;
          state.auth.notice = "已从安全链接带入一次性令牌；提交后该链接会失效。";
          // 令牌只用于当前内存流程，去掉地址栏副本，避免复制链接时意外传播。
          window.history.replaceState({}, document.title, window.location.pathname);
        }

        restoreAuthActionFromLink();
        render();
        void loadHealth();
        void handleBrowserSync();
        if (state.screen === "formal" && state.session.token) void openFormal();
      })();
