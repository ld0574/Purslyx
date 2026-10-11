import { flushPromises, mount, type VueWrapper } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import DailyRecommendations from "@/components/DailyRecommendations.vue";
import JobHandlingDialog from "@/components/JobHandlingDialog.vue";
import JobHandlingUndo from "@/components/JobHandlingUndo.vue";
import JobOpportunityDialog from "@/components/JobOpportunityDialog.vue";
import type { JsonMap } from "@/types";
import PoolView from "@/views/PoolView.vue";

const mocks = vi.hoisted(() => ({ api: vi.fn(), push: vi.fn() }));
vi.mock("@/services/api", () => ({ api: mocks.api, idempotencyKey: () => "stable-handling-key", deleteWithImpact: vi.fn() }));
vi.mock("vue-router", () => ({ useRouter: () => ({ push: mocks.push }), useRoute: () => ({ query: {} }) }));
const stubs = {
  RouterLink: { props: ["to"], template: "<a><slot /></a>" },
  AdminDialog: { props: ["open", "title"], template: "<div v-if='open' role='dialog'><h3>{{ title }}</h3><slot /></div>" },
  AppShell: { template: "<main><slot /></main>" }, PageHeader: { template: "<header><slot /></header>" },
  AsyncState: { props: ["error"], template: "<p>{{ error }}</p>" },
};
const pending = { status: "pending", label: "待投", revision: 0 };
const item = { id: "pool-old", analysis_id: "report-old", job_title: "旧版前端工程师", company_name: "测试公司", handling: pending,
  recommendation: { tier: "priority", label: "优先投", reason: "已有真实依据" }, job_conditions: { salary: "20–30K·14薪", location: "上海", work_mode: "remote" },
  completed_at: "2026-10-10T02:00:00Z", source_update: { updated: true, latest_pool_item_id: "pool-new" } };
const page = (items: JsonMap[] = [item], changes: JsonMap = {}): JsonMap => ({ items, counts: { total: items.length, tiers: { priority: items.length } }, automation: { enabled: true, valid: true }, progress: {}, page: { has_more: false }, range: { from: "2026-09-12", to: "2026-10-11" }, ...changes });
const wrappers: VueWrapper[] = [];
function render(component: any, props: JsonMap = {}) { const wrapper = mount(component, { props, global: { stubs } }); wrappers.push(wrapper); return wrapper; }
function button(wrapper: VueWrapper, label: string) { return wrapper.findAll("button").find(element => element.text() === label)!; }
beforeEach(() => { mocks.api.mockReset(); });
afterEach(() => { wrappers.splice(0).forEach(wrapper => wrapper.unmount()); vi.useRealTimers(); });

