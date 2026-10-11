import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import AutoMatchingDialog from "@/components/AutoMatchingDialog.vue";
import DailyRecommendations from "@/components/DailyRecommendations.vue";

const apiMock = vi.hoisted(() => vi.fn());
vi.mock("@/services/api", () => ({ api: apiMock, idempotencyKey: () => "automatic-test-key" }));
vi.mock("vue-router", () => ({ useRouter: () => ({ push: vi.fn() }) }));
const stubs = { RouterLink: { props: ["to"], template: "<a><slot /></a>" }, AdminDialog: { props: ["open"], template: "<div v-if='open' role='dialog'><slot /></div>" } };
const policy = { revision: 0, enabled: false, valid: false, daily_limit: 10, preference_ids: [] };
const recommendations = {
  automation: { ...policy, revision: 1, enabled: true, valid: true, today_scheduled: 2 },
  progress: { captured: 2, completed: 2, active: 0, waiting: 1, failed: 1 },
  waiting_reasons: [{ code: "USAGE_INSUFFICIENT", count: 1, message: "分析次数不足，补充后自动继续" }],
  counts: { total: 2, tiers: { priority: 1, verify: 1 } },
  page: { has_more: false },
  items: [
    { id: "job-a", job_title: "测试前端岗位", company_name: "合成公司", analysis_id: "report-a", source_url: "https://www.zhipin.com/job_detail/test.html", apply_action: { available: true, click_token: "signed-token" }, recommendation: { tier: "priority", label: "优先投", reason: "已确认经历较贴合", ability_score: 85, evidence_coverage: 0.8, strengths: [{ requirement: "React 项目", explanation: "已有真实依据", evidence: "负责 React 项目交付", segment_key: "segment-1" }], risks: ["部分岗位要求需要确认"], verification_items: ["请确认办公方式"] } },
    { id: "job-b", job_title: "待核实岗位", analysis_id: "report-b", recommendation: { tier: "verify", label: "先核实", reason: "依据不足", ability_score: null, evidence_coverage: 0.3 } },
  ],
};

beforeEach(() => { apiMock.mockReset(); });
afterEach(() => { vi.restoreAllMocks(); vi.useRealTimers(); });

