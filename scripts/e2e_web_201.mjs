#!/usr/bin/env node

/**
 * 用本机 Chrome 对 201 完整前端执行真实浏览器验收。
 *
 * 脚本只创建随机本地测试账号，不读取《本地开发环境.md》，也不打印令牌或密码。
 */

import { spawn } from "node:child_process";
import { mkdtemp, rm } from "node:fs/promises";
import net from "node:net";
import os from "node:os";
import path from "node:path";

const BASE_URL = (process.env.PURSLYX_BASE_URL || "http://127.0.0.1:8001").replace(/\/$/, "");
const CHROME = process.env.PURSLYX_CHROME_PATH || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const PASSWORD = "Purslyx-E2E!2026";

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

function freePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      server.close(() => resolve(address.port));
    });
  });
}

async function waitForValue(action, description, timeoutMs = 15000) {
  const deadline = Date.now() + timeoutMs;
  let lastError;
  while (Date.now() < deadline) {
    try {
      const value = await action();
      if (value) return value;
    } catch (error) {
      lastError = error;
    }
    await new Promise((resolve) => setTimeout(resolve, 120));
  }
  throw new Error(`${description}超时${lastError ? `：${lastError.message}` : ""}`);
}

class CdpClient {
  constructor(socket) {
    this.socket = socket;
    this.nextId = 1;
    this.pending = new Map();
    this.events = new Map();
    socket.addEventListener("message", (event) => this.receive(JSON.parse(event.data)));
  }

  receive(message) {
    if (message.id) {
      const pending = this.pending.get(message.id);
      if (!pending) return;
      this.pending.delete(message.id);
      if (message.error) pending.reject(new Error(message.error.message));
      else pending.resolve(message.result);
      return;
    }
    const listeners = this.events.get(message.method) || [];
    for (const listener of listeners) listener(message.params || {});
  }

  send(method, params = {}) {
    const id = this.nextId++;
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      this.socket.send(JSON.stringify({ id, method, params }));
    });
  }

  on(method, listener) {
    const listeners = this.events.get(method) || [];
    listeners.push(listener);
    this.events.set(method, listeners);
  }

  async evaluate(expression) {
    const result = await this.send("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true });
    if (result.exceptionDetails) throw new Error(result.exceptionDetails.exception?.description || "页面脚本执行失败");
    return result.result?.value;
  }

  async navigate(url) {
    await this.send("Page.navigate", { url });
    await waitForValue(() => this.evaluate("document.readyState === 'complete'"), `页面加载 ${url}`);
  }

  close() {
    this.socket.close();
  }
}

async function connectCdp(webSocketUrl) {
  const socket = new WebSocket(webSocketUrl);
  await new Promise((resolve, reject) => {
    socket.addEventListener("open", resolve, { once: true });
    socket.addEventListener("error", reject, { once: true });
  });
  return new CdpClient(socket);
}

async function setValue(client, selector, value) {
  const changed = await client.evaluate(`(() => {
    const element = document.querySelector(${JSON.stringify(selector)});
    if (!element) return { ok: false, path: location.pathname, forms: [...document.forms].map(item => item.id) };
    element.value = ${JSON.stringify(value)};
    element.dispatchEvent(new Event("input", { bubbles: true }));
    element.dispatchEvent(new Event("change", { bubbles: true }));
    return { ok: element.value === ${JSON.stringify(value)}, path: location.pathname, actual: element.value };
  })()`);
  assert(changed?.ok, `不能填写控件 ${selector}；当前 ${changed?.path || "未知页面"}；现有表单 ${(changed?.forms || []).join("、") || "无"}`);
}

async function setChecked(client, selector) {
  const changed = await client.evaluate(`(() => {
    const element = document.querySelector(${JSON.stringify(selector)});
    if (!element) return false;
    element.checked = true;
    element.dispatchEvent(new Event("input", { bubbles: true }));
    element.dispatchEvent(new Event("change", { bubbles: true }));
    return element.checked;
  })()`);
  assert(changed, `不能选择控件 ${selector}`);
}

async function submit(client, selector) {
  const submitted = await client.evaluate(`(() => {
    const form = document.querySelector(${JSON.stringify(selector)});
    if (!form) return { ok: false, path: location.pathname, forms: [...document.forms].map(item => item.id) };
    form.requestSubmit();
    return { ok: true, path: location.pathname };
  })()`);
  assert(submitted?.ok, `不能提交表单 ${selector}；当前 ${submitted?.path || "未知页面"}；现有表单 ${(submitted?.forms || []).join("、") || "无"}`);
}

