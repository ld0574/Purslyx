import { flushPromises, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useAuthStore } from "@/stores/auth";
import AdminJobPoolView from "@/views/admin/AdminJobPoolView.vue";
import AdminRolesView from "@/views/admin/AdminRolesView.vue";
import AdminUsageView from "@/views/admin/AdminUsageView.vue";
import AdminUsersView from "@/views/admin/AdminUsersView.vue";

const apiMock = vi.hoisted(() => vi.fn());
vi.mock("@/services/api", () => ({ api: apiMock, idempotencyKey: () => "test-key", deleteWithImpact: vi.fn() }));

function mountAdmin(component: object) {
  const pinia = createPinia();
  setActivePinia(pinia);
  const auth = useAuthStore();
  auth.account = { id: "admin-1", email: "admin@example.test", registration_role: "seeker", admin_permissions: [
    "admin.users.read", "admin.users.manage_status", "admin.roles.manage", "admin.usage.grant", "admin.job_pool.read", "admin.job_pool.manage",
  ] };
  return mount(component, { global: { plugins: [pinia], stubs: {
    AppShell: { template: "<main><slot /></main>" },
    PageHeader: { template: "<header><slot /></header>" },
    AsyncState: { template: "<div />" },
  } } });
}

describe("后台列表与弹窗", () => {
  beforeEach(() => { apiMock.mockReset(); });

  it("用户详情在弹窗里显示并可保存暂停状态", async () => {
    apiMock.mockImplementation(async (url: string) => {
      if (url.startsWith("/api/v1/admin/users?")) return { items: [{ id: "user-1", email: "user@example.test", status: "active", registration_role: "seeker" }], page: {} };
      if (url === "/api/v1/admin/roles") return { items: [] };
      if (url === "/api/v1/admin/users/user-1") return { account: { id: "user-1", email: "user@example.test", status: "active", registration_role: "seeker", revision: 1 }, usage: { balances: [] }, summary: { counts: {} }, admin_roles: [] };
      if (url === "/api/v1/admin/users/user-1/status") return { account: { id: "user-1", email: "user@example.test", status: "suspended", registration_role: "seeker", revision: 2 } };
      throw new Error(`unexpected ${url}`);
    });
    const wrapper = mountAdmin(AdminUsersView);
    await flushPromises();
    expect(wrapper.find(".admin-dialog[open]").exists()).toBe(false);
    await wrapper.find('[data-action="open-admin-user"]').trigger("click");
    await flushPromises();
    expect(wrapper.find(".admin-dialog[open]").exists()).toBe(true);
    await wrapper.find("#admin-user-status").setValue("suspended");
    await wrapper.find(".admin-modal-form").trigger("submit");
    await flushPromises();
    expect(apiMock).toHaveBeenCalledWith("/api/v1/admin/users/user-1/status", expect.objectContaining({ method: "PUT", body: expect.objectContaining({ status: "suspended" }) }));
  });

  it("角色列表按权限筛选，新增表单只在弹窗里打开", async () => {
    apiMock.mockResolvedValue({ items: [{ id: "role-1", name: "审核员", description: "审核账户", status: "active", permission_keys: ["admin.users.read"], member_count: 2 }], permission_catalog: [{ key: "admin.users.read", display_name: "查看用户", description: "查看账号" }] });
    const wrapper = mountAdmin(AdminRolesView);
    await flushPromises();
    expect(wrapper.find(".admin-dialog[open]").exists()).toBe(false);
    expect(wrapper.find(".role-chip").text()).toBe("查看用户");
    await wrapper.find("#role-permission").setValue("admin.users.read");
    expect(wrapper.findAll(".admin-row")).toHaveLength(1);
    await wrapper.find('[data-action="open-admin-role"]').trigger("click");
    await flushPromises();
    expect(wrapper.find(".admin-dialog[open] #admin-role-form").exists()).toBe(true);
    await wrapper.find(".admin-dialog[open] button[aria-label='关闭弹窗']").trigger("click");
    await flushPromises();
    expect(wrapper.find(".admin-dialog[open]").exists()).toBe(false);
  });

  it("次数管理用可搜索弹窗选择目标账号并限制招聘功能", async () => {
    apiMock.mockImplementation(async (url: string) => {
      if (url.startsWith("/api/v1/admin/usage-grants?")) return { items: [], page: {} };
      if (url.startsWith("/api/v1/admin/usage-targets?")) return { items: [{ id: "user-2", email: "recruiter@example.test", registration_role: "recruiter" }], page: {} };
      throw new Error(`unexpected ${url}`);
    });
    const wrapper = mountAdmin(AdminUsageView);
    await flushPromises();
    await wrapper.find('[data-action="open-admin-grant"]').trigger("click");
    await wrapper.find('[data-action="choose-grant-target"]').trigger("click");
    await flushPromises();
    expect(wrapper.findAll(".admin-dialog[open]")).toHaveLength(2);
    await wrapper.find("#target-search").setValue("recruiter");
    await wrapper.find(".admin-picker-filters").trigger("submit");
    await flushPromises();
    expect(apiMock).toHaveBeenCalledWith(expect.stringContaining("search=recruiter"));
    await wrapper.find(".admin-picker-row").trigger("click");
    await flushPromises();
    expect(wrapper.find('[data-action="choose-grant-target"]').text()).toContain("recruiter@example.test");
    expect(wrapper.findAll("#grant-feature option")).toHaveLength(1);
  });

  it("岗位详情在弹窗中展示完整 JD", async () => {
    apiMock.mockImplementation(async (url: string) => {
      if (url.startsWith("/api/v1/admin/job-pool/items?")) return { items: [{ id: "job-1", job_title: "前端工程师", source_type: "browser_capture", platform: "boss", in_pool: true, analysis_status: "available" }], page: {} };
      if (url === "/api/v1/admin/job-pool/items/job-1") return { id: "job-1", job_title: "前端工程师", company_name: "某公司", source_type: "browser_capture", platform: "boss", job_description_text: "完整岗位职责", in_pool: true, analysis_status: "available" };
      throw new Error(`unexpected ${url}`);
    });
    const wrapper = mountAdmin(AdminJobPoolView);
    await flushPromises();
    await wrapper.find(".admin-job-table button").trigger("click");
    await flushPromises();
    expect(wrapper.find(".admin-dialog[open] .admin-job-description").text()).toContain("完整岗位职责");
    await wrapper.find(".admin-dialog[open] button[aria-label='关闭弹窗']").trigger("click");
    await flushPromises();
    expect(wrapper.find(".admin-dialog[open]").exists()).toBe(false);
  });
});
