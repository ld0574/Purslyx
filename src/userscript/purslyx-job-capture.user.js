// ==UserScript==
// @name         Purslyx 岗位自动获取
// @namespace    https://purslyx.local/
// @version      0.1.0
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

  // 生产部署只需替换这个地址；脚本不保存密码，也不把岗位正文写进日志。
  const API_ORIGIN = "http://127.0.0.1:8001";
  const DRAFT_KEY = "purslyx.pending-job-drafts.v1";
  const TOKEN_KEY = "purslyx.browser-token.v1";
  const seen = new Set();

  function platform() {
    return location.hostname.includes("liepin") ? "liepin" : "boss";
  }

  function textOf(selectors) {
    for (const selector of selectors) {
      const node = document.querySelector(selector);
      const text = node && (node.innerText || node.textContent || "").trim();
      if (text) return text.replace(/\s+\n/g, "\n").replace(/\n{3,}/g, "\n\n");
    }
    return "";
  }

  function capturePage() {
    const value = {
      platform: platform(),
      source_url: location.href,
      capture_schema_version: "browser-job-capture-v1",
      captured_at: new Date().toISOString(),
      job_title: textOf(["h1", ".job-name", ".name", "[class*=job-title]"]),
      company_name: textOf([".company-name", ".company-info h2", "[class*=company-name]"]),
      location_text: textOf([".location-address", ".job-location", "[class*=location]"]),
      work_mode: null,
      salary_text: textOf([".salary", ".job-salary", "[class*=salary]"]),
      job_description_text: textOf([".job-sec-text", ".job-description", ".content", "[class*=job-detail]"]),
      missing_field_codes: [],
    };
    if (!value.job_title) value.missing_field_codes.push("job_title");
    if (!value.location_text) value.missing_field_codes.push("job_location");
    if (!value.salary_text) value.missing_field_codes.push("job_salary");
    if (!value.job_description_text) value.missing_field_codes.push("job_description");
    return value;
  }

  function digest(value) {
    // 这里只做本机去重指纹；服务端仍会重新计算摘要，不能信任客户端摘要。
    return [value.platform, value.source_url, value.job_title, value.company_name, value.job_description_text].join("|");
  }

  function notify(message, failed) {
    let node = document.querySelector("#purslyx-capture-status");
    if (!node) {
      node = document.createElement("div");
      node.id = "purslyx-capture-status";
      node.style.cssText = "position:fixed;right:18px;bottom:18px;z-index:2147483647;padding:10px 14px;border-radius:10px;font:14px sans-serif;color:#16324f;background:#e7f5ed;box-shadow:0 4px 18px #16324f33";
      document.body.appendChild(node);
    }
    node.style.background = failed ? "#fff0ec" : "#e7f5ed";
    node.textContent = `Purslyx：${message}`;
    window.setTimeout(() => node.remove(), 4800);
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
        onerror: () => reject(new Error("无法连接本地 Purslyx 服务")),
      });
    });
  }

  function readPending() {
    try { return JSON.parse(localStorage.getItem(DRAFT_KEY) || "[]"); } catch (_) { return []; }
  }

  function savePending(value) {
    const rows = readPending().filter((row) => row.fingerprint !== digest(value));
    rows.unshift({ fingerprint: digest(value), value, saved_at: new Date().toISOString() });
    localStorage.setItem(DRAFT_KEY, JSON.stringify(rows.slice(0, 10)));
  }

  function removePending(value) {
    const fingerprint = digest(value);
    const rows = readPending().filter((row) => row.fingerprint !== fingerprint);
    localStorage.setItem(DRAFT_KEY, JSON.stringify(rows));
  }

  async function upload(value, fromPending) {
    const fingerprint = digest(value);
    if (!value.job_description_text || (seen.has(fingerprint) && !fromPending)) return;
    const browserToken = localStorage.getItem(TOKEN_KEY);
    if (!browserToken) {
      savePending(value);
      notify("已保存在本机草稿，登录 Purslyx 后可继续同步", false);
      return;
    }
    seen.add(fingerprint);
    try {
      const draft = await request({
        method: "POST",
        path: "/api/v1/browser/job-drafts",
        headers: { Authorization: `Bearer ${browserToken}`, "Idempotency-Key": `capture-${fingerprint.slice(0, 80)}` },
        body: value,
      });
      removePending(value);
      notify(`已自动获取「${draft.job_title || "当前岗位"}」，请回 Purslyx 检查并确认入池`, false);
    } catch (error) {
      savePending(value);
      notify(`${error.message}；岗位已保存在本机，可稍后重试`, true);
    }
  }

  async function flushPending() {
    if (!localStorage.getItem(TOKEN_KEY)) return;
    for (const row of readPending()) {
      if (row && row.value) await upload(row.value, true);
    }
  }

  function scheduleCapture() {
    window.clearTimeout(scheduleCapture.timer);
    scheduleCapture.timer = window.setTimeout(() => upload(capturePage()), 700);
  }

  // 延迟 DOM、平台局部刷新和重复渲染都统一走去重后的自动获取，不要求用户点击获取按钮。
  const observer = new MutationObserver(scheduleCapture);
  observer.observe(document.documentElement, { childList: true, subtree: true });
  window.addEventListener("load", scheduleCapture, { once: true });
  window.addEventListener("purslyx-browser-token-updated", flushPending);
  scheduleCapture();
  flushPending();
})();