async function waitForText(client, text, timeoutMs = 15000) {
  return waitForValue(
    () => client.evaluate(`document.body.innerText.includes(${JSON.stringify(text)})`),
    `等待页面文字“${text}”`,
    timeoutMs,
  );
}

async function waitForSelector(client, selector, timeoutMs = 15000) {
  return waitForValue(
    () => client.evaluate(`Boolean(document.querySelector(${JSON.stringify(selector)}))`),
    `等待控件 ${selector}`,
    timeoutMs,
  );
}

async function waitForPath(client, pathname, timeoutMs = 15000) {
  return waitForValue(
    () => client.evaluate(`location.pathname === ${JSON.stringify(pathname)}`),
    `等待路由 ${pathname}`,
    timeoutMs,
  );
}

async function waitForWorkspaceReady(client, expectedText, timeoutMs = 15000) {
  return waitForValue(
    () => client.evaluate(`document.querySelector(".workspace-layout")?.dataset.workspaceLoaded === "true"
      && Boolean(document.querySelector(".workspace-content .page-heading"))
      && !document.querySelector(".route-bootstrap")
      && document.body.innerText.includes(${JSON.stringify(expectedText)})`),
    `等待工作台页面“${expectedText}”`,
    timeoutMs,
  );
}

async function openWorkbenchPage(client, pathname, expectedText) {
  await client.navigate(`${BASE_URL}${pathname}`);
  await waitForWorkspaceReady(client, expectedText);
  const staticRoute = await client.evaluate(`fetch(${JSON.stringify(`${BASE_URL}${pathname}`)}, { cache: "no-store" })
    .then(response => response.text())
    .then(text => /data-route-view=/.test(text) && /<h1>/.test(text) && (text.match(/<h2>/g) || []).length >= 2)`);
  assert(staticRoute, `${pathname} 仍是空 HTML 壳`);
}

async function followLink(client, selector) {
  const href = await client.evaluate(`document.querySelector(${JSON.stringify(selector)})?.href || ""`);
  assert(href, `链接 ${selector} 没有目标地址`);
  await client.navigate(href);
}

async function register(client, role, email) {
  await client.navigate(`${BASE_URL}/app/register`);
  await waitForSelector(client, "#auth-form");
  if (role === "recruiter") {
    await setChecked(client, 'input[name="registration_role"][value="recruiter"]');
  }
  await setValue(client, "#auth-email", email);
  await setValue(client, "#auth-password", PASSWORD);
  await submit(client, "#auth-form");
  try {
    await waitForPath(client, `/app/${role}/dashboard`);
  } catch (error) {
    const diagnostics = await client.evaluate(`({
      href: location.href,
      activeRole: document.querySelector("[data-action=auth-role].active")?.dataset.role || null,
      errors: [...document.querySelectorAll(".callout.attention")].map(element => element.innerText),
    })`);
    throw new Error(`${error.message}；${JSON.stringify(diagnostics)}`);
  }
  await waitForWorkspaceReady(client, "工作台");
  const sessionReady = await client.evaluate(`(() => {
    const session = JSON.parse(localStorage.getItem("purslyx-web-session-v1") || "{}");
    return Boolean(session.token && session.account?.registration_role === ${JSON.stringify(role)});
  })()`);
  assert(sessionReady, `${role} 登录会话没有跨页面保存`);
}

async function logoutSession(client) {
  const loggedOut = await client.evaluate(`(async () => {
    const session = JSON.parse(localStorage.getItem("purslyx-web-session-v1") || "{}");
    const response = await fetch("/api/v1/auth/logout", {
      method: "POST",
      headers: { Authorization: "Bearer " + (session.token || ""), "X-CSRF-Token": session.csrf || "" },
    });
    localStorage.clear();
    sessionStorage.clear();
    return response.status === 204;
  })()`);
  assert(loggedOut, "测试账号退出失败");
}