describe("跨日待投机会", () => {
  it("精简列表展示中文分类，原文和风险放入详情，保留等待和失败入口", async () => {
    apiMock.mockResolvedValue(recommendations);
    const wrapper = mount(DailyRecommendations, { global: { stubs } });
    await flushPromises();
    expect(wrapper.findAll(".recommendation-item")).toHaveLength(2);
    expect(wrapper.text()).toContain("优先投");
    expect(wrapper.text()).toContain("待投机会");
    expect(wrapper.text()).not.toContain("原文依据：负责 React 项目交付");
    expect(wrapper.text()).not.toContain("风险与边界");
    expect(wrapper.text()).toContain("未披露");
    expect(wrapper.text()).toContain("分析次数不足");
    expect(wrapper.text()).toContain("失败 1 个");
    expect(wrapper.text()).toContain("去投递点击不等于外部平台提交成功");
    expect(apiMock).toHaveBeenCalledTimes(1);
    apiMock.mockResolvedValueOnce({ ...recommendations, items: [recommendations.items[1]] });
    await wrapper.find('[aria-label="投递建议筛选"]').findAll("button")[3].trigger("click");
    await flushPromises();
    expect(apiMock).toHaveBeenLastCalledWith("/api/v1/job-pool/opportunities?view=unhandled&limit=20&tier=verify");
    expect(wrapper.findAll(".recommendation-item")).toHaveLength(1);
    expect(wrapper.find(".recommendation-item").text()).toContain("待核实岗位");
    wrapper.unmount();
  });

  it("空状态提供设置、抓取指引和岗位池，不擅自开启扣次", async () => {
    apiMock.mockResolvedValue({ automation: policy, progress: {}, items: [] });
    const wrapper = mount(DailyRecommendations, { global: { stubs } });
    await flushPromises();
    expect(wrapper.text()).toContain("近 30 天暂无未处理机会");
    expect(wrapper.text()).toContain("设置自动处理");
    expect(wrapper.text()).toContain("查看抓取指引");
    expect(apiMock.mock.calls.every(call => !call[1]?.method)).toBe(true);
    wrapper.unmount();
  });

  it("读取失败显示请求 ID", async () => {
    apiMock.mockRejectedValue(new Error("读取失败（请求 ID：trace-a）"));
    const wrapper = mount(DailyRecommendations, { global: { stubs } });
    await flushPromises();
    expect(wrapper.find('[role="alert"]').text()).toContain("trace-a");
    wrapper.unmount();
  });

  it("处理中定时刷新，离开后不再轮询", async () => {
    vi.useFakeTimers();
    apiMock.mockResolvedValue({ ...recommendations, progress: { active: 1, waiting: 0 } });
    const wrapper = mount(DailyRecommendations, { global: { stubs } });
    await flushPromises();
    await vi.advanceTimersByTimeAsync(10_000);
    expect(apiMock).toHaveBeenCalledTimes(2);
    wrapper.unmount();
    await vi.advanceTimersByTimeAsync(20_000);
    expect(apiMock).toHaveBeenCalledTimes(2);
  });

  it("已开启且空闲时仍定期发现新的抓取结果", async () => {
    vi.useFakeTimers();
    apiMock.mockResolvedValueOnce({ ...recommendations, progress: { active: 0, waiting: 0 }, items: [] });
    apiMock.mockResolvedValueOnce(recommendations);
    const wrapper = mount(DailyRecommendations, { global: { stubs } });
    await flushPromises();
    expect(wrapper.findAll(".recommendation-item")).toHaveLength(0);
    await vi.advanceTimersByTimeAsync(29_999);
    expect(apiMock).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(apiMock).toHaveBeenCalledTimes(2);
    expect(wrapper.findAll(".recommendation-item")).toHaveLength(2);
    wrapper.unmount();
  });

  it("自动领取或补次改变余额时刷新左侧用量，不新增首页额度卡片", async () => {
    const refreshUsage = vi.fn().mockResolvedValue(undefined);
    apiMock.mockResolvedValueOnce({ ...recommendations, automation: { ...recommendations.automation, available_analysis: 10 } });
    const wrapper = mount(DailyRecommendations, { global: { stubs, provide: { refreshUsage } } });
    await flushPromises();
    expect(refreshUsage).not.toHaveBeenCalled();
    apiMock.mockResolvedValueOnce({ ...recommendations, automation: { ...recommendations.automation, available_analysis: 9 } });
    await wrapper.findAll(".daily-recommendations-head button")[0].trigger("click");
    await flushPromises();
    expect(refreshUsage).toHaveBeenCalledTimes(1);
    expect(wrapper.text()).toContain("余额在左侧用量入口查看");
    expect(wrapper.findAll(".usage-card")).toHaveLength(0);
    wrapper.unmount();
  });

  it("迟到响应不能覆盖新刷新的建议", async () => {
    let resolveFirst: (value: unknown) => void = () => {};
    apiMock.mockImplementationOnce(() => new Promise(resolve => { resolveFirst = resolve; }));
    apiMock.mockResolvedValueOnce(recommendations);
    const wrapper = mount(DailyRecommendations, { global: { stubs } });
    await wrapper.findAll(".daily-recommendations-head button")[0].trigger("click");
    await flushPromises();
    resolveFirst({ automation: policy, progress: {}, items: [] });
    await flushPromises();
    expect(wrapper.findAll(".recommendation-item")).toHaveLength(2);
    wrapper.unmount();
  });

  it("只有用户点去投递才记录点击并打开原岗位", async () => {
    apiMock.mockResolvedValueOnce({ ...recommendations, progress: {} });
    const destination = { opener: {}, location: { replace: vi.fn() }, close: vi.fn() };
    vi.spyOn(window, "open").mockReturnValue(destination as unknown as Window);
    apiMock.mockResolvedValueOnce({ status: 303 });
    const wrapper = mount(DailyRecommendations, { global: { stubs } });
    await flushPromises();
    expect(window.open).not.toHaveBeenCalled();
    await wrapper.findAll(".recommendation-item button")[2].trigger("click");
    await flushPromises();
    expect(apiMock).toHaveBeenLastCalledWith("/api/v1/job-pool/items/job-a/go-to-apply", expect.objectContaining({ method: "POST", body: { click_token: "signed-token" }, redirect: "manual" }));
    expect(destination.location.replace).toHaveBeenCalledWith(recommendations.items[0].source_url);
    expect(wrapper.text()).toContain("是否提交投递由你在招聘平台确认");
    wrapper.unmount();
  });
});

