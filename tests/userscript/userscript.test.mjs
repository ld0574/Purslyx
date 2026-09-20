/** BOSS／猎聘用户脚本在隔离 DOM 中的行为测试。 */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";
import { webcrypto } from "node:crypto";

const SOURCE = readFileSync(
  new URL("../../src/userscript/purslyx-job-capture.user.js", import.meta.url),
  "utf8",
);
const TOKEN_KEY = "purslyx.browser-token.v1";
const DRAFT_KEY = "purslyx.pending-job-drafts.v1";

function createRuntime({
  url,
  texts = {},
  nodeTexts = {},
  token = null,
  requestStatus = 201,
  responseData = { id: "draft-1", job_title: "测试岗位", pool_path: "/app/seeker/pool?pool_item_id=pool-1", job_pool_item: { id: "pool-1" } },
} = {}) {
  const location = new URL(url);
  const storage = new Map(token ? [[TOKEN_KEY, token]] : []);
  const requests = [];
  const listeners = new Map();
  let timerCallback = null;
  let observerCallback = null;
  let panelExists = false;

  const statusText = { textContent: "" };
  const syncButton = { addEventListener() {} };
  const retryButton = { addEventListener() {} };
  const draftLink = {
    href: "",
    style: { display: "none" },
    removeAttribute(name) {
      if (name === "href") this.href = "";
    },
  };
  const panel = {
    id: "",
    style: {},
    innerHTML: "",
    querySelector(selector) {
      if (selector === "[data-status-text]") return statusText;
      if (selector === "[data-sync]") return syncButton;
      if (selector === "[data-retry]") return retryButton;
      if (selector === "[data-open-draft]") return draftLink;
      return null;
    },
  };
  const document = {
    documentElement: {},
    body: {
      appendChild(node) {
        assert.equal(node, panel);
        panelExists = true;
      },
    },
    querySelector(selector) {
      if (selector === "#purslyx-capture-status") return panelExists ? panel : null;
      const value = texts[selector];
      return value === undefined ? null : { innerText: value, textContent: value };
    },
    querySelectorAll(selector) {
      const values = nodeTexts[selector];
      if (values !== undefined) {
        return values.map((value) => ({ innerText: value, textContent: value }));
      }
      const value = texts[selector];
      return value === undefined ? [] : [{ innerText: value, textContent: value }];
    },
    createElement(tagName) {
      assert.equal(tagName, "aside");
      return panel;
    },
  };
  const localStorage = {
    getItem(key) {
      return storage.has(key) ? storage.get(key) : null;
    },
    setItem(key, value) {
      storage.set(key, String(value));
    },
  };
  class MutationObserver {
    constructor(callback) {
      observerCallback = callback;
    }

    observe() {}
  }
  class CustomEvent {
    constructor(type, options = {}) {
      this.type = type;
      this.detail = options.detail;
    }
  }
  const window = {
    addEventListener(type, callback) {
      const callbacks = listeners.get(type) || [];
      callbacks.push(callback);
      listeners.set(type, callbacks);
    },
    dispatchEvent(event) {
      for (const callback of listeners.get(event.type) || []) callback(event);
    },
    setTimeout(callback) {
      timerCallback = callback;
      return 1;
    },
    clearTimeout() {},
    setInterval() {
      return 2;
    },
    open() {
      return null;
    },
  };
  const context = {
    URL,
    console,
    crypto: webcrypto,
    CustomEvent,
    document,
    location,
    localStorage,
    MutationObserver,
    window,
    btoa(value) {
      return Buffer.from(value, "binary").toString("base64");
    },
    GM_xmlhttpRequest(options) {
      requests.push(options);
      if (requestStatus === 0) {
        options.onerror();
        return;
      }
      options.onload({
        status: requestStatus,
        responseText: JSON.stringify(
          requestStatus >= 200 && requestStatus < 300
            ? { data: responseData }
            : { error: { message: "模拟上传失败" } },
        ),
      });
    },
  };
  vm.runInNewContext(SOURCE, context, { filename: "purslyx-job-capture.user.js" });

  return {
    draftLink,
    localStorage,
    requests,
    statusText,
    async runCapture() {
      for (let attempt = 0; attempt < 5 && typeof timerCallback === "function"; attempt += 1) {
        const callback = timerCallback;
        timerCallback = null;
        await callback();
        await new Promise((resolve) => setTimeout(resolve, 0));
      }
    },
    async runScheduledCapture() {
      assert.equal(typeof timerCallback, "function");
      const callback = timerCallback;
      timerCallback = null;
      await callback();
      await new Promise((resolve) => setTimeout(resolve, 0));
    },
    async mutateAndCapture() {
      assert.equal(typeof observerCallback, "function");
      observerCallback();
      await this.runCapture();
    },
    async emit(type, event = { type }) {
      for (const callback of listeners.get(type) || []) await callback(event);
    },
    setToken(value) {
      localStorage.setItem(TOKEN_KEY, value);
    },
    setText(selector, value) {
      texts[selector] = value;
    },
  };
}

