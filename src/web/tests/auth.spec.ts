import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { SESSION_KEY } from "@/services/api";
import { useAuthStore } from "@/stores/auth";

describe("认证状态", () => {
  beforeEach(() => setActivePinia(createPinia()));

  it("登录后持久化令牌、CSRF、固定身份和后台权限", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ data: {
      access_token: "token", csrf_token: "csrf", account: {
        id: "account-1", email: "user@example.com", registration_role: "seeker", admin_permissions: ["admin.users.read"],
      },
    } }), { status: 200, headers: { "Content-Type": "application/json" } }));
    const store = useAuthStore();
    await store.login("user@example.com", "password-password");
    expect(store.role).toBe("seeker");
    expect(store.permissions.has("admin.users.read")).toBe(true);
    expect(JSON.parse(localStorage.getItem(SESSION_KEY) || "{}")).toMatchObject({ token: "token", csrf: "csrf" });
  });

  it("恢复会话失败时清除失效缓存", async () => {
    localStorage.setItem(SESSION_KEY, JSON.stringify({ token: "expired", csrf: "old", account: { registration_role: "seeker" } }));
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ error: { code: "AUTH_REQUIRED", message: "请登录" } }), {
      status: 401, headers: { "Content-Type": "application/json" },
    }));
    const store = useAuthStore();
    await store.restore();
    expect(store.account).toBeNull();
    expect(localStorage.getItem(SESSION_KEY)).toBeNull();
  });

  it("验证码注册完成后直接建立会话", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(new Response(JSON.stringify({ data: { status: "registered", registration_ready: true } }), { status: 202, headers: { "Content-Type": "application/json" } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ data: {
        access_token: "token", csrf_token: "csrf", account: { id: "account-2", email: "new@example.com", registration_role: "recruiter", admin_permissions: [] },
      } }), { status: 200, headers: { "Content-Type": "application/json" } }));
    const store = useAuthStore();
    await expect(store.register("new@example.com", "password-password", "recruiter", "captcha-1", "12")).resolves.toBe(true);
    expect(store.role).toBe("recruiter");
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("线上待验证注册不会提前尝试登录", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ data: { status: "verification_requested" } }), {
      status: 202, headers: { "Content-Type": "application/json" },
    }));
    const store = useAuthStore();
    await expect(store.register("new@example.com", "password-password", "seeker", "captcha-1", "12")).resolves.toBe(false);
    expect(store.account).toBeNull();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
