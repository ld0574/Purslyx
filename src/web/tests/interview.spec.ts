import { flushPromises, mount, type VueWrapper } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { JsonMap } from "@/types";
import InterviewFeedback from "@/components/InterviewFeedback.vue";
import InterviewView from "@/views/InterviewView.vue";

const mocks = vi.hoisted(() => ({ api: vi.fn(), waitForTask: vi.fn(), route: { query: {} as JsonMap } }));
vi.mock("@/services/api", () => ({ api: mocks.api, waitForTask: mocks.waitForTask, idempotencyKey: () => "test-key" }));
vi.mock("vue-router", () => ({ useRoute: () => mocks.route }));

const summary = () => ({
  id: "summary-1", completion_type: "full", content: {
    rubric_version: "interview-rubric-v2", practice_index: 70, answered_main_count: 3, answered_followup_count: 1,
    evaluation_dimensions: { relevance: { score: 83 }, specificity: { score: 83 }, ownership: { score: 67 }, outcome_evidence: { score: 50 }, communication: { score: 67 } },
    next_practice_focus: { label: "结果证据力度", suggestion: "补充可核对的指标或验证方式" },
    content: { summary: "回答贴合岗位，结果证据仍待加强。", strengths: ["个人职责清楚"], gaps: ["缺少验证过程"], next_steps: ["下次练习补充性能记录"],
      star_assessment: { situation: { status: "strong", feedback: "背景已经交代清楚" } } },
  },
});

const view = (overrides: JsonMap = {}): JsonMap => ({ id: "i1", title: "第一场练习", rubric_version: "interview-rubric-v2", revision: 2, status: "completed", questions: [], summary: summary(), ...overrides });
const pool = (overrides: JsonMap = {}): JsonMap => ({ id: "p1", job_title: "前端工程师", resume_version_id: "new-resume", latest_analysis: { id: "new-analysis", status: "available", resume_version_id: "frozen-resume" }, ...overrides });
const feedback = (): JsonMap => ({ schema_version: "interview-feedback-v5", rubric_version: "interview-rubric-v2", question_kind: "reasoning", summary: "Ownership 仍需加强。请说明取舍依据。", evaluation_dimensions: Object.fromEntries(["relevance", "specificity", "ownership", "outcome_evidence", "communication"].map(key => [key, { status: "partial", feedback: "请说明对应依据。", evidence_quote: "Redis 的边界需验证", evidence_status: "verified" }])), priority_actions: ["请补充约束", "请说明验证方法", "不应默认出现的第三条"], star_assessment: null, answer_outline: [{ kind: "quote", label: "依据", text: "Redis 的边界需验证" }, { kind: "prompt", label: "取舍", text: "请补充待确认的方案依据。" }], knowledge_checks: [{ claim_quote: "Redis 的边界需验证", note: "需核实故障隔离边界", verification: "请说明验证方法" }] });
const question = (overrides: JsonMap = {}): JsonMap => ({ id: "q1", question_type: "main", question_kind: "reasoning", main_no: 1, question_text: "如何选择隔离方案？", status: "answered", answer: { answer_text: "Redis 的边界需验证", created_at: "2026-10-05T01:00:00Z" }, feedback: { content: feedback() }, practice_state: { eligible: true, remaining: 1, practice: null }, ...overrides });
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
    if (path.startsWith("/api/v1/interviews?")) return { items: [{ id: "i1", title: "第一场练习", status: "completed", rubric_version: "interview-rubric-v2" }] };
    if (path.startsWith("/api/v1/job-pool/items?")) return { items: [pool()] };
    if (path === "/api/v1/interviews/i1") return view();
    return {};
  });
});
afterEach(() => { wrappers.forEach((wrapper) => wrapper.unmount()); wrappers = []; vi.useRealTimers(); });

