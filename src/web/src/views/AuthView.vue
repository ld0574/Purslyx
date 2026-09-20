<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";

import AsyncState from "@/components/AsyncState.vue";
import { api, ApiError } from "@/services/api";
import { useAuthStore } from "@/stores/auth";
import type { RegistrationRole } from "@/types";

const route = useRoute();
const router = useRouter();
const auth = useAuthStore();
const mode = computed(() => String(route.meta.mode || "login"));
const email = ref(String(route.query.email || ""));
const password = ref("");
const token = ref(String(route.query.token || ""));
const registrationRole = ref<RegistrationRole>("seeker");
const loading = ref(false);
const error = ref("");
const success = ref("");
const captcha = ref<{ captcha_id: string; question: string; expires_in: number } | null>(null);
const captchaAnswer = ref("");
const captchaLoading = ref(false);
const title = computed(() => ({
  login: "登录工作台",
  register: "创建账号",
  forgot: "找回密码",
  reset: "重置密码",
  verify: "验证邮箱",
  "recovery-request": "恢复已暂停账号",
  recover: "确认恢复账号",
}[mode.value] || "账号操作"));
const description = computed(() => ({
  login: "使用注册邮箱登录并继续上次进度。",
  register: "注册后直接进入工作台；邮箱验证可由部署配置开启。身份创建后不可切换。",
  forgot: "填写注册邮箱，我们会发送一次性重置链接。",
  reset: "输入邮件中的一次性令牌并设置新密码。",
  verify: "输入邮件中的一次性令牌完成邮箱验证。",
  "recovery-request": "填写注册邮箱，我们会发送一次性恢复链接。",
  recover: "输入邮件中的一次性令牌恢复原账号、资料和使用次数。",
}[mode.value] || "完成账号操作。"));

async function loadCaptcha() {
  if (mode.value !== "register") return;
  captchaLoading.value = true;
  try {
    captcha.value = await api<{ captcha_id: string; question: string; expires_in: number }>("/api/v1/auth/captcha");
    captchaAnswer.value = "";
  } catch (value) {
    error.value = value instanceof Error ? value.message : "验证码加载失败";
  } finally {
    captchaLoading.value = false;
  }
}

watch(mode, (value) => {
  if (value === "register") void loadCaptcha();
  else {
    captcha.value = null;
    captchaAnswer.value = "";
  }
}, { immediate: true });

async function submit() {
  loading.value = true; error.value = ""; success.value = "";
  try {
    if (mode.value === "register") {
      if (!captcha.value) {
        await loadCaptcha();
        throw new Error("验证码正在加载，请稍后再试");
      }
      const loggedIn = await auth.register(
        email.value,
        password.value,
        registrationRole.value,
        captcha.value.captcha_id,
        captchaAnswer.value,
      );
      if (loggedIn) await router.push(`/app/${auth.role}/dashboard`);
      else success.value = "注册申请已受理；如果账号需要验证，请查收邮箱。";
    } else if (mode.value === "login") {
      await auth.login(email.value, password.value);
      await router.push(String(route.query.redirect || `/app/${auth.role}/dashboard`));
    } else if (mode.value === "forgot") {
      const result = await api<Record<string, string>>("/api/v1/auth/forgot-password", { method: "POST", body: { email: email.value } });
      if (result.reset_token) await router.push({ path: "/app/reset-password", query: { token: result.reset_token } });
      else success.value = "若账号存在，重置说明已经发送。";
    } else if (mode.value === "verify") {
      await api("/api/v1/auth/verify-email", { method: "POST", body: { token: token.value } });
      success.value = "邮箱验证完成，现在可以登录。";
    } else if (mode.value === "reset") {
      await api("/api/v1/auth/reset-password", { method: "POST", body: { token: token.value, new_password: password.value } });
      success.value = "密码已重置，请重新登录。";
    } else if (mode.value === "recovery-request") {
      const result = await api<Record<string, string>>("/api/v1/auth/request-account-recovery", { method: "POST", body: { email: email.value } });
      if (result.recovery_token) await router.push({ path: "/app/recover-account", query: { token: result.recovery_token } });
      else success.value = "若账号符合恢复条件，恢复说明已经发送。";
    } else {
      await api("/api/v1/auth/recover-account", { method: "POST", body: { token: token.value } });
      success.value = "账号已恢复，可以使用原邮箱登录。";
    }
  } catch (value) {
    if (mode.value === "register" && value instanceof ApiError && value.code === "AUTH_CAPTCHA_INVALID") {
      captcha.value = null;
      captchaAnswer.value = "";
      await loadCaptcha();
    }
    if (value instanceof ApiError && value.action === "verify_email") {
      await router.push({ path: "/app/verify-email", query: { email: email.value } });
      return;
    }
    if (value instanceof ApiError && value.action === "recover_account") {
      await router.push({ path: "/app/request-account-recovery", query: { email: email.value } });
      return;
    }
    error.value = value instanceof Error ? value.message : "操作失败";
  }
  finally { loading.value = false; }
}

