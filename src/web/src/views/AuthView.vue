<script setup lang="ts">
import { computed, ref } from "vue";
import { useRoute, useRouter } from "vue-router";

import AsyncState from "@/components/AsyncState.vue";
import { api } from "@/services/api";
import { useAuthStore } from "@/stores/auth";
import type { RegistrationRole } from "@/types";

const route = useRoute();
const router = useRouter();
const auth = useAuthStore();
const mode = computed(() => String(route.meta.mode || "login"));
const email = ref("");
const password = ref("");
const token = ref(String(route.query.token || ""));
const registrationRole = ref<RegistrationRole>("seeker");
const loading = ref(false);
const error = ref("");
const success = ref("");
const title = computed(() => ({
  login: "登录工作台",
  register: "创建账号",
  forgot: "找回密码",
  reset: "重置密码",
  verify: "验证邮箱",
}[mode.value] || "账号操作"));

async function submit() {
  loading.value = true; error.value = ""; success.value = "";
  try {
    if (mode.value === "register") {
      await auth.register(email.value, password.value, registrationRole.value);
      await router.push(`/app/${auth.role}/dashboard`);
    } else if (mode.value === "login") {
      await auth.login(email.value, password.value);
      await router.push(String(route.query.redirect || `/app/${auth.role}/dashboard`));
    } else if (mode.value === "forgot") {
      await api("/api/v1/auth/forgot-password", { method: "POST", body: { email: email.value } });
      success.value = "若账号存在，重置说明已经发送。";
    } else if (mode.value === "verify") {
      await api("/api/v1/auth/verify-email", { method: "POST", body: { token: token.value } });
      success.value = "邮箱验证完成，现在可以登录。";
    } else {
      await api("/api/v1/auth/reset-password", { method: "POST", body: { token: token.value, new_password: password.value } });
      success.value = "密码已重置，请重新登录。";
    }
  } catch (value) { error.value = value instanceof Error ? value.message : "操作失败"; }
  finally { loading.value = false; }
}
</script>

<template>
  <header class="topbar"><RouterLink class="brand-button" to="/"><span class="brand-mark">P</span>Purslyx</RouterLink><span class="topbar-spacer" /><RouterLink class="button soft" :to="mode === 'login' ? '/app/register' : '/app/login'">{{ mode === "login" ? "创建账号" : "返回登录" }}</RouterLink></header>
  <main class="public-main"><section class="auth-layout"><div class="auth-story"><div class="eyebrow">YOUR NEXT OPPORTUNITY</div><h1>先确认事实，<span>再生成下一步。</span></h1><p>身份在注册时固定。求职与招聘使用相同的证据规则，数据、版本和用量按账号隔离。</p><div class="auth-points"><div class="auth-point"><div><strong>资料可恢复</strong><small>重新登录后继续未完成任务</small></div></div><div class="auth-point"><div><strong>计次可审计</strong><small>成功只结算一次，失败释放预留</small></div></div></div></div>
    <form id="auth-form" class="auth-card" @submit.prevent="submit"><h2>{{ title }}</h2><p>本机运行连接《本地开发环境.md》的 201 PostgreSQL。</p><div v-if="mode === 'register'" class="identity-grid" style="margin-top:20px"><label class="identity-card" :class="{active: registrationRole === 'seeker'}"><input v-model="registrationRole" type="radio" name="registration_role" value="seeker" /><span><strong>求职者</strong><small>匹配、改写、PDF 与面试</small></span></label><label class="identity-card" :class="{active: registrationRole === 'recruiter'}"><input v-model="registrationRole" type="radio" name="registration_role" value="recruiter" /><span><strong>招聘方</strong><small>单人分析与待核实问题</small></span></label></div><div class="auth-fields" style="margin-top:20px"><div v-if="['login','register','forgot'].includes(mode)" class="field-group"><label for="auth-email">邮箱</label><input id="auth-email" v-model="email" class="field" type="email" autocomplete="email" required /></div><div v-if="['login','register','reset'].includes(mode)" class="field-group"><label for="auth-password">{{ mode === 'reset' ? '新密码' : '密码' }}</label><input id="auth-password" v-model="password" class="field" type="password" minlength="12" :autocomplete="mode === 'reset' ? 'new-password' : 'current-password'" required /></div><div v-if="['verify','reset'].includes(mode)" class="field-group"><label for="auth-token">一次性令牌</label><input id="auth-token" v-model="token" class="field" required /></div></div><AsyncState :loading="loading" :error="error" :success="success" /><button class="button primary full" type="submit" style="margin-top:18px">确认</button><div class="auth-foot"><RouterLink v-if="mode === 'login'" to="/app/forgot-password">忘记密码</RouterLink> · 状态变更请求经过 CSRF、频率与幂等校验。</div></form>
  </section></main>
</template>
