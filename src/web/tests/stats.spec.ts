import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";

import StatsView from "@/views/StatsView.vue";

const apiMock = vi.hoisted(() => vi.fn());
vi.mock("@/services/api", () => ({ api: apiMock, idempotencyKey: () => "feedback-idem" }));

describe("统计与反馈页面", () => {
  beforeEach(() => {
    apiMock.mockReset();
    apiMock.mockImplementation((path: string) => path.startsWith("/api/v1/stats/me")
      ? Promise.resolve({ registration_role: "seeker", metrics: [], summary: {}, timezone: "Asia/Shanghai", rule_version: "personal-stats-v1" })
      : Promise.resolve({ id: "feedback-1", status: "new" }));
  });

  it("提交后端允许的反馈类型、评分和统一上下文", async () => {
    const wrapper = mount(StatsView, {
      global: { stubs: {
        AppShell: { template: "<main><slot /></main>" },
        PageHeader: { template: "<header><slot /></header>" },
        AsyncState: { template: "<div />" },
      } },
    });
    await flushPromises();
    await wrapper.find("textarea").setValue("希望增加报告对比功能");
    await wrapper.find("#feedback-form").trigger("submit");
    await flushPromises();
    expect(apiMock).toHaveBeenCalledWith("/api/v1/feedback", expect.objectContaining({
      method: "POST",
      idempotencyKey: "feedback-idem",
      body: expect.objectContaining({ feedback_type: "suggestion", rating: 5, context_type: "general" }),
    }));
  });
});