async function resendVerification() {
  loading.value = true; error.value = ""; success.value = "";
  try {
    if (!email.value) throw new Error("请先填写注册邮箱");
    const result = await api<Record<string, string>>("/api/v1/auth/resend-verification", { method: "POST", body: { email: email.value } });
    if (result.verification_token) token.value = result.verification_token;
    success.value = result.verification_token ? "新的本地验证令牌已填入" : "若账号尚未验证，新的验证邮件已经发送。";
  } catch (value) { error.value = value instanceof Error ? value.message : "重新发送失败"; }
  finally { loading.value = false; }
}
</script>

<template>
  <header class="topbar"><RouterLink class="brand-button" to="/"><img class="brand-mark" src="/purslyx-logo.png" alt="Purslyx 品牌标志" />Purslyx</RouterLink><span class="topbar-spacer" /><RouterLink class="button soft" :to="mode === 'login' ? '/app/register' : '/app/login'">{{ mode === "login" ? "创建账号" : "返回登录" }}</RouterLink></header>
  <main class="public-main"><section class="auth-layout"><div class="auth-story"><div class="eyebrow">YOUR NEXT OPPORTUNITY</div><h1>先确认事实，<span>再生成下一步。</span></h1><p>身份在注册时固定。求职与招聘使用相同的证据规则，数据、版本和用量按账号隔离。</p><div class="auth-points"><div class="auth-point"><div><strong>资料可恢复</strong><small>重新登录后继续未完成任务</small></div></div><div class="auth-point"><div><strong>计次可审计</strong><small>成功只结算一次，失败释放预留</small></div></div></div></div>
    <form id="auth-form" class="auth-card" @submit.prevent="submit"><h2>{{ title }}</h2><p>{{ description }}</p><div v-if="mode === 'register'" class="identity-grid" style="margin-top:20px"><label class="identity-card" :class="{active: registrationRole === 'seeker'}"><input v-model="registrationRole" type="radio" name="registration_role" value="seeker" /><span><strong>求职者</strong><small>匹配、改写、PDF 与面试</small></span></label><label class="identity-card" :class="{active: registrationRole === 'recruiter'}"><input v-model="registrationRole" type="radio" name="registration_role" value="recruiter" /><span><strong>招聘方</strong><small>单人分析与待核实问题</small></span></label></div><div class="auth-fields" style="margin-top:20px"><div v-if="['login','register','forgot','verify','recovery-request'].includes(mode)" class="field-group"><label for="auth-email">邮箱</label><input id="auth-email" v-model="email" class="field" type="email" autocomplete="email" :required="mode !== 'verify'" /></div><div v-if="['login','register','reset'].includes(mode)" class="field-group"><label for="auth-password">{{ mode === 'reset' ? '新密码' : '密码' }}</label><input id="auth-password" v-model="password" class="field" type="password" minlength="8" :autocomplete="mode === 'reset' ? 'new-password' : 'current-password'" required /></div><div v-if="['verify','reset','recover'].includes(mode)" class="field-group"><label for="auth-token">一次性令牌</label><input id="auth-token" v-model="token" class="field" required /></div><div v-if="mode === 'register'" class="field-group captcha-field"><div class="captcha-heading"><label for="auth-captcha-answer">简单验证码</label><button class="button link-button small" type="button" :disabled="captchaLoading" @click="loadCaptcha">换一题</button></div><div class="captcha-question" data-captcha-question>{{ captcha?.question || (captchaLoading ? '正在加载……' : '验证码加载失败') }}</div><input id="auth-captcha-answer" v-model="captchaAnswer" class="field" type="text" inputmode="numeric" autocomplete="off" pattern="[0-9]*" placeholder="请输入答案" required /><small class="micro">算术题有效期 5 分钟</small></div></div><AsyncState :loading="loading" :error="error" :success="success" /><button class="button primary full" :disabled="loading || (mode === 'register' && (!captcha || captchaLoading))" type="submit" style="margin-top:18px">确认</button><button v-if="mode === 'verify'" class="button soft full" type="button" style="margin-top:10px" :disabled="loading" @click="resendVerification">重新发送验证邮件</button><div v-if="mode === 'login'" class="auth-foot"><RouterLink to="/app/forgot-password">忘记密码</RouterLink><span> · </span><RouterLink to="/app/request-account-recovery">账号被暂停？申请恢复</RouterLink></div></form>
  </section></main>
</template>