test("BOSS 详情页自动提取并直接写入匹配池", async () => {
  const runtime = createRuntime({
    url: "https://www.zhipin.com/job_detail/abc.html?ka=search_list_jname_1",
    token: "browser-token",
    texts: {
      "h1.job-name": "高级前端工程师",
      ".company-info .name": "示例科技",
      ".job-primary .text-desc": "杭州 · 现场办公",
      ".job-primary .salary": "20K-30K",
      ".job-sec-text": "负责 React 和 TypeScript 项目交付",
    },
  });
  await runtime.runCapture();

  assert.equal(runtime.requests.length, 1);
  const request = runtime.requests[0];
  const body = JSON.parse(request.data);
  assert.equal(request.url, "https://purslyx.com/api/v1/browser/job-drafts");
  assert.equal(request.headers.Authorization, "Bearer browser-token");
  assert.equal(body.platform, "boss");
  assert.equal(body.job_title, "高级前端工程师");
  assert.equal(body.work_mode, "onsite");
  assert.equal(body.job_description_text, "负责 React 和 TypeScript 项目交付");
  assert.match(request.headers["Idempotency-Key"], /^capture-/);
  assert.match(runtime.statusText.textContent, /直接写入匹配池/);
  assert.equal(runtime.draftLink.style.display, "inline-block");
});

test("猎聘详情页使用独立适配器并识别远程岗位", async () => {
  const runtime = createRuntime({
    url: "https://www.liepin.com/job/123456.shtml",
    token: "browser-token",
    texts: {
      "h1.job-title": "Python 后端工程师",
      ".company-name": "猎聘示例公司",
      ".job-properties .job-properties-item": "上海",
      ".job-properties": "支持 Remote 远程办公",
      ".job-salary": "25K-35K",
      ".job-description": "负责 Python API 与 PostgreSQL",
    },
  });
  await runtime.runCapture();
  const body = JSON.parse(runtime.requests[0].data);
  assert.equal(body.platform, "liepin");
  assert.equal(body.work_mode, "remote");
  assert.equal(body.company_name, "猎聘示例公司");
});

test("BOSS 多个正文节点会合并，避免只抓到职位描述的一半", async () => {
  const runtime = createRuntime({
    url: "https://www.zhipin.com/job_detail/multiple-sections.html",
    token: "browser-token",
    texts: {
      "h1.job-name": "全栈工程师",
      ".job-primary .text-desc": "杭州",
    },
    nodeTexts: {
      ".job-sec-text": ["职位描述：负责 Agent 产品开发", "职位要求：熟悉 Python、TypeScript 和 PostgreSQL"],
    },
  });
  await runtime.runCapture();
  const body = JSON.parse(runtime.requests[0].data);
  assert.match(body.job_description_text, /职位描述：负责 Agent 产品开发/);
  assert.match(body.job_description_text, /职位要求：熟悉 Python、TypeScript 和 PostgreSQL/);
});

test("岗位正文动态加载时不会先上传半截内容", async () => {
  const runtime = createRuntime({
    url: "https://www.zhipin.com/job_detail/progressive.html",
    token: "browser-token",
    texts: {
      "h1.job-name": "渐进加载岗位",
      ".job-primary .text-desc": "杭州",
      ".job-sec-text": "职位描述：首屏内容",
    },
  });
  await runtime.runScheduledCapture();
  assert.equal(runtime.requests.length, 0);
  runtime.setText(".job-sec-text", "职位描述：首屏内容\n职位要求：完整正文和任职条件");
  await runtime.mutateAndCapture();
  assert.equal(runtime.requests.length, 1);
  assert.match(JSON.parse(runtime.requests[0].data).job_description_text, /完整正文和任职条件/);
});

test("平台要求登录查看完整内容时不会保存半截正文", async () => {
  const runtime = createRuntime({
    url: "https://www.zhipin.com/job_detail/login-gated.html",
    token: "browser-token",
    texts: {
      "h1.job-name": "登录限制岗位",
      ".job-primary .text-desc": "杭州",
    },
    nodeTexts: {
      ".job-sec-text": ["职位描述：当前可见内容\n登录查看完整内容"],
    },
  });
  await runtime.runCapture();
  assert.equal(runtime.requests.length, 0);
  assert.match(runtime.statusText.textContent, /展开完整内容/);
});