describe("面试核心流程", () => {
  it("显示嵌套总结、五维指数和最弱维度建议，不再强制整场 STAR", async () => {
    const wrapper = render(); await flushPromises();
    expect(wrapper.text()).toContain("练习表现指数 70");
    expect(wrapper.text()).toContain("下次练习补充性能记录");
    expect(wrapper.text()).not.toContain("背景已经交代清楚");
    expect(wrapper.text()).toContain("个人职责清楚");
    expect(wrapper.text()).toContain("已回答 3/3 道主问题，1 道追问");
    expect(wrapper.text()).toContain("下次重点：结果证据力度");
  });

  it("旧版链接只给新版入口、不暴露旧反馈；提前结束保留新版实际反馈", async () => {
    mocks.route.query = { interview_id: "i1" };
    mocks.api.mockImplementation(async (path: string) => {
      if (path.startsWith("/api/v1/interviews?")) return { items: [{ id: "i1" }] };
      if (path.startsWith("/api/v1/job-pool/items?")) return { items: [] };
      return view({ rubric_version: "interview-rubric-v1", summary: { completion_type: "full", content: { summary: "旧版总结原文", next_steps: ["旧版建议"], star_assessment: { action: { status: "partial", feedback: "原有行动反馈" } } } } });
    });
    const wrapper = render(); await flushPromises();
    expect(wrapper.text()).not.toContain("旧版总结原文");
    expect(wrapper.text()).not.toContain("旧版建议");
    expect(wrapper.text()).toContain("不纳入新练习趋势");
    expect(wrapper.text()).toContain("开始新版练习");
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
    await wrapper.findAll("button").find((button) => button.text() === "开始新练习")!.trigger("click"); await flushPromises();
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
      if (path.startsWith("/api/v1/interviews?")) return { items: [{ id: "i1", rubric_version: "interview-rubric-v2" }] };
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
      if (path.startsWith("/api/v1/interviews?")) return { items: [{ id: "i1", title: "第一场练习", rubric_version: "interview-rubric-v2" }, { id: "i2", title: "第二场练习", rubric_version: "interview-rubric-v2" }] };
      if (path.startsWith("/api/v1/job-pool/items?")) return { items: [] };
      if (path === "/api/v1/interviews/i2") return view({ id: "i2", title: "第二场练习", status: "awaiting_answer", summary: null, current_question_id: "q2", questions: [{ id: "q2", main_no: 1, question_text: "第二场问题" }] });
      reads += 1;
      if (reads === 1) return view({ status: "processing", summary: null });
      return new Promise<JsonMap>((resolve) => { resolvePoll = resolve; });
    });
    const wrapper = render(); await flushPromises();
    await vi.advanceTimersByTimeAsync(2000);
    await wrapper.findAll("button").find((button) => button.text() === "练习记录")!.trigger("click"); await flushPromises();
    const second = wrapper.findAll(".interview-item").find((button) => button.text().includes("第二场练习"))!;
    await second.trigger("click"); await flushPromises();
    await wrapper.get("#interview-answer").setValue("新会话未提交的草稿");
    resolvePoll(view({ status: "completed" })); await flushPromises();
    expect(wrapper.get("#interview-answer").element).toHaveProperty("value", "新会话未提交的草稿");
    expect(wrapper.find(".question").text()).toContain("第二场问题");
  });

  it("等待反馈使用中文状态，不暴露内部步骤枚举", async () => {
    mocks.api.mockImplementation(async (path: string, options?: JsonMap) => {
      if (path.startsWith("/api/v1/interviews?")) return { items: [{ id: "i1", rubric_version: "interview-rubric-v2" }] };
      if (path.startsWith("/api/v1/job-pool/items?")) return { items: [] };
      if (path.endsWith("/answers") && options) return { interview: view({ status: "processing", summary: null }), task: { id: "t1", status: "running", current_step: "running", task_type: "interview_feedback" } };
      return view({ status: "awaiting_answer", summary: null, current_question_id: "q1", questions: [question({ answer: null })] });
    });
    mocks.waitForTask.mockImplementation(() => new Promise(() => {}));
    const wrapper = render(); await flushPromises();
    await wrapper.get("#interview-answer").setValue("请用监控验证边界");
    await wrapper.get("#answer-form").trigger("submit"); await flushPromises();
    expect(wrapper.text()).toContain("执行中 · 正在生成评价");
    expect(wrapper.text()).not.toContain("running");
  });

  it("五维默认紧凑折叠、中文标签、最多两条动作，技术名称与专业核查保留", async () => {
    const wrapper = mount(InterviewFeedback, { props: { content: feedback() } }); wrappers.push(wrapper);
    expect(wrapper.findAll(".coaching-dimension")).toHaveLength(5);
    expect(wrapper.findAll(".coaching-dimension").every(part => !part.attributes("open"))).toBe(true);
    expect(wrapper.text()).not.toContain("Ownership");
    expect(wrapper.text()).toContain("Redis");
    expect(wrapper.findAll(".coaching-actions li")).toHaveLength(2);
    expect(wrapper.text()).toContain("不是权威事实校验");
    expect(wrapper.text()).not.toContain("STAR 结构诊断");
    wrapper.get(".coaching-dimension").element.setAttribute("open", "");
    expect(wrapper.get(".coaching-dimension").attributes("open")).toBe("");
  });

  it("只显示当前问题，不铺开未答题；刚才的反馈按回答时间而非追问位置排序", async () => {
    mocks.api.mockImplementation(async (path: string) => path.startsWith("/api/v1/interviews?") ? { items: [{ id: "i1", rubric_version: "interview-rubric-v2" }] }
      : path.startsWith("/api/v1/job-pool/items?") ? { items: [] }
        : view({ status: "awaiting_answer", summary: null, current_question_id: "q3", questions: [question(), question({ id: "q2", main_no: 2, answer: { answer_text: "最新正式回答", created_at: "2026-10-05T03:00:00Z" } }), question({ id: "q3", main_no: 3, question_text: "当前第三题", answer: null }), question({ id: "f1", question_type: "followup", answer: { answer_text: "之前追问", created_at: "2026-10-05T02:00:00Z" } }), question({ id: "future", question_text: "不应铺开的未来问题", answer: null })] }));
    const wrapper = render(); await flushPromises();
    expect(wrapper.text()).not.toContain("不应铺开的未来问题");
    expect(wrapper.text()).toContain("刚才的回答 · 第 2 题");
    expect(wrapper.findAll(".coaching-history-row details").every(part => !part.attributes("open"))).toBe(true);
  });

  it("重答对照如实显示下降或无基线，不覆盖原指数", async () => {
    let submitted = false;
    const record = { id: "practice1", status: "available", answer_text: "本次重答", feedback: feedback(), comparison: { baseline_available: false, dimensions: { ownership: { label: "个人贡献", before: { status: "strong", evidence_quote: "原回答依据" }, after: { status: "partial", evidence_quote: "新回答依据" }, change: "weaker" }, outcome_evidence: { label: "结果验证", before: null, after: { status: "partial" }, change: "unavailable" } } } };
    mocks.api.mockImplementation(async (path: string, options?: JsonMap) => {
      if (path.startsWith("/api/v1/interviews?")) return { items: [{ id: "i1", rubric_version: "interview-rubric-v2" }] };
      if (path.startsWith("/api/v1/job-pool/items?")) return { items: [] };
      if (path.endsWith("/practice") && options) { submitted = true; return { practice: record, task: { id: "pt1", status: "succeeded" } }; }
      return view({ questions: [question({ practice_state: { eligible: !submitted, remaining: submitted ? 0 : 1, practice: submitted ? record : null } })] });
    });
    mocks.waitForTask.mockResolvedValue({ status: "succeeded" });
    const wrapper = render(); await flushPromises();
    await wrapper.get("[data-practice-question='q1']").trigger("click");
    await wrapper.get("#practice-answer").setValue("本次重答");
    await wrapper.get("#practice-form").trigger("submit"); await flushPromises();
    expect(wrapper.text()).toContain("无有效基线，无法比较");
    expect(wrapper.text()).toContain("本次需加强");
    expect(wrapper.text()).toContain("练习表现指数 70");
    expect(wrapper.find("#practice-form").exists()).toBe(false);
    expect(mocks.api).toHaveBeenCalledWith("/api/v1/interviews/i1/questions/q1/practice", expect.objectContaining({ body: { answer_text: "本次重答" }, idempotencyKey: "test-key" }));
  });

  it("刷新恢复失败训练及原任务重试入口，不让修改已提交训练", async () => {
    mocks.route.query = { interview_id: "i1", practice_id: "practice1" };
    mocks.api.mockImplementation(async (path: string) => path.startsWith("/api/v1/interviews?") ? { items: [{ id: "i1", rubric_version: "interview-rubric-v2" }] }
      : path.startsWith("/api/v1/job-pool/items?") ? { items: [] }
        : view({ questions: [question({ practice_state: { eligible: false, remaining: 1, practice: { id: "practice1", status: "failed", answer_text: "已保存训练回答", task: { id: "pt1", retryable: true, request_id: "saved-practice-request" } } } })] }));
    const wrapper = render(); await flushPromises();
    expect(wrapper.text()).toContain("已保存训练回答");
    expect(wrapper.text()).toContain("saved-practice-request");
    expect(wrapper.findAll("button").some(button => button.text() === "重试原训练任务")).toBe(true);
    expect(wrapper.find("#practice-answer").exists()).toBe(false);
    expect(wrapper.text()).toContain("练习表现指数 70");
  });

  it("弹窗关闭后仍发现已提交训练，迟到提交不覆盖另一题的草稿", async () => {
    let resolvePost: (value: JsonMap) => void = () => {}; let submitted = false;
    mocks.api.mockImplementation(async (path: string, options?: JsonMap) => {
      if (path.startsWith("/api/v1/interviews?")) return { items: [{ id: "i1", rubric_version: "interview-rubric-v2" }] };
      if (path.startsWith("/api/v1/job-pool/items?")) return { items: [] };
      if (path.endsWith("/practice") && options) return new Promise(resolve => { resolvePost = resolve; });
      return view({ questions: [question({ practice_state: { eligible: !submitted, remaining: 1, practice: submitted ? { id: "practice1", status: "queued", answer_text: "第一题训练" } : null } }), question({ id: "q2", main_no: 2 })] });
    });
    const wrapper = render(); await flushPromises();
    await wrapper.get("[data-practice-question='q1']").trigger("click");
    await wrapper.get("#practice-answer").setValue("第一题训练");
    await wrapper.get("#practice-form").trigger("submit");
    expect(wrapper.text()).toContain("正在保存重答并生成评价");
    expect(wrapper.get("#practice-form button").attributes("disabled")).toBeDefined();
    await wrapper.get("button[aria-label='关闭弹窗']").trigger("click");
    await wrapper.get("[data-practice-question='q2']").trigger("click");
    await wrapper.get("#practice-answer").setValue("另一题的草稿");
    submitted = true; resolvePost({ practice: { id: "practice1" }, task: { id: "pt1", status: "queued" } }); await flushPromises();
    expect(wrapper.get("#practice-answer").element).toHaveProperty("value", "另一题的草稿");
    expect(wrapper.get("[data-practice-question='q1']").text()).toContain("恢复重答训练");
    await wrapper.get("button[aria-label='关闭弹窗']").trigger("click");
    await wrapper.get("[data-practice-question='q2']").trigger("click");
    expect(wrapper.get("#practice-answer").element).toHaveProperty("value", "另一题的草稿");
  });
});
