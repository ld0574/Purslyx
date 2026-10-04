import { flushPromises, mount, type VueWrapper } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { JsonMap } from "@/types";
import InterviewView from "@/views/InterviewView.vue";

const mocks = vi.hoisted(() => ({ api: vi.fn(), waitForTask: vi.fn(), route: { query: {} as JsonMap } }));
vi.mock("@/services/api", () => ({ api: mocks.api, waitForTask: mocks.waitForTask, idempotencyKey: () => "test-key" }));
vi.mock("vue-router", () => ({ useRoute: () => mocks.route }));

const summary = () => ({
  id: "summary-1", completion_type: "full", content: {
    rubric_version: "interview-rubric-v1", practice_index: 70, answered_main_count: 3, answered_followup_count: 1,
    evaluation_dimensions: { relevance: { score: 83 }, specificity: { score: 83 }, ownership: { score: 67 }, outcome_evidence: { score: 50 }, communication: { score: 67 } },
    next_practice_focus: { label: "结果证据力度", suggestion: "补充可核对的指标或验证方式" },
    content: { summary: "回答贴合岗位，结果证据仍待加强。", strengths: ["个人职责清楚"], gaps: ["缺少验证过程"], next_steps: ["下次练习补充性能记录"],
      star_assessment: { situation: { status: "strong", feedback: "背景已经交代清楚" } } },
  },
});

const view = (overrides: JsonMap = {}): JsonMap => ({ id: "i1", title: "第一场练习", revision: 2, status: "completed", questions: [], summary: summary(), ...overrides });
const pool = (overrides: JsonMap = {}): JsonMap => ({ id: "p1", job_title: "前端工程师", resume_version_id: "new-resume", latest_analysis: { id: "new-analysis", status: "available", resume_version_id: "frozen-resume" }, ...overrides });
let wrappers: VueWrapper[] = [];
function render() {
  const wrapper = mount(InterviewView, { global: { stubs: {
    AppShell: { template: "<main><slot /></main>" }, PageHeader: { template: "<header><slot /></header>" },
    AsyncState: { props: ["error"], template: "<div>{{ error }}</div>" }, RouterLink: { template: "<a><slot /></a>" },
  } } });
  wrappers.push(wrapper);
  return wrapper;
}

beforeEach(() => {
  mocks.route.query = {};
  mocks.api.mockReset(); mocks.waitForTask.mockReset();
  mocks.api.mockImplementation(async (path: string) => {
    if (path.startsWith("/api/v1/interviews?")) return { items: [{ id: "i1", title: "第一场练习", status: "completed" }] };
    if (path.startsWith("/api/v1/job-pool/items?")) return { items: [pool()] };
    if (path === "/api/v1/interviews/i1") return view();
    return {};
  });
});
afterEach(() => { wrappers.forEach((wrapper) => wrapper.unmount()); wrappers = []; vi.useRealTimers(); });