async function createAndConfirmDocument(client, { type, title, text }) {
  const previousCount = await client.evaluate("document.querySelectorAll('.document-item').length");
  if (type) await setValue(client, '#formal-document-form [name="document_type"]', type);
  await setValue(client, '#formal-document-form [name="title"]', title);
  await setValue(client, '#formal-document-form [name="text"]', text);
  await submit(client, "#formal-document-form");
  await waitForSelector(client, "#document-draft-review");
  await submit(client, "#document-draft-review");
  await waitForValue(
    () => client.evaluate(`!document.querySelector("#document-draft-review")
      && document.querySelectorAll(".document-item").length > ${Number(previousCount)}
      && document.body.innerText.includes(${JSON.stringify(title)})`),
    `确认资料 ${title}`,
  );
}

async function seekerFlow(client, suffix) {
  await register(client, "seeker", `e2e-seeker-${suffix}@purslyx.local`);
  await openWorkbenchPage(client, "/app/seeker/resume", "简历与岗位期望");
  await createAndConfirmDocument(client, {
    title: "E2E 前端简历",
    text: "负责 Vue 与 TypeScript 前端项目开发，完成组件设计、性能优化和自动化测试。",
  });
  await submit(client, "#preference-form");
  try {
    await waitForSelector(client, '[data-action="edit-preference"]');
  } catch (error) {
    const pageError = await client.evaluate("[...document.querySelectorAll('.callout.attention')].map(element => element.innerText).join('；')");
    throw new Error(`${error.message}${pageError ? `：${pageError}` : ""}`);
  }

  await openWorkbenchPage(client, "/app/seeker/pool", "保存一份手动岗位");
  await submit(client, "#pool-form");
  try {
    await waitForSelector(client, 'a[href*="/report?analysis_id="]', 20000);
  } catch (error) {
    const diagnostics = await client.evaluate(`({
      href: location.href,
      errors: [...document.querySelectorAll(".callout.attention")].map(element => element.innerText),
      poolItems: document.querySelectorAll(".pool-item").length,
      text: document.body.innerText.slice(-800),
    })`);
    throw new Error(`${error.message}；${JSON.stringify(diagnostics)}`);
  }
  await followLink(client, 'a[href*="/report?analysis_id="]');
  try {
    await waitForPath(client, "/app/seeker/report");
  } catch (error) {
    const location = await client.evaluate("location.href");
    const pageError = await client.evaluate("[...document.querySelectorAll('.callout.attention')].map(element => element.innerText).join('；')");
    throw new Error(`${error.message}；当前 ${location}${pageError ? `；${pageError}` : ""}`);
  }
  await waitForText(client, "能力维度与逐条依据", 20000);

  const pages = [
    ["/app/seeker/dashboard", "继续下一步"],
    ["/app/seeker/rewrite", "事实与改写"],
    ["/app/seeker/variants", "岗位版简历"],
    ["/app/seeker/interview", "面试练习"],
    ["/app/seeker/tasks", "任务中心"],
    ["/app/seeker/usage", "最近流水"],
    ["/app/seeker/stats", "统计与反馈"],
  ];
  for (const [pathname, text] of pages) await openWorkbenchPage(client, pathname, text);
  return { pages: pages.length + 3, report: true };
}

async function recruiterFlow(client, suffix) {
  // 先调用正式退出接口，再清理当前浏览器的测试会话，避免两个固定身份互相污染。
  await logoutSession(client);
  const email = `e2e-recruiter-${suffix}@purslyx.local`;
  await register(client, "recruiter", email);
  await openWorkbenchPage(client, "/app/recruiter/materials", "候选人资料");
  await createAndConfirmDocument(client, {
    type: "job_description",
    title: "E2E 招聘岗位",
    text: "前端开发工程师，要求熟悉 Vue、TypeScript、性能优化与自动化测试。",
  });
  await createAndConfirmDocument(client, {
    type: "resume",
    title: "E2E 候选人简历",
    text: "候选人负责 Vue 与 TypeScript 项目，参与性能优化并编写自动化测试。",
  });
  await waitForSelector(client, "#recruiter-analysis-form button[type=submit]");
  await submit(client, "#recruiter-analysis-form");
  try {
    await waitForPath(client, "/app/recruiter/report");
  } catch (error) {
    const diagnostics = await client.evaluate(`({
      href: location.href,
      errors: [...document.querySelectorAll(".callout.attention")].map(element => element.innerText),
      reportText: document.body.innerText.includes("能力维度与逐条依据"),
      historyLinks: document.querySelectorAll('a[href*="/report?analysis_id="]').length,
    })`);
    throw new Error(`${error.message}；${JSON.stringify(diagnostics)}`);
  }
  await waitForText(client, "能力维度与逐条依据", 20000);

  const pages = [
    ["/app/recruiter/dashboard", "最近分析"],
    ["/app/recruiter/tasks", "任务中心"],
    ["/app/recruiter/usage", "最近流水"],
    ["/app/recruiter/stats", "统计与反馈"],
  ];
  for (const [pathname, text] of pages) await openWorkbenchPage(client, pathname, text);

  await logoutSession(client);
  await client.navigate(`${BASE_URL}/app/login`);
  await waitForSelector(client, "#auth-form");
  await setValue(client, "#auth-email", email);
  await setValue(client, "#auth-password", PASSWORD);
  await submit(client, "#auth-form");
  await waitForPath(client, "/app/recruiter/dashboard");
  await waitForWorkspaceReady(client, "工作台");
  return { pages: pages.length + 2, report: true, login: true };
}

