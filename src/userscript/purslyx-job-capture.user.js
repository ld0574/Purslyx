// ==UserScript==
// @name         Purslyx 岗位自动获取
// @namespace    https://purslyx.local/
// @version      0.2.0
// @description  在支持的 BOSS 直聘／猎聘详情页自动上传待确认岗位草稿。
// @match        https://www.zhipin.com/job_detail/*
// @match        https://zhipin.com/job_detail/*
// @match        https://www.liepin.com/job/*
// @match        https://liepin.com/job/*
// @grant        GM_xmlhttpRequest
// @connect      127.0.0.1
// @connect      localhost
// ==/UserScript==

(function () {
  "use strict";

  // 演示环境由【本地开发环境.md】启动到 201 PostgreSQL；部署时只需替换产品地址。
  const API_ORIGIN = "http://127.0.0.1:8001";
  const PRODUCT_ORIGIN = API_ORIGIN;
  const DRAFT_KEY = "purslyx.pending-job-drafts.v1";
  const TOKEN_KEY = "purslyx.browser-token.v1";
  const seen = new Set();
  let captureTimer = 0;
  let lastUrl = location.href;
  let syncWindow = null;
  let syncNonce = "";

  const ADAPTERS = {
    boss: {
      title: ["h1.job-name", ".job-name", ".job-title", "h1"],
      company: [".company-info .name", ".company-name", ".sider-company .name"],
      location: [".job-primary .text-desc", ".job-location", ".location-address", "[class*=location]"],
      workMode: [".job-primary .job-tags", ".job-primary .text-desc"],
      salary: [".job-primary .salary", ".salary", "[class*=salary]"],
      description: [".job-sec-text", ".job-detail", ".job-description", ".job-sec", "[class*=job-detail]"]
    },
    liepin: {
      title: ["h1.job-title", ".job-title", ".job-name", "h1"],
      company: [".company-name", ".job-company .name", "[class*=company-name]"],
      location: [".job-properties .job-properties-item", ".job-location", ".job-address", "[class*=location]"],
      workMode: [".job-properties", ".job-tags", "[class*=work-mode]"],
      salary: [".job-salary", ".salary", "[class*=salary]"],
      description: [".job-description", ".job-intro", ".job-detail", ".content", "[class*=job-detail]"]
    }
  };

  function currentPlatform() {
    return location.hostname.includes("liepin") ? "liepin" : "boss";
  }

  function isDetailPage() {
    const path = location.pathname;
    return currentPlatform() === "boss" ? path.startsWith("/job_detail/") : path.startsWith("/job/");
  }

  function cleanText(value) {
    return String(value || "")
      .replace(/\u00a0/g, " ")
      .replace(/[ \t]+\n/g, "\n")
      .replace(/\n{3,}/g, "\n\n")
      .trim();
  }

  function textOf(selectors) {
    for (const selector of selectors) {
      const node = document.querySelector(selector);
      const text = cleanText(node && (node.innerText || node.textContent || ""));
      if (text) return text;
    }
    return "";
  }

  function disclosedWorkMode(value) {
    const text = cleanText(value).toLowerCase();
    if (/remote|远程|居家/.test(text)) return "remote";
    if (/hybrid|混合办公|混合/.test(text)) return "hybrid";
    if (/onsite|现场办公|到岗|坐班/.test(text)) return "onsite";
    return null;
  }

  function capturePage() {
    if (!isDetailPage()) return null;
    const adapter = ADAPTERS[currentPlatform()];
    const locationText = textOf(adapter.location);
    const workModeText = textOf(adapter.workMode);
    const description = textOf(adapter.description);
    const value = {
      platform: currentPlatform(),
      source_url: location.href,
      capture_schema_version: "browser-job-capture-v1",
      captured_at: new Date().toISOString(),
      job_title: textOf(adapter.title),
      company_name: textOf(adapter.company),
      location_text: locationText,
      work_mode: disclosedWorkMode(workModeText),
      salary_text: textOf(adapter.salary),
      job_description_text: description,
      missing_field_codes: []
    };
    if (!value.job_title) value.missing_field_codes.push("job_title");
    if (!value.location_text) value.missing_field_codes.push("job_location");
    if (!value.work_mode) value.missing_field_codes.push("work_mode");
    if (!value.salary_text) value.missing_field_codes.push("job_salary");
    if (!value.job_description_text) value.missing_field_codes.push("job_description");
    return value;
  }

  function fingerprint(value) {
    // 这里只做本机去重指纹；服务端仍会重新计算规范化摘要。
    return [value.platform, value.source_url, value.job_title, value.company_name, value.job_description_text].join("|");
  }

  function ensureStatusPanel() {
    let node = document.querySelector("#purslyx-capture-status");
    if (node) return node;
    node = document.createElement("aside");
    node.id = "purslyx-capture-status";
    node.style.cssText = "position:fixed;right:18px;bottom:18px;z-index:2147483647;width:270px;padding:12px 14px;border:1px solid #cfe0ec;border-radius:12px;font:13px/1.5 -apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;color:#16324f;background:#f8fcff;box-shadow:0 8px 28px #16324f2b";
    node.innerHTML = "<div data-status-text></div><div style=\"display:flex;gap:8px;margin-top:9px;flex-wrap:wrap\"><button type=\"button\" data-sync style=\"border:0;border-radius:7px;padding:6px 9px;background:#1264e8;color:white;cursor:pointer\">同步 Purslyx</button><button type=\"button\" data-retry style=\"border:1px solid #cfe0ec;border-radius:7px;padding:6px 9px;background:white;color:#16324f;cursor:pointer\">重试识别</button><a data-open-draft target=\"_blank\" rel=\"noreferrer\" style=\"display:none;border:1px solid #b9d3f6;border-radius:7px;padding:6px 9px;color:#1264e8;text-decoration:none;background:#eaf5ff\">打开确认页</a></div>";
    document.body.appendChild(node);
    node.querySelector("[data-sync]").addEventListener("click", openSyncWindow);
    node.querySelector("[data-retry]").addEventListener("click", scheduleCapture);
    return node;
  }

  function setStatus(message, kind) {
    const node = ensureStatusPanel();
    const textNode = node.querySelector("[data-status-text]");
    textNode.textContent = `Purslyx：${message}`;
    node.style.background = kind === "error" ? "#fff4f0" : kind === "success" ? "#edf9f3" : "#f8fcff";
    node.style.borderColor = kind === "error" ? "#f2c9be" : kind === "success" ? "#bfe8d2" : "#cfe0ec";
  }

  function setDraftLink(path) {
    const link = ensureStatusPanel().querySelector("[data-open-draft]");
    if (!link) return;
    if (typeof path === "string" && path.startsWith("/job-pool/items?browser_draft_id=")) {
      link.href = `${PRODUCT_ORIGIN}${path}`;
      link.style.display = "inline-block";
    } else {
      link.removeAttribute("href");
      link.style.display = "none";
    }
  }

  function randomNonce() {
    const bytes = new Uint8Array(24);
    crypto.getRandomValues(bytes);
    return btoa(String.fromCharCode(...bytes)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  }

  function openSyncWindow() {
    const nonce = randomNonce();
    syncNonce = nonce;
    syncWindow = window.open(`${PRODUCT_ORIGIN}/?purslyx_browser_sync=1&origin=${encodeURIComponent(location.origin)}&nonce=${encodeURIComponent(nonce)}`, "purslyx-browser-sync", "popup,width=440,height=720");
    if (!syncWindow) {
      setStatus("同步窗口被浏览器拦截，请允许弹窗后重试。", "error");
      return;
    }
    setStatus("等待已登录的 Purslyx 页面完成一次性同步…");
  }

  function request(options) {
    return new Promise((resolve, reject) => {
      GM_xmlhttpRequest({
        method: options.method || "GET",
        url: `${API_ORIGIN}${options.path}`,
        headers: { "Content-Type": "application/json", ...(options.headers || {}) },
        data: options.body ? JSON.stringify(options.body) : undefined,
        onload: (response) => {
          let body;
          try { body = JSON.parse(response.responseText || "{}"); } catch (_) { body = {}; }
          if (response.status >= 200 && response.status < 300) resolve(body.data);
          else reject(new Error(body?.error?.message || `服务返回 ${response.status}`));
        },
        onerror: () => reject(new Error("无法连接 201 PostgreSQL 对应的 Purslyx 服务"))
      });
    });
  }

  function readPending() {
    try {
      const rows = JSON.parse(localStorage.getItem(DRAFT_KEY) || "[]");
      if (!Array.isArray(rows)) return [];
      const cutoff = Date.now() - 7 * 24 * 60 * 60 * 1000;
      return rows.filter((row) => row && row.value && Date.parse(row.saved_at || "") >= cutoff);
    } catch (_) { return []; }
  }

  function savePending(value) {
    const rows = readPending().filter((row) => row.fingerprint !== fingerprint(value));
    rows.unshift({ fingerprint: fingerprint(value), value, saved_at: new Date().toISOString() });
    localStorage.setItem(DRAFT_KEY, JSON.stringify(rows.slice(0, 10)));
  }

  function removePending(value) {
    const valueFingerprint = fingerprint(value);
    localStorage.setItem(DRAFT_KEY, JSON.stringify(readPending().filter((row) => row.fingerprint !== valueFingerprint)));
  }

  async function exchangeCode(data) {
    if (!data || data.type !== "PURSLYX_BROWSER_CODE" || data.nonce !== syncNonce || data.origin !== location.origin) return;
    if (syncWindow && data.sourceWindow && data.sourceWindow !== syncWindow) return;
    const currentWindow = syncWindow;
    syncWindow = null;
    syncNonce = "";
    try {
      const tokenData = await request({
        method: "POST",
        path: "/api/v1/browser-auth/exchange",
        headers: { "Idempotency-Key": `browser-exchange-${data.nonce}` },
        body: { authorization_code: data.authorization_code, origin: data.origin, nonce: data.nonce }
      });
      if (!tokenData || !tokenData.browser_token) throw new Error("浏览器授权响应不完整");
      localStorage.setItem(TOKEN_KEY, tokenData.browser_token);
      window.dispatchEvent(new CustomEvent("purslyx-browser-token-updated"));
      setStatus("登录同步完成，正在上传当前岗位…", "success");
      if (currentWindow && !currentWindow.closed) currentWindow.close();
      await flushPending();
      scheduleCapture();
    } catch (error) {
      setStatus(`${error.message}；请重新点击同步。`, "error");
    }
  }

  window.addEventListener("message", (event) => {
    if (event.origin !== PRODUCT_ORIGIN || event.source !== syncWindow) return;
    const data = event.data || {};
    exchangeCode({ ...data, sourceWindow: event.source });
  });

  async function upload(value, fromPending) {
    if (!value || !value.job_description_text) {
      setStatus("页面内容尚未稳定，继续等待岗位正文…");
      return;
    }
    const valueFingerprint = fingerprint(value);
    if (seen.has(valueFingerprint) && !fromPending) return;
    const browserToken = localStorage.getItem(TOKEN_KEY);
    if (!browserToken) {
      savePending(value);
      setStatus("尚未同步 Purslyx；岗位已保存在本机草稿。", "error");
      return;
    }
    seen.add(valueFingerprint);
    setStatus("正在识别并上传待确认岗位…");
    try {
      const draft = await request({
        method: "POST",
        path: "/api/v1/browser/job-drafts",
        headers: { Authorization: `Bearer ${browserToken}`, "Idempotency-Key": `capture-${valueFingerprint.slice(0, 80)}` },
        body: value
      });
      removePending(value);
      setDraftLink(draft.confirm_path);
      setStatus(`已获取「${draft.job_title || "当前岗位"}」，请回 Purslyx 检查并确认入池。`, "success");
    } catch (error) {
      seen.delete(valueFingerprint);
      savePending(value);
      setStatus(`${error.message}；岗位已保存在本机，可稍后重试。`, "error");
    }
  }

  async function flushPending() {
    if (!localStorage.getItem(TOKEN_KEY)) return;
    for (const row of readPending()) {
      if (row && row.value) await upload(row.value, true);
    }
  }

  function scheduleCapture() {
    window.clearTimeout(captureTimer);
    captureTimer = window.setTimeout(() => upload(capturePage(), false), 700);
  }

  const observer = new MutationObserver(scheduleCapture);
  observer.observe(document.documentElement, { childList: true, subtree: true });
  window.addEventListener("load", scheduleCapture, { once: true });
  window.addEventListener("popstate", scheduleCapture);
  window.addEventListener("hashchange", scheduleCapture);
  window.setInterval(() => {
    if (location.href !== lastUrl) {
      lastUrl = location.href;
      seen.clear();
      scheduleCapture();
    }
  }, 1000);
  window.addEventListener("purslyx-browser-token-updated", flushPending);
  ensureStatusPanel();
  scheduleCapture();
  flushPending();
})();
