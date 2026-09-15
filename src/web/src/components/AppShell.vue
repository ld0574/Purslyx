<script setup lang="ts">
import { computed } from "vue";
import { useRoute, useRouter } from "vue-router";

import { useAuthStore } from "@/stores/auth";

const auth = useAuthStore();
const route = useRoute();
const router = useRouter();
const role = computed(() => auth.role || "seeker");
const mainNav = computed(() => role.value === "seeker"
  ? [
      ["工作台", `/app/seeker/dashboard`, "概览"], ["简历", "/app/seeker/resume", "资料与期望"],
      ["匹配池", "/app/seeker/pool", "岗位与报告"], ["事实与改写", "/app/seeker/rewrite", "真实表达"],
      ["岗位版简历", "/app/seeker/variants", "排版与 PDF"], ["面试", "/app/seeker/interview", "逐轮练习"],
      ["任务", "/app/seeker/tasks", "执行恢复"], ["用量", "/app/seeker/usage", "次数流水"],
      ["统计", "/app/seeker/stats", "反馈与结果"],
    ]
  : [
      ["工作台", "/app/recruiter/dashboard", "概览"], ["候选人资料", "/app/recruiter/materials", "JD 与简历"],
      ["单人报告", "/app/recruiter/report", "证据与条件"], ["任务", "/app/recruiter/tasks", "执行恢复"],
      ["用量", "/app/recruiter/usage", "次数流水"], ["统计", "/app/recruiter/stats", "反馈与结果"],
    ]);
const adminNav = computed(() => {
  const p = auth.permissions;
  return [
    ["站点概况", "/app/admin/metrics", "全站指标", p.has("admin.stats.read")],
    ["用户管理", "/app/admin/users", "账号与状态", p.has("admin.users.read")],
    ["角色权限", "/app/admin/roles", "授权组合", p.has("admin.roles.manage")],
    ["次数管理", "/app/admin/usage", "发放与流水", p.has("admin.usage.grant")],
    ["日志管理", "/app/admin/logs", "审计与导出", [...p].some((value) => value.startsWith("admin.logs."))],
  ].filter((item) => item[3]);
});

async function logout() {
  await auth.logout();
  await router.push("/app/login");
}
</script>

<template>
  <header class="topbar">
    <RouterLink class="brand-button" to="/"><span class="brand-mark">P</span><span>Purslyx</span></RouterLink>
    <span class="eyebrow">{{ role === "seeker" ? "求职工作台" : "招聘工作台" }}</span>
    <span class="topbar-spacer" />
    <span class="health-chip"><i class="health-dot" />201 PostgreSQL</span>
    <span class="micro">{{ auth.account?.email }}</span>
    <button class="button soft small" type="button" @click="logout">退出</button>
  </header>
  <div class="workspace-layout" data-workspace-loaded="true">
    <aside class="sidebar">
      <div class="side-brand"><span class="brand-mark">P</span><div>Purslyx<small>{{ role === "seeker" ? "求职工作台" : "招聘工作台" }}</small></div></div>
      <nav class="side-nav" aria-label="业务导航">
        <RouterLink v-for="item in mainNav" :key="String(item[1])" class="nav-button" :to="String(item[1])" :class="{ active: route.path === item[1] }">
          <span>{{ item[0] }}</span><small>{{ item[2] }}</small>
        </RouterLink>
        <template v-if="adminNav.length">
          <div class="eyebrow nav-group">授权管理</div>
          <RouterLink v-for="item in adminNav" :key="String(item[1])" class="nav-button" :to="String(item[1])" :class="{ active: route.path === item[1] }">
            <span>{{ item[0] }}</span><small>{{ item[2] }}</small>
          </RouterLink>
        </template>
      </nav>
      <div class="side-note"><strong>重要结论可复核</strong><p>每个结果都绑定已确认输入版本；未知信息不会被推断成事实。</p></div>
    </aside>
    <main class="workspace-content"><slot /></main>
  </div>
</template>
