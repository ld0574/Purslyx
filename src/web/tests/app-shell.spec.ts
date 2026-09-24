import { flushPromises, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { createMemoryHistory, createRouter } from "vue-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

import AppShell from "@/components/AppShell.vue";
import { useAuthStore } from "@/stores/auth";

const apiMock = vi.hoisted(() => vi.fn());
vi.mock("@/services/api", () => ({ api: apiMock }));

describe("左侧额度入口", () => {
  beforeEach(() => { apiMock.mockReset(); });

  it("展示中文额度并链接到用量页面", async () => {
    apiMock.mockResolvedValue({ balances: [
      { feature: "analysis", available: 16, unit: "times" },
      { feature: "interview", available: 4, unit: "sessions" },
      { feature: "rewrite", available: 20, unit: "times" },
    ] });
    const pinia = createPinia();
    setActivePinia(pinia);
    const auth = useAuthStore();
    auth.account = { id: "account-1", email: "user@example.test", registration_role: "seeker", admin_permissions: [] };
    const router = createRouter({ history: createMemoryHistory(), routes: [
      { path: "/app/seeker/dashboard", component: { template: "<div />" } },
      { path: "/app/seeker/usage", component: { template: "<div />" } },
      { path: "/:pathMatch(.*)*", component: { template: "<div />" } },
    ] });
    await router.push("/app/seeker/dashboard");
    await router.isReady();

    const wrapper = mount(AppShell, { global: { plugins: [pinia, router], stubs: { SiteFooter: true } } });
    await flushPromises();

    expect(apiMock).toHaveBeenCalledWith("/api/v1/usage?entries_limit=1");
    expect(wrapper.find('.side-nav a[href="/app/seeker/usage"]').exists()).toBe(false);
    const usage = wrapper.find('.sidebar-usage[href="/app/seeker/usage"]');
    expect(usage.exists()).toBe(true);
    expect(usage.text()).toContain("分析16次");
    expect(usage.text()).toContain("面试4场");
    expect(usage.text()).toContain("改写20次");
  });
});
