import { flushPromises, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useAuthStore } from "@/stores/auth";
import MaterialsView from "@/views/MaterialsView.vue";

const apiMock = vi.hoisted(() => vi.fn());
const routerPushMock = vi.hoisted(() => vi.fn());
vi.mock("@/services/api", () => ({
  api: apiMock,
  deleteWithImpact: vi.fn(),
  idempotencyKey: () => "preference-idem",
  waitForTask: vi.fn(),
}));
vi.mock("vue-router", () => ({
  useRouter: () => ({ push: routerPushMock }),
}));

describe("岗位期望编辑器", () => {
  beforeEach(() => {
    apiMock.mockReset();
    routerPushMock.mockReset();
    apiMock.mockImplementation((path: string, options?: Record<string, unknown>) => {
      if (path === "/api/v1/documents") return Promise.resolve({ items: [] });
      if (path === "/api/v1/preferences" && !options) return Promise.resolve({ items: [] });
      if (path === "/api/v1/preferences" && options) return Promise.resolve({ version: { version_no: 1 } });
      return Promise.resolve({});
    });
  });

  it("分别保存字段状态、强度和完整薪资口径", async () => {
    const pinia = createPinia();
    setActivePinia(pinia);
    const auth = useAuthStore();
    auth.account = { id: "account-1", email: "user@example.test", registration_role: "seeker", admin_permissions: [] };

    const wrapper = mount(MaterialsView, {
      global: {
        plugins: [pinia],
        stubs: {
          AppShell: { template: "<main><slot /></main>" },
          PageHeader: { template: "<header><slot /></header>" },
          AsyncState: { template: "<div />" },
          RouterLink: { template: "<a><slot /></a>" },
        },
      },
    });
    await flushPromises();

    const form = wrapper.find("#preference-form");
    await form.find('input[placeholder="例如：前端方向 · 华东"]').setValue("前端方向 · 华东");
    const sections = form.findAll(".condition-editor");

    await sections[0].find("select.compact").setValue("specified");
    await sections[0].find('input[placeholder="例如：前端工程师"]').setValue("前端工程师");
    await sections[0].findAll("select")[1].setValue("important");

    await sections[1].find("select.compact").setValue("specified");
    await sections[1].find('input[placeholder="杭州、上海"]').setValue("杭州、上海");
    await sections[1].findAll("select")[1].setValue("required");

    await sections[2].find("select.compact").setValue("specified");
    await sections[2].findAll("select")[1].setValue("remote");
    await sections[2].findAll("select")[2].setValue("prefer");

    await sections[3].find("select.compact").setValue("specified");
    const salaryInputs = sections[3].findAll('input[type="number"]');
    await salaryInputs[0].setValue("20000");
    await salaryInputs[1].setValue("30000");
    await salaryInputs[2].setValue("14");
    const salarySelects = sections[3].findAll("select");
    await salarySelects[1].setValue("CNY");
    await salarySelects[2].setValue("monthly");
    await salarySelects[3].setValue("pre_tax");
    await salarySelects[4].setValue("important");

    await form.trigger("submit");
    await flushPromises();

    expect(apiMock).toHaveBeenCalledWith("/api/v1/preferences", expect.objectContaining({
      method: "POST",
      idempotencyKey: "preference-idem",
      body: expect.objectContaining({
        display_name: "前端方向 · 华东",
        preference: {
          job_title: { status: "specified", strength: "important", value: "前端工程师" },
          locations: { status: "specified", strength: "required", values: ["杭州", "上海"] },
          work_mode: { status: "specified", strength: "prefer", value: "remote" },
          salary: {
            status: "specified",
            min: 20000,
            max: 30000,
            currency: "CNY",
            period: "monthly",
            tax_basis: "pre_tax",
            salary_months: 14,
            strength: "important",
          },
        },
      }),
    }));
  });

  it("新表单不预填虚构的简历或岗位内容", async () => {
    const pinia = createPinia();
    setActivePinia(pinia);
    const auth = useAuthStore();
    auth.account = { id: "account-1", email: "user@example.test", registration_role: "seeker", admin_permissions: [] };
    const wrapper = mount(MaterialsView, { global: { plugins: [pinia], stubs: { AppShell: { template: "<main><slot /></main>" }, PageHeader: { template: "<header><slot /></header>" }, AsyncState: true } } });
    await flushPromises();
    expect((wrapper.find('input[name="title"]').element as HTMLInputElement).value).toBe("");
    expect((wrapper.find('textarea[name="text"]').element as HTMLTextAreaElement).value).toBe("");
  });
});