describe("一次性自动处理授权", () => {
  function mockOptions() {
    apiMock.mockImplementation((path: string) => {
      if (path === "/api/v1/job-pool/automation") return Promise.resolve(policy);
      if (path.includes("/documents")) return Promise.resolve({ items: [{ id: "resume-1", title: "测试简历", subject_type: "self_resume", latest_version: { version_no: 2 } }, { id: "draft", title: "未确认草稿", subject_type: "self_resume" }] });
      return Promise.resolve({ items: [{ id: "preference-1", display_name: "前端期望", context: "self", version: { id: "p-v1" } }] });
    });
  }

  it("默认不开启，必须勾选扣次确认后才能保存", async () => {
    mockOptions();
    const wrapper = mount(AutoMatchingDialog, { props: { open: true }, global: { stubs } });
    await flushPromises();
    expect(wrapper.findAll('input[type="radio"]')).toHaveLength(1);
    expect(wrapper.find('button[type="submit"]').attributes("disabled")).toBeDefined();
    expect(apiMock.mock.calls.every(call => !call[1]?.method)).toBe(true);
    await wrapper.find(".auto-consent-confirm input").setValue(true);
    apiMock.mockResolvedValueOnce({ ...policy, enabled: true, revision: 1 });
    await wrapper.find("form").trigger("submit");
    await flushPromises();
    expect(apiMock).toHaveBeenLastCalledWith("/api/v1/job-pool/automation", expect.objectContaining({ method: "PUT", body: { enabled: true, resume_document_id: "resume-1", preference_ids: ["preference-1"], daily_limit: 10, base_revision: 0, confirm_usage: true } }));
    expect(wrapper.emitted("saved")).toHaveLength(1);
  });

  it("失败重试使用同一幂等键，保留已选资料", async () => {
    mockOptions();
    const wrapper = mount(AutoMatchingDialog, { props: { open: true }, global: { stubs } });
    await flushPromises();
    await wrapper.find(".auto-consent-confirm input").setValue(true);
    apiMock.mockRejectedValueOnce(new Error("网络失败（请求 ID：trace-save）"));
    await wrapper.find("form").trigger("submit"); await flushPromises();
    expect(wrapper.text()).toContain("trace-save");
    apiMock.mockResolvedValueOnce({ enabled: true });
    await wrapper.find("form").trigger("submit"); await flushPromises();
    const writes = apiMock.mock.calls.filter(call => call[1]?.method === "PUT");
    expect(writes).toHaveLength(2);
    expect(writes[0][1]).toEqual(writes[1][1]);
  });

  it("设置读取失败时不能用空的修订号覆盖设置", async () => {
    apiMock.mockRejectedValue(new Error("设置读取失败"));
    const wrapper = mount(AutoMatchingDialog, { props: { open: true }, global: { stubs } });
    await flushPromises();
    await wrapper.find(".auto-consent input").setValue(false);
    expect(wrapper.find('button[type="submit"]').attributes("disabled")).toBeDefined();
    await wrapper.find("form").trigger("submit");
    expect(apiMock.mock.calls.every(call => !call[1]?.method)).toBe(true);
  });
});
