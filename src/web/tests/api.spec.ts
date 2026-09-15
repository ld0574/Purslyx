import { describe, expect, it, vi } from "vitest";

import { api, deleteWithImpact, SESSION_KEY, waitForTask } from "@/services/api";

describe("API 客户端", () => {
  it("为写请求携带 Bearer、CSRF 和幂等键并解包 data", async () => {
    localStorage.setItem(SESSION_KEY, JSON.stringify({ token: "access-token", csrf: "csrf-token" }));
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ data: { id: "ok" } }), {
      status: 201, headers: { "Content-Type": "application/json" },
    }));

    await expect(api("/api/v1/example", { method: "POST", body: { value: 1 }, idempotencyKey: "idem-1" })).resolves.toEqual({ id: "ok" });
    const [, init] = fetchMock.mock.calls[0];
    expect(init?.credentials).toBe("same-origin");
    expect(init?.headers).toMatchObject({
      Authorization: "Bearer access-token",
      "X-CSRF-Token": "csrf-token",
      "Idempotency-Key": "idem-1",
      "Content-Type": "application/json",
    });
  });

  it("把统一错误响应转换为带业务码的异常", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ error: { code: "USAGE_INSUFFICIENT", message: "次数不足", action: "open_usage" } }), {
      status: 429, headers: { "Content-Type": "application/json" },
    }));
    await expect(api("/api/v1/example")).rejects.toMatchObject({ status: 429, code: "USAGE_INSUFFICIENT", message: "次数不足", action: "open_usage" });
  });

  it("删除前读取影响快照并用 If-Match 提交确认版本", async () => {
    localStorage.setItem(SESSION_KEY, JSON.stringify({ token: "access-token", csrf: "csrf-token" }));
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const fetchMock = vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(new Response(JSON.stringify({ data: { affected: { versions: 2 }, impact_version: "impact-2" } }), {
        status: 200, headers: { "Content-Type": "application/json" },
      }))
      .mockResolvedValueOnce(new Response(null, { status: 204 }));

    await expect(deleteWithImpact("/api/v1/documents/document-1", (impact) => `删除 ${impact.affected.versions} 个版本？`)).resolves.toBe(true);
    expect(window.confirm).toHaveBeenCalledWith("删除 2 个版本？");
    expect(fetchMock.mock.calls[1][1]).toMatchObject({
      method: "DELETE",
      headers: expect.objectContaining({ "If-Match": '"impact-2"', "X-CSRF-Token": "csrf-token" }),
    });
  });

  it("轮询异步任务直到 Worker 交付结果", async () => {
    const updates: string[] = [];
    const fetchMock = vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(new Response(JSON.stringify({ data: { id: "task-1", status: "running", poll_after_ms: 1 } }), {
        status: 200, headers: { "Content-Type": "application/json" },
      }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ data: { id: "task-1", status: "succeeded", result: { resource_id: "report-1" } } }), {
        status: 200, headers: { "Content-Type": "application/json" },
      }));

    const task = await waitForTask(
      { id: "task-1", status: "queued", poll_after_ms: 1 },
      { pollIntervalMs: 1, onUpdate: (value) => updates.push(value.status) },
    );

    expect(task).toMatchObject({ status: "succeeded", result: { resource_id: "report-1" } });
    expect(updates).toEqual(["queued", "running", "succeeded"]);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("把任务失败原因返回给发起页面", async () => {
    await expect(waitForTask({ id: "task-2", status: "failed", failure: { message: "模型暂时不可用" } }))
      .rejects.toMatchObject({ message: "模型暂时不可用", task: { id: "task-2", status: "failed" } });
  });
});