describe("待投机会筛选与分页", () => {
  it("默认近三十天未处理、服务端关键词和分类筛选、旧版标签与薪资原文", async () => {
    mocks.api.mockResolvedValue(page());
    const wrapper = render(DailyRecommendations); await flushPromises();
    expect(mocks.api).toHaveBeenCalledWith("/api/v1/job-pool/opportunities?view=unhandled&limit=20");
    expect(wrapper.text()).toContain("20–30K·14薪"); expect(wrapper.text()).toContain("远程办公");
    expect(wrapper.get(".recommendation-title .tag").text()).toBe("优先投（旧版）");
    await wrapper.get("#opportunity-search").setValue("测试公司");
    await wrapper.get(".opportunity-search").trigger("submit"); await flushPromises();
    expect(new URLSearchParams(mocks.api.mock.calls.at(-1)![0].split("?")[1]).get("search")).toBe("测试公司");
    await wrapper.get('[aria-label="处理视图"]').findAll("button")[1].trigger("click"); await flushPromises();
    expect(new URLSearchParams(mocks.api.mock.calls.at(-1)![0].split("?")[1]).get("view")).toBe("handled");
  });

  it("第二页自动刷新只提示更新，返回第一页才替换列表", async () => {
    vi.useFakeTimers();
    mocks.api.mockResolvedValueOnce(page([item], { page: { has_more: true, next_cursor: "page-2" } }));
    const wrapper = render(DailyRecommendations); await flushPromises();
    const second = { ...item, id: "second-row", job_title: "正在阅读的第二页岗位" };
    mocks.api.mockResolvedValueOnce(page([second]));
    await button(wrapper, "下一页").trigger("click"); await flushPromises();
    expect(mocks.api.mock.calls.at(-1)![0]).toContain("cursor=page-2");
    mocks.api.mockResolvedValueOnce(page([{ ...item, id: "new-row" }], { counts: { total: 3 } }));
    await vi.advanceTimersByTimeAsync(30_000); await flushPromises();
    expect(wrapper.get(".recommendation-item").text()).toContain("正在阅读的第二页岗位");
    expect(wrapper.text()).toContain("有新结果或处理状态更新");
    mocks.api.mockResolvedValueOnce(page([item]));
    await button(wrapper, "返回第一页刷新").trigger("click"); await flushPromises();
    expect(wrapper.text()).not.toContain("正在阅读的第二页岗位");
    expect(wrapper.text()).toContain("第 1 页");
  });

  it("翻页请求迟到不能覆盖已切换的筛选或页码", async () => {
    mocks.api.mockResolvedValueOnce(page([item], { page: { has_more: true, next_cursor: "page-2" } }));
    const wrapper = render(DailyRecommendations); await flushPromises();
    let resolvePage: (value: unknown) => void = () => {};
    mocks.api.mockImplementationOnce(() => new Promise(resolve => { resolvePage = resolve; }));
    await button(wrapper, "下一页").trigger("click");
    mocks.api.mockResolvedValueOnce(page([], { counts: { total: 0 } }));
    await wrapper.get('[aria-label="处理视图"]').findAll("button")[1].trigger("click"); await flushPromises();
    resolvePage(page([{ ...item, job_title: "过期第二页" }])); await flushPromises();
    expect(wrapper.text()).not.toContain("过期第二页");
    expect(wrapper.text()).toContain("第 1 页");
    expect(wrapper.get('[aria-label="处理视图"] button[aria-pressed="true"]').text()).toBe("已处理");
  });

  it("成功标记已投移出未处理清单，并提供撤销", async () => {
    mocks.api.mockResolvedValueOnce(page());
    const wrapper = render(DailyRecommendations); await flushPromises();
    await button(wrapper, "修改状态").trigger("click");
    const dialog = wrapper.findComponent(JobHandlingDialog);
    await dialog.get('input[value="applied"]').setValue(true);
    expect(dialog.get('button[type="submit"]').attributes("disabled")).toBeDefined();
    await dialog.get('input[type="checkbox"]').setValue(true);
    const applied = { status: "applied", label: "本人确认已投", revision: 1 };
    mocks.api.mockResolvedValueOnce({ handling: applied, previous_status: "pending" }).mockResolvedValueOnce(page([]));
    await dialog.get("form").trigger("submit"); await flushPromises();
    expect(wrapper.findAll(".recommendation-item")).toHaveLength(0);
    expect(wrapper.text()).toContain("撤销最近操作");
    mocks.api.mockResolvedValueOnce({ handling: { ...pending, revision: 2 } }).mockResolvedValueOnce(page());
    await button(wrapper, "撤销最近操作").trigger("click"); await flushPromises();
    expect(wrapper.findAll(".recommendation-item")).toHaveLength(1);
    const writes = mocks.api.mock.calls.filter(call => call[1]?.method === "PATCH");
    expect(writes[0][1].body).toEqual({ status: "applied", base_revision: 0, confirm_applied: true });
    expect(writes[1][1].body).toEqual({ status: "pending", base_revision: 1, confirm_applied: false });
  });
});

