import { defineStore } from "pinia";

import { api, ApiError, SESSION_KEY } from "@/services/api";
import type { Account, JsonMap, RegistrationRole, SessionState } from "@/types";

export const useAuthStore = defineStore("auth", {
  state: (): SessionState & { restored: boolean } => ({ token: "", csrf: "", account: null, restored: false }),
  getters: {
    role: (state): RegistrationRole | null => state.account?.registration_role || null,
    permissions: (state): Set<string> => new Set(state.account?.admin_permissions || []),
  },
  actions: {
    persist() {
      try {
        localStorage.setItem(SESSION_KEY, JSON.stringify({ token: this.token, csrf: this.csrf, account: this.account }));
      } catch {
        // 登录 Cookie 仍由浏览器保存；存储受限时下次启动可通过 /me 恢复账号。
      }
    },
    async restore() {
      if (this.restored) return;
      this.restored = true;
      try {
        const cached = JSON.parse(localStorage.getItem(SESSION_KEY) || "{}");
        this.token = cached.token || "";
        this.csrf = cached.csrf || "";
        this.account = cached.account || null;
      } catch {
        this.clear();
      }
      try {
        const result = await api<{ account: Account }>("/api/v1/me");
        this.account = result.account;
        this.persist();
      } catch (value) {
        // 只有服务明确确认会话无效时才清理缓存；临时网络、网关或服务错误不能把用户踢出登录。
        if (value instanceof ApiError && [401, 403].includes(value.status)) this.clear();
      }
    },
    async login(email: string, password: string) {
      const result = await api<JsonMap>("/api/v1/auth/login", { method: "POST", body: { email, password } });
      this.token = result.access_token;
      this.csrf = result.csrf_token;
      this.account = result.account;
      this.persist();
    },
    async register(
      email: string,
      password: string,
      registrationRole: RegistrationRole,
      captchaId: string,
      captchaAnswer: string,
    ): Promise<boolean> {
      const registration = await api<JsonMap>("/api/v1/auth/register", {
        method: "POST",
        body: {
          email,
          password,
          registration_role: registrationRole,
          captcha_id: captchaId,
          captcha_answer: captchaAnswer,
        },
      });
      if (registration.verification_token) {
        await api("/api/v1/auth/verify-email", { method: "POST", body: { token: registration.verification_token } });
      }
      if (registration.registration_ready || registration.verification_token || registration.local_auto_verified) {
        await this.login(email, password);
        return true;
      }
      return false;
    },
    async logout() {
      try {
        await api("/api/v1/auth/logout", { method: "POST" });
      } finally {
        this.clear();
      }
    },
    clear() {
      this.token = "";
      this.csrf = "";
      this.account = null;
      try {
        localStorage.removeItem(SESSION_KEY);
      } catch {
        // 某些隐私模式会禁止 Web Storage；此时只能依赖服务端 Cookie 的生命周期。
      }
    },
  },
});
