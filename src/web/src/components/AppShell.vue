<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";

import { api } from "@/services/api";
import { useAuthStore } from "@/stores/auth";
import type { JsonMap } from "@/types";
import SiteFooter from "@/components/SiteFooter.vue";

interface NavItem {
  label: string;
  to: string;
  hint: string;
}

interface AdminNavItem extends NavItem {
  visible: boolean;
}

const auth = useAuthStore();
const route = useRoute();
const router = useRouter();
const role = computed(() => auth.role || "seeker");
const balances = ref<JsonMap[]>([]);
const usagePath = computed(() => `/app/${role.value}/usage`);
const featureLabels: Record<string, string> = { analysis: "分析", rewrite: "改写", interview: "面试" };
const mainNav = computed<NavItem[]>(() => role.value === "seeker"
  ? [
      { label: "工作台", to: "/app/seeker/dashboard", hint: "概览" }, { label: "简历", to: "/app/seeker/resume", hint: "资料与期望" },
      { label: "匹配池", to: "/app/seeker/pool", hint: "岗位与报告" }, { label: "面试", to: "/app/seeker/interview", hint: "逐轮练习" },
      { label: "统计", to: "/app/seeker/stats", hint: "反馈与结果" },
    ]
  : [
      { label: "工作台", to: "/app/recruiter/dashboard", hint: "概览" }, { label: "候选人资料", to: "/app/recruiter/materials", hint: "JD 与简历" },
      { label: "统计", to: "/app/recruiter/stats", hint: "反馈与结果" },
    ]);
const adminNav = computed(() => {
  const p = auth.permissions;
  const items: AdminNavItem[] = [
    { label: "站点概况", to: "/app/admin/metrics", hint: "全站指标", visible: p.has("admin.stats.read") },
    { label: "用户管理", to: "/app/admin/users", hint: "账号与状态", visible: p.has("admin.users.read") },
    { label: "角色权限", to: "/app/admin/roles", hint: "授权组合", visible: p.has("admin.roles.manage") },
    { label: "次数管理", to: "/app/admin/usage", hint: "发放与流水", visible: p.has("admin.usage.grant") },
    { label: "抓取岗位", to: "/app/admin/job-pool", hint: "JD 统一管理", visible: p.has("admin.job_pool.read") },
    { label: "日志管理", to: "/app/admin/logs", hint: "审计与导出", visible: [...p].some((value) => value.startsWith("admin.logs.")) },
  ];
  return items.filter((item) => item.visible);
});
const adminEntry = computed(() => adminNav.value[0]?.to || "");

async function logout() {
  await auth.logout();
  await router.push("/app/login");
}

async function loadBalances() {
  try {
    const result = await api<JsonMap>("/api/v1/usage?entries_limit=1");
    balances.value = Array.isArray(result.balances) ? result.balances : [];
  } catch {
    // 额度是辅助信息；读取失败不能阻塞主导航，用户仍可进入用量页重试。
    balances.value = [];
  }
}

onMounted(loadBalances);
</script>

<template>
  <header class="topbar">
    <RouterLink class="brand-button" to="/"><img class="brand-mark" src="/purslyx-logo.png" alt="Purslyx 品牌标志" /><span>Purslyx</span></RouterLink>
    <span class="eyebrow">{{ role === "seeker" ? "求职工作台" : "招聘工作台" }}</span>
    <span class="topbar-spacer" />
    <details class="account-menu">
      <summary class="button soft small">账号</summary>
      <div class="account-popover">
        <strong>{{ auth.account?.email }}</strong>
        <small>{{ role === "seeker" ? "求职账号" : "招聘账号" }} · 身份不可切换</small>
        <RouterLink v-if="adminEntry" class="button outline small full" :to="adminEntry">进入管理端</RouterLink>
        <button class="button soft small full" type="button" @click="logout">退出登录</button>
      </div>
    </details>
  </header>
  <div class="workspace-layout" data-workspace-loaded="true">
    <aside class="sidebar">
      <nav class="side-nav" aria-label="业务导航">
        <RouterLink v-for="item in mainNav" :key="item.to" class="nav-button" :to="item.to" :class="{ active: route.path === item.to }">
          <span>{{ item.label }}</span><small>{{ item.hint }}</small>
        </RouterLink>
        <template v-if="adminNav.length">
          <div class="eyebrow nav-group">授权管理</div>
          <RouterLink v-for="item in adminNav" :key="item.to" class="nav-button" :to="item.to" :class="{ active: route.path === item.to }">
            <span>{{ item.label }}</span><small>{{ item.hint }}</small>
          </RouterLink>
        </template>
      </nav>
      <RouterLink class="sidebar-usage" :class="{ active: route.path === usagePath }" :to="usagePath" aria-label="查看可用额度和用量流水">
        <div class="sidebar-usage-head"><strong>可用额度</strong><span>查看流水 →</span></div>
        <div v-if="balances.length" class="sidebar-balance-list">
          <div v-for="item in balances" :key="item.feature"><span>{{ featureLabels[item.feature] || item.feature }}</span><strong>{{ item.available || 0 }}<small>{{ item.unit === "sessions" ? "场" : "次" }}</small></strong></div>
        </div>
        <small v-else class="sidebar-usage-empty">点击查看用量</small>
      </RouterLink>
      <div class="side-note"><strong>重要结论可复核</strong><p>每个结果都绑定已确认输入版本；未知信息不会被推断成事实。</p></div>
    </aside>
    <main class="workspace-content"><slot /></main>
  </div>
  <SiteFooter />
</template>