describe("处理状态与详情弹窗", () => {
  it("失败保留原状态与草稿，请求 ID 可见，重试使用同一幂等键", async () => {
    const wrapper = render(JobHandlingDialog, { open: true, item });
    await wrapper.get('input[value="dismissed"]').setValue(true);
    mocks.api.mockRejectedValueOnce(new Error("状态冲突（请求 ID：handling-trace）"));
    await wrapper.get("form").trigger("submit"); await flushPromises();
    expect(wrapper.get('[role="alert"]').text()).toContain("handling-trace");
    expect((wrapper.get('input[value="dismissed"]').element as HTMLInputElement).checked).toBe(true);
    expect(item.handling.status).toBe("pending");
    mocks.api.mockResolvedValueOnce({ handling: { status: "dismissed", label: "不感兴趣", revision: 1 }, previous_status: "pending" });
    await wrapper.get("form").trigger("submit"); await flushPromises();
    expect(mocks.api.mock.calls[0][1]).toEqual(mocks.api.mock.calls[1][1]);
    expect(wrapper.emitted("saved")).toHaveLength(1);
  });

  it("切换岗位后迟到的状态响应不能改新岗位", async () => {
    let resolve: (value: unknown) => void = () => {};
    mocks.api.mockImplementationOnce(() => new Promise(done => { resolve = done; }));
    const wrapper = render(JobHandlingDialog, { open: true, item });
    await wrapper.get('input[value="verifying"]').setValue(true); await wrapper.get("form").trigger("submit");
    await wrapper.setProps({ item: { ...item, id: "other-pool" } });
    resolve({ handling: { status: "verifying", revision: 1 } }); await flushPromises();
    expect(wrapper.emitted("saved")).toBeUndefined();
    expect((wrapper.get('input[value="pending"]').element as HTMLInputElement).checked).toBe(true);
  });

  it("编辑期间同一岗位刷新不偷换基线修订，其他设备修改仍返回冲突", async () => {
    const wrapper = render(JobHandlingDialog, { open: true, item });
    await wrapper.get('input[value="dismissed"]').setValue(true);
    await wrapper.setProps({ item: { ...item, handling: { status: "verifying", label: "待核实", revision: 2 } } });
    mocks.api.mockRejectedValueOnce(new Error("处理状态已修改，请刷新（请求 ID：revision-trace）"));
    await wrapper.get("form").trigger("submit"); await flushPromises();
    expect(mocks.api.mock.calls[0][1].body.base_revision).toBe(0);
    expect(wrapper.text()).toContain("revision-trace");
    expect(wrapper.emitted("saved")).toBeUndefined();
  });

  it("撤销使用独立修订号，遇到冲突不覆盖其他设备", async () => {
    const wrapper = render(JobHandlingUndo, { change: null });
    await wrapper.setProps({ change: { item_id: "pool-old", handling: { status: "dismissed", label: "不感兴趣", revision: 3 }, previous_status: "pending" } });
    mocks.api.mockRejectedValueOnce(new Error("其他设备已修改（请求 ID：undo-trace）"));
    await button(wrapper, "撤销最近操作").trigger("click"); await flushPromises();
    expect(mocks.api).toHaveBeenCalledWith("/api/v1/job-pool/items/pool-old/handling-status", expect.objectContaining({ body: { status: "pending", base_revision: 3, confirm_applied: false } }));
    expect(wrapper.text()).toContain("undo-trace"); expect(wrapper.emitted("undone")).toBeUndefined();
  });

  it("详情只显示报告冻结 JD 与依据，旧版推荐明确标识", async () => {
    mocks.api.mockImplementation(async (path: string) => path.includes("/analyses/") ? {
      id: "report-old", job_pool_item_id: "pool-old", status: "available", completed_at: item.completed_at,
      job_snapshot: { title: "冻结标题", company_name: "冻结公司", conditions: item.job_conditions,
        content: { job_fields: { source_text: "冻结的旧 JD 原文" } }, source_update: item.source_update },
      application_recommendation: { ...item.recommendation, strengths: [{ requirement: "React", evidence: "负责组件上线", explanation: "原文已确认" }], risks: ["部分条件需要核实"] },
      report: { dimensions: [{ key: "technical", label: "技术", requirements: [{ requirement_id: "r1", job_quote: "React", status: "supported", explanation: "原文已确认", evidence: [{ quote: "负责组件上线" }] }] }] },
    } : { ...item, job_title: "最新字段不应混入", job_content: { job_fields: { source_text: "新版 JD 正文不应混入" } } });
    const wrapper = render(JobOpportunityDialog, { open: true, itemId: item.id, analysisId: item.analysis_id }); await flushPromises();
    expect(wrapper.text()).toContain("冻结标题"); expect(wrapper.text()).toContain("冻结的旧 JD 原文");
    expect(wrapper.text()).not.toContain("新版 JD 正文不应混入"); expect(wrapper.text()).toContain("优先投（旧版）");
    expect(wrapper.text()).toContain("原文依据：负责组件上线"); expect(wrapper.get(".opportunity-report").attributes("open")).toBeUndefined();
  });

  it("报告缺少旧版条件时显示未披露，不借用当前岗位字段", async () => {
    mocks.api.mockResolvedValueOnce({ ...item, job_content: { job_fields: { source_text: "新版 JD 不应混入" } } })
      .mockResolvedValueOnce({ id: item.analysis_id, job_pool_item_id: item.id, status: "available",
        job_snapshot: { title: "旧版标题", conditions: {} }, application_recommendation: item.recommendation, report: {} });
    const wrapper = render(JobOpportunityDialog, { open: true, itemId: item.id, analysisId: item.analysis_id }); await flushPromises();
    expect(wrapper.get(".opportunity-conditions").text()).toContain("薪资：未披露");
    expect(wrapper.get(".opportunity-conditions").text()).not.toContain("20–30K");
    expect(wrapper.get(".opportunity-jd").text()).not.toContain("新版 JD 不应混入");
  });

  it("无有效报告不展示推荐，也不尝试展开损坏证据", async () => {
    mocks.api.mockResolvedValueOnce({ ...item, analysis_status: "failed", application_recommendation: item.recommendation })
      .mockResolvedValueOnce({ id: item.analysis_id, job_pool_item_id: item.id, status: "available", application_recommendation: null,
        report: { dimensions: [{ requirements: [{ evidence: [null] }] }] } });
    const wrapper = render(JobOpportunityDialog, { open: true, itemId: item.id, analysisId: item.analysis_id }); await flushPromises();
    expect(wrapper.find(".opportunity-report").exists()).toBe(false);
    expect(wrapper.text()).not.toContain("优先投");
    expect(wrapper.text()).toContain("查看任务");
  });

  it("已打开详情可以同步最新处理修订和匹配结果", async () => {
    mocks.api.mockResolvedValueOnce({ ...item, analysis_history: [], analysis_status: "running" });
    const wrapper = render(JobOpportunityDialog, { open: true, itemId: item.id }); await flushPromises();
    expect(wrapper.find(".opportunity-report").exists()).toBe(false);
    mocks.api.mockResolvedValueOnce({ ...item, handling: { status: "verifying", label: "待核实", revision: 2 }, analysis_history: [{ id: "report-ready", status: "available", readable: true }] })
      .mockResolvedValueOnce({ id: "report-ready", job_pool_item_id: item.id, status: "available", application_recommendation: item.recommendation,
        job_snapshot: { title: item.job_title, conditions: item.job_conditions, content: {} }, report: {} });
    await (wrapper.vm as unknown as { refresh: () => Promise<void> }).refresh(); await flushPromises();
    expect(wrapper.text()).toContain("待核实");
    expect(wrapper.find(".opportunity-report").exists()).toBe(true);
    await button(wrapper, "修改处理状态").trigger("click");
    const handlingDialog = wrapper.findComponent(JobHandlingDialog);
    expect(handlingDialog.props("item")?.handling.revision).toBe(2);
  });

  it("切换详情后忽略迟到读取，不再请求旧报告", async () => {
    let resolveOld: (value: unknown) => void = () => {};
    mocks.api.mockImplementationOnce(() => new Promise(resolve => { resolveOld = resolve; }));
    const wrapper = render(JobOpportunityDialog, { open: true, itemId: "old", analysisId: "old-report" });
    mocks.api.mockResolvedValueOnce({ id: "new", job_title: "新岗位", job_content: {} });
    await wrapper.setProps({ itemId: "new", analysisId: undefined }); await flushPromises();
    resolveOld({ id: "old", job_title: "旧岗位", analysis_history: [{ id: "old-report", status: "available" }] }); await flushPromises();
    expect(wrapper.text()).toContain("新岗位"); expect(wrapper.text()).not.toContain("旧岗位");
    expect(mocks.api.mock.calls.some(call => call[0] === "/api/v1/analyses/old-report")).toBe(false);
  });

  it("岗位池复用详情弹窗，新增表单只在点击后出现，并提供处理状态筛选", async () => {
    mocks.api.mockImplementation(async (path: string) => path.includes("/job-pool/items?") ? { items: [{ ...item, analysis_summary: {}, source_type: "manual" }], page: {} }
      : path === "/api/v1/job-pool/items/pool-old" ? { ...item, job_content: {} }
      : { items: [] });
    const wrapper = render(PoolView); await flushPromises();
    expect(wrapper.find("#pool-form").exists()).toBe(false);
    expect(wrapper.find(".pool-detail").exists()).toBe(false);
    await wrapper.get("#pool-handling").setValue("applied");
    await wrapper.get(".pool-filter-panel").trigger("submit"); await flushPromises();
    expect(mocks.api.mock.calls.some(call => call[0].includes("handling_status=applied"))).toBe(true);
    await button(wrapper, "手动新增岗位").trigger("click"); expect(wrapper.find("#pool-form").exists()).toBe(true);
    await button(wrapper, "详情").trigger("click"); await flushPromises();
    expect(wrapper.findComponent(JobOpportunityDialog).props("open")).toBe(true);
  });

  it("岗位池翻页期间切换筛选，迟到失败不能恢复旧游标或覆盖新列表", async () => {
    mocks.api.mockImplementation(async (path: string) => path.includes("/job-pool/items?")
      ? { items: [item], page: { has_more: true, next_cursor: "pool-page-2" } } : { items: [] });
    const wrapper = render(PoolView); await flushPromises();
    let rejectPage: (value: unknown) => void = () => {};
    mocks.api.mockImplementationOnce(() => new Promise((_, reject) => { rejectPage = reject; }));
    await button(wrapper, "下一页").trigger("click");
    await wrapper.get("#pool-handling").setValue("dismissed");
    mocks.api.mockResolvedValueOnce({ items: [{ ...item, job_title: "新筛选结果" }], page: {} });
    await wrapper.get(".pool-filter-panel").trigger("submit"); await flushPromises();
    rejectPage(new Error("过期请求的失败")); await flushPromises();
    expect(wrapper.text()).toContain("新筛选结果");
    expect(wrapper.text()).toContain("第 1 页");
    expect(wrapper.text()).not.toContain("过期请求的失败");
  });
});