describe("面试核心流程", () => {
  it("显示嵌套总结、五维指数、整场 STAR 诊断和最弱维度建议", async () => {
    const wrapper = render(); await flushPromises();
    expect(wrapper.text()).toContain("练习表现指数 70");
    expect(wrapper.text()).toContain("下次练习补充性能记录");
    expect(wrapper.text()).toContain("背景已经交代清楚");
    expect(wrapper.text()).toContain("个人职责清楚");
    expect(wrapper.text()).toContain("已回答 3/3 道主问题，1 道追问");
    expect(wrapper.text()).toContain("下次重点：结果证据力度");
  });

  it("保留旧版与提前结束的文字反馈，说明不计入指数", async () => {
    mocks.api.mockImplementation(async (path: string) => {
      if (path.startsWith("/api/v1/interviews?")) return { items: [{ id: "i1" }] };
      if (path.startsWith("/api/v1/job-pool/items?")) return { items: [] };
      return view({ summary: { completion_type: "full", content: { summary: "旧版总结原文", next_steps: ["旧版建议"], star_assessment: { action: { status: "partial", feedback: "原有行动反馈" } } } } });
    });
    const wrapper = render(); await flushPromises();
    expect(wrapper.text()).toContain("旧版总结原文");
    expect(wrapper.text()).toContain("旧版建议");
    expect(wrapper.text()).toContain("不计入新练习趋势");
    expect(wrapper.text()).not.toContain("练习表现指数 ");
    mocks.api.mockImplementation(async (path: string) => path.startsWith("/api/v1/interviews?") ? { items: [{ id: "i1" }] }
      : path.startsWith("/api/v1/job-pool/items?") ? { items: [] }
        : view({ status: "ended_early", summary: { completion_type: "early", content: { content: { summary: "提前结束的反馈" } } } }));
    const early = render(); await flushPromises();
    expect(early.text()).toContain("提前结束仅保留实际反馈，不生成练习指数");
  });

  it("排除未完成报告，并用历史报告的冻结简历发起练习", async () => {
    mocks.route.query = { analysis_id: "old-analysis" };
    mocks.api.mockImplementation(async (path: string, options?: JsonMap) => {
      if (path.startsWith("/api/v1/interviews?")) return { items: [] };
      if (path.startsWith("/api/v1/job-pool/items?")) return { items: [pool(), pool({ id: "p2", job_title: "失败岗位", latest_analysis: { id: "failed-analysis", status: "failed" } })] };
      if (path === "/api/v1/analyses/old-analysis") return { id: "old-analysis", status: "available", job_pool_item_id: "p1", input_versions: [{ type: "resume", id: "old-frozen-resume" }] };
      if (path === "/api/v1/interviews" && options?.method === "POST") return { interview: view({ status: "awaiting_answer", summary: null }) };
      return view({ status: "awaiting_answer", summary: null });
    });
    const wrapper = render(); await flushPromises();
    expect(wrapper.findAll("#interview-start-form select option").map((item) => item.text())).not.toContain("失败岗位");
    await wrapper.get("#interview-start-form").trigger("submit"); await flushPromises();
    expect(mocks.api).toHaveBeenCalledWith("/api/v1/interviews", expect.objectContaining({ body: expect.objectContaining({
      analysis_id: "old-analysis", resume_document_version_id: "old-frozen-resume",
    }) }));
  });

  it("反馈任务失败后恢复已保存回答和重试入口", async () => {
    let detailReads = 0;
    const question = { id: "q1", main_no: 1, question_type: "main", question_text: "请说明你的贡献", status: "awaiting_answer" };
    mocks.api.mockImplementation(async (path: string, options?: JsonMap) => {
      if (path.startsWith("/api/v1/interviews?")) return { items: [{ id: "i1" }] };
      if (path.startsWith("/api/v1/job-pool/items?")) return { items: [] };
      if (path.endsWith("/answers") && options) return { interview: view({ status: "processing", summary: null }), task: { id: "t1", status: "queued" } };
      detailReads += 1;
      return detailReads === 1 ? view({ status: "awaiting_answer", current_question_id: "q1", questions: [question], summary: null })
        : view({ status: "feedback_failed", questions: [{ ...question, status: "answered", answer: { answer_text: "我负责接口优化" } }], summary: null, task: { id: "t1", retryable: true, status: "failed" } });
    });
    mocks.waitForTask.mockRejectedValue(new Error("模型暂时不可用（请求 ID：original-request）"));
    const wrapper = render(); await flushPromises();
    await wrapper.get("#interview-answer").setValue("我负责接口优化");
    await wrapper.get("#answer-form").trigger("submit"); await flushPromises();
    expect(wrapper.text()).toContain("original-request");
    expect(wrapper.text()).toContain("我负责接口优化");
    expect(wrapper.findAll("button").some((button) => button.text() === "重试生成")).toBe(true);
    expect(wrapper.find("#answer-form").exists()).toBe(false);
  });

  it("旧会话的迟到轮询不会覆盖新会话或清空新回答", async () => {
    vi.useFakeTimers();
    let resolvePoll: (value: JsonMap) => void = () => {};
    let reads = 0;
    mocks.api.mockImplementation(async (path: string) => {
      if (path.startsWith("/api/v1/interviews?")) return { items: [{ id: "i1", title: "第一场练习" }, { id: "i2", title: "第二场练习" }] };
      if (path.startsWith("/api/v1/job-pool/items?")) return { items: [] };
      if (path === "/api/v1/interviews/i2") return view({ id: "i2", title: "第二场练习", status: "awaiting_answer", summary: null, current_question_id: "q2", questions: [{ id: "q2", main_no: 1, question_text: "第二场问题" }] });
      reads += 1;
      if (reads === 1) return view({ status: "processing", summary: null });
      return new Promise<JsonMap>((resolve) => { resolvePoll = resolve; });
    });
    const wrapper = render(); await flushPromises();
    await vi.advanceTimersByTimeAsync(2000);
    const second = wrapper.findAll(".interview-item").find((button) => button.text().includes("第二场练习"))!;
    await second.trigger("click"); await flushPromises();
    await wrapper.get("#interview-answer").setValue("新会话未提交的草稿");
    resolvePoll(view({ status: "completed" })); await flushPromises();
    expect(wrapper.get("#interview-answer").element).toHaveProperty("value", "新会话未提交的草稿");
    expect(wrapper.find(".question").text()).toContain("第二场问题");
  });
});
