import { flushPromises, mount, type VueWrapper } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useAuthStore } from "@/stores/auth";
import type { JsonMap } from "@/types";
import ReportView from "@/views/ReportView.vue";

const mocks = vi.hoisted(() => ({ api: vi.fn(), waitForTask: vi.fn() }));
vi.mock("@/services/api", () => ({ api: mocks.api, waitForTask: mocks.waitForTask, idempotencyKey: () => "retry-key", deleteWithImpact: vi.fn() }));
vi.mock("vue-router", () => ({ useRoute: () => ({ query: { analysis_id: "report-1" } }), useRouter: () => ({ replace: vi.fn() }) }));

const report = (overrides: JsonMap = {}): JsonMap => ({ id: "report-1", status: "available", job_category: "engineering", ability_score: null, evidence_coverage: null,
  report: { overall_advice: { status: "needs_more_information", text: "请补充相关经历" }, dimensions: [], conditions: [] }, ...overrides });
const wrappers: VueWrapper[] = [];
function render(role: "seeker" | "recruiter" = "seeker") {
  const pinia = createPinia(); setActivePinia(pinia);
  useAuthStore().account = { id: "a1", registration_role: role, email: "user@example.test", admin_permissions: [] };
  const wrapper = mount(ReportView, { global: { plugins: [pinia], stubs: {
    AppShell: { template: "<main><slot /></main>" }, PageHeader: { template: "<header><slot /></header>" },
    AsyncState: { props: ["error"], template: "<p>{{ error }}</p>" }, RouterLink: { template: "<a><slot /></a>" },
  } } });
  wrappers.push(wrapper); return wrapper;
}
beforeEach(() => { mocks.api.mockReset(); mocks.waitForTask.mockReset(); mocks.api.mockResolvedValue(report()); });
afterEach(() => { wrappers.splice(0).forEach((wrapper) => wrapper.unmount()); });

describe("匹配报告可信展示", () => {
  it("未知分数与覆盖率不显示为零，岗位类别使用中文", async () => {
    const wrapper = render(); await flushPromises();
    expect(wrapper.get(".score-copy strong").text()).toBe("待补充");
    expect(wrapper.get(".report-metrics").text()).toContain("—");
    expect(wrapper.get(".report-metrics").text()).toContain("技术研发");
    expect(wrapper.get(".report-metrics").text()).not.toContain("engineering");
    expect(wrapper.text()).toContain("待补充资料");
  });

  it("有明确差距的真实零分仍然显示零", async () => {
    mocks.api.mockResolvedValue(report({ ability_score: 0, evidence_coverage: 1 }));
    const wrapper = render(); await flushPromises();
    expect(wrapper.get(".score-copy strong").text()).toBe("0");
    expect(wrapper.get(".report-metrics").text()).toContain("100%");
  });

  it("条件冲突有中文警示，存在引用时仍展示逐条判断理由", async () => {
    mocks.api.mockResolvedValue(report({ report: { conditions: [{ condition: "location", status: "conflicted", explanation: "地点不符合必须条件" }],
      dimensions: [{ key: "technical", label: "技术能力", score: 50, requirements: [{ requirement_id: "req-1", status: "partially_supported", job_quote: "熟悉 React", explanation: "可迁移能力已有依据，专用经历待补充", evidence: [{ segment_key: "s1", quote: "负责 Vue 组件交付" }] }] }] } }));
    const wrapper = render(); await flushPromises();
    expect(wrapper.get(".condition-row .tag").text()).toBe("冲突");
    expect(wrapper.get(".condition-row .tag").classes()).toContain("attention");
    expect(wrapper.get(".requirement").text()).toContain("专用经历待补充");
  });

  it("未完成任务不显示报告已生成或开放练习入口", async () => {
    mocks.api.mockResolvedValue(report({ status: "running", report: null, task: { id: "t1", status: "running", request_id: "original-request" } }));
    const wrapper = render(); await flushPromises();
    expect(wrapper.text()).toContain("岗位报告正在生成");
    expect(wrapper.text()).toContain("original-request");
    expect(wrapper.text()).not.toContain("报告已生成");
    expect(wrapper.text()).not.toContain("开始面试练习");
  });

  it("重试恢复原分析任务后读取报告", async () => {
    let retried = false;
    mocks.api.mockImplementation(async (path: string, options?: JsonMap) => {
      if (path === "/api/v1/tasks/t1/retry" && options?.method === "POST") { retried = true; return { task: { id: "t1", status: "queued" } }; }
      return retried ? report({ ability_score: 80, evidence_coverage: 1 })
        : report({ status: "failed", report: null, task: { id: "t1", retryable: true, request_id: "original-request", failure: { message: "模型暂时不可用" } } });
    });
    mocks.waitForTask.mockResolvedValue({ id: "t1", status: "succeeded" });
    const wrapper = render(); await flushPromises();
    await wrapper.findAll("button").find((button) => button.text() === "重试分析")!.trigger("click"); await flushPromises();
    expect(mocks.api).toHaveBeenCalledWith("/api/v1/tasks/t1/retry", expect.objectContaining({ method: "POST", idempotencyKey: "retry-key" }));
    expect(wrapper.get(".score-copy strong").text()).toBe("80");
  });

  it("招聘报告不出现求职改写和面试入口", async () => {
    const wrapper = render("recruiter"); await flushPromises();
    expect(wrapper.text()).not.toContain("开始面试练习");
    expect(wrapper.text()).not.toContain("补充事实与改写");
  });
});