test("升级后当前岗位的旧半截本机草稿会等待完整正文", async () => {
  const runtime = createRuntime({
    url: "https://www.zhipin.com/job_detail/legacy-pending.html",
    texts: {
      "h1.job-name": "旧草稿岗位",
      ".job-primary .text-desc": "杭州",
      ".job-sec-text": "旧版只抓到的半截正文",
    },
  });
  await runtime.runCapture();
  assert.equal(JSON.parse(runtime.localStorage.getItem(DRAFT_KEY)).length, 1);

  runtime.setToken("synced-token");
  await runtime.emit("purslyx-browser-token-updated");
  assert.equal(runtime.requests.length, 0);
  runtime.setText(".job-sec-text", "旧版只抓到的半截正文\n完整职位要求和任职条件");
  await runtime.mutateAndCapture();
  assert.equal(runtime.requests.length, 1);
  assert.match(JSON.parse(runtime.requests[0].data).job_description_text, /完整职位要求和任职条件/);
});

test("完整正文变化不会复用被截断的幂等键", async () => {
  const prefix = "共同开头".repeat(30);
  const runtime = createRuntime({
    url: "https://www.zhipin.com/job_detail/full-key.html",
    token: "browser-token",
    texts: {
      "h1.job-name": "完整指纹岗位",
      ".job-primary .text-desc": "杭州",
      ".job-sec-text": `${prefix}第一版`,
    },
  });
  await runtime.runCapture();
  runtime.setText(".job-sec-text", `${prefix}第二版`);
  await runtime.mutateAndCapture();
  assert.equal(runtime.requests.length, 2);
  assert.notEqual(
    runtime.requests[0].headers["Idempotency-Key"],
    runtime.requests[1].headers["Idempotency-Key"],
  );
});

test("未登录时保留本机草稿，登录后继续上传", async () => {
  const runtime = createRuntime({
    url: "https://www.zhipin.com/job_detail/pending.html",
    texts: {
      "h1.job-name": "待同步岗位",
      ".job-primary .text-desc": "杭州",
      ".job-sec-text": "负责 API 开发",
    },
  });
  await runtime.runCapture();
  assert.equal(runtime.requests.length, 0);
  const pending = JSON.parse(runtime.localStorage.getItem(DRAFT_KEY));
  assert.equal(pending.length, 1);
  assert.equal(pending[0].value.job_title, "待同步岗位");
  assert.match(runtime.statusText.textContent, /保存在本机草稿/);

  runtime.setToken("synced-token");
  await runtime.emit("purslyx-browser-token-updated");
  assert.equal(runtime.requests.length, 0);
  await runtime.mutateAndCapture();
  assert.equal(runtime.requests.length, 1);
  assert.deepEqual(JSON.parse(runtime.localStorage.getItem(DRAFT_KEY)), []);
});

test("同页同内容不会因 DOM 变化重复上传", async () => {
  const runtime = createRuntime({
    url: "https://www.zhipin.com/job_detail/dedupe.html",
    token: "browser-token",
    texts: {
      "h1.job-name": "去重岗位",
      ".job-primary .text-desc": "杭州",
      ".job-sec-text": "负责前端开发",
    },
  });
  await runtime.runCapture();
  await runtime.mutateAndCapture();
  await runtime.mutateAndCapture();
  assert.equal(runtime.requests.length, 1);
});

test("上传失败后保存草稿并允许重试", async () => {
  const runtime = createRuntime({
    url: "https://www.zhipin.com/job_detail/retry.html",
    token: "browser-token",
    requestStatus: 500,
    texts: {
      "h1.job-name": "可重试岗位",
      ".job-primary .text-desc": "杭州",
      ".job-sec-text": "负责后端开发",
    },
  });
  await runtime.runCapture();
  assert.equal(runtime.requests.length, 1);
  assert.equal(JSON.parse(runtime.localStorage.getItem(DRAFT_KEY)).length, 1);
  assert.match(runtime.statusText.textContent, /模拟上传失败/);
  assert.match(runtime.statusText.textContent, /稍后重试/);
});

test("非详情页和正文未稳定时不会调用上传接口", async () => {
  const unsupported = createRuntime({
    url: "https://www.zhipin.com/web/geek/job",
    token: "browser-token",
  });
  await unsupported.runCapture();
  assert.equal(unsupported.requests.length, 0);

  const unstable = createRuntime({
    url: "https://www.zhipin.com/job_detail/loading.html",
    token: "browser-token",
    texts: { "h1.job-name": "正在加载" },
  });
  await unstable.runCapture();
  assert.equal(unstable.requests.length, 0);
  assert.match(unstable.statusText.textContent, /继续等待岗位正文/);
});
