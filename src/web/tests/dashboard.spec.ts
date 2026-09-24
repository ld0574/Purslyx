import { flushPromises, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useAuthStore } from "@/stores/auth";
import DashboardView from "@/views/DashboardView.vue";

const apiMock = vi.hoisted(() => vi.fn());
vi.mock("@/services/api", () => ({ api: apiMock }));

const seekerDashboard = {
  registration_role: "seeker",
  counts: { confirmed_resumes: 2, job_pool_items: 8, completed_analyses: 5, active_tasks: 1 },
  balances: [
    { feature: "analysis", available: 16, unit: "times" },
    { feature: "interview", available: 4, unit: "sessions" },
    { feature: "rewrite", available: 20, unit: "times" },
  ],
  attention: { active_tasks: 1, confirmed_resumes: 2, active_preferences: 1, job_pool_items: 8, completed_analyses: 5, completed_interviews: 1 },
  activity_series: [{ date: "2026-09-24", captured_jobs: 3, completed_analyses: 2, apply_clicks: 1 }],
  practice_series: [{ date: "2026-09-24", sessions: 1, practice_index: 70 }],
  latest_practice_dimensions: { relevance: { score: 83 }, specificity: { score: 67 }, ownership: { score: 50 }, outcome_evidence: { score: 50 }, communication: { score: 100 } },
};

function mountDashboard(role: "seeker" | "recruiter") {
  const pinia = createPinia();
  setActivePinia(pinia);
  const auth = useAuthStore();
  auth.account = { id: "account-1", email: "user@example.test", registration_role: role, admin_permissions: [] };
  return mount(DashboardView, { global: { plugins: [pinia], stubs: {
    AppShell: { template: "<main><slot /></main>" },
    PageHeader: { template: "<header><slot /></header>" },
    AsyncState: { template: "<div />" },
    RouterLink: { props: ["to"], template: "<a><slot /></a>" },
  } } });
}

describe("工作台", () => {
  beforeEach(() => { apiMock.mockReset(); });

  it("使用中文业务指标、趋势、下一步和三个快捷入口", async () => {
    apiMock.mockResolvedValue(seekerDashboard);
    const wrapper = mountDashboard("seeker");
    await flushPromises();
    expect(apiMock).toHaveBeenCalledWith("/api/v1/dashboard?days=14");
    expect(wrapper.text()).toContain("岗位分析剩余");
    expect(wrapper.text()).toContain("去投递点击");
    expect(wrapper.text()).toContain("面试练习进步");
    expect(wrapper.text()).toContain("练习表现指数用于比较自己的变化");
    expect(wrapper.text()).not.toContain("最近分析");
    expect(wrapper.findAll(".shortcut-card")).toHaveLength(3);
  });

  it("可切换到近三十天趋势", async () => {
    apiMock.mockResolvedValue(seekerDashboard);
    const wrapper = mountDashboard("seeker");
    await flushPromises();
    await wrapper.find('select[aria-label="趋势周期"]').setValue("30");
    await flushPromises();
    expect(apiMock).toHaveBeenLastCalledWith("/api/v1/dashboard?days=30");
  });
});