async function adminEntries(client) {
  const entries = [
    ["/app/admin/metrics", "站点概况"],
    ["/app/admin/users", "用户管理"],
    ["/app/admin/roles", "角色权限"],
    ["/app/admin/usage", "次数管理"],
    ["/app/admin/logs", "日志管理"],
  ];
  for (const [pathname, text] of entries) {
    await openWorkbenchPage(client, pathname, text);
    await waitForText(client, "没有访问权限");
  }
  const denied = await client.evaluate(`(async () => {
    const session = JSON.parse(localStorage.getItem("purslyx-web-session-v1") || "{}");
    const response = await fetch("/api/v1/admin/users", { headers: { Authorization: "Bearer " + (session.token || "") } });
    return response.status === 403;
  })()`);
  assert(denied, "普通账号访问管理接口没有被拒绝");
  return { pages: entries.length, permissionProtected: true };
}

async function main() {
  const health = await fetch(`${BASE_URL}/health`).then((response) => response.json());
  assert(health.status === "ok", "201 服务健康检查失败");
  assert(health.environment === "201", "当前服务不是 201 环境");
  assert(health.database?.backend === "postgresql", "当前服务不是 PostgreSQL");

  const port = await freePort();
  const profile = await mkdtemp(path.join(os.tmpdir(), "purslyx-e2e-"));
  const chrome = spawn(CHROME, [
    "--headless=new",
    "--disable-gpu",
    "--no-first-run",
    "--no-default-browser-check",
    `--remote-debugging-port=${port}`,
    `--user-data-dir=${profile}`,
    "about:blank",
  ], { stdio: ["ignore", "ignore", "ignore"] });

  let client;
  try {
    const version = await waitForValue(
      async () => {
        const response = await fetch(`http://127.0.0.1:${port}/json/version`).catch(() => null);
        return response?.ok ? response.json() : null;
      },
      "启动本机 Chrome",
    );
    const target = await fetch(`http://127.0.0.1:${port}/json/new?${encodeURIComponent(`${BASE_URL}/app/register`)}`, { method: "PUT" }).then((response) => response.json());
    assert(version.webSocketDebuggerUrl && target.webSocketDebuggerUrl, "Chrome 调试通道不可用");
    client = await connectCdp(target.webSocketDebuggerUrl);
    await client.send("Page.enable");
    await client.send("Runtime.enable");

    const browserErrors = [];
    client.on("Runtime.exceptionThrown", (event) => browserErrors.push(event.exceptionDetails?.exception?.description || event.exceptionDetails?.text || "页面异常"));
    const suffix = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    const seeker = await seekerFlow(client, suffix);
    const recruiter = await recruiterFlow(client, suffix);
    const admin = await adminEntries(client);
    assert(browserErrors.length === 0, `浏览器控制台异常：${browserErrors.join("；")}`);

    console.log(JSON.stringify({
      environment: health.environment,
      database: health.database.backend,
      seeker,
      recruiter,
      admin,
      result: "passed",
    }, null, 2));
  } finally {
    if (client) client.close();
    const chromeStopped = new Promise((resolve) => chrome.once("exit", resolve));
    chrome.kill("SIGTERM");
    await Promise.race([chromeStopped, new Promise((resolve) => setTimeout(resolve, 3000))]);
    await rm(profile, { recursive: true, force: true, maxRetries: 5, retryDelay: 120 });
  }
}

main().catch((error) => {
  console.error(`201 前端 E2E 失败：${error.message}`);
  process.exitCode = 1;
});
