<script setup lang="ts">
import { computed } from "vue";
import { useRoute, useRouter } from "vue-router";

import { useAuthStore } from "@/stores/auth";

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
const mainNav = computed<NavItem[]>(() => role.value === "seeker"
  ? [
      { label: "工作台", to: "/app/seeker/dashboard", hint: "概览" }, { label: "简历", to: "/app/seeker/resume", hint: "资料与期望" },
      { label: "匹配池", to: "/app/seeker/pool", hint: "岗位与报告" }, { label: "事实与改写", to: "/app/seeker/rewrite", hint: "真实表达" },
      { label: "岗位版简历", to: "/app/seeker/variants", hint: "排版与 PDF" }, { label: "面试", to: "/app/seeker/interview", hint: "逐轮练习" },
      { label: "任务", to: "/app/seeker/tasks", hint: "执行恢复" }, { label: "用量", to: "/app/seeker/usage", hint: "次数流水" },
      { label: "统计", to: "/app/seeker/stats", hint: "反馈与结果" },
    ]
  : [
      { label: "工作台", to: "/app/recruiter/dashboard", hint: "概览" }, { label: "候选人资料", to: "/app/recruiter/materials", hint: "JD 与简历" },
      { label: "单人报告", to: "/app/recruiter/report", hint: "证据与条件" }, { label: "任务", to: "/app/recruiter/tasks", hint: "执行恢复" },
      { label: "用量", to: "/app/recruiter/usage", hint: "次数流水" }, { label: "统计", to: "/app/recruiter/stats", hint: "反馈与结果" },
    ]);
const adminNav = computed(() => {
  const p = auth.permissions;
  const items: AdminNavItem[] = [
    { label: "站点概况", to: "/app/admin/metrics", hint: "全站指标", visible: p.has("admin.stats.read") },
    { label: "用户管理", to: "/app/admin/users", hint: "账号与状态", visible: p.has("admin.users.read") },
    { label: "角色权限", to: "/app/admin/roles", hint: "授权组合", visible: p.has("admin.roles.manage") },
    { label: "次数管理", to: "/app/admin/usage", hint: "发放与流水", visible: p.has("admin.usage.grant") },
    { label: "日志管理", to: "/app/admin/logs", hint: "审计与导出", visible: [...p].some((value) => value.startsWith("admin.logs.")) },
  ];
  return items.filter((item) => item.visible);
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
      <div class="side-note"><strong>重要结论可复核</strong><p>每个结果都绑定已确认输入版本；未知信息不会被推断成事实。</p></div>
    </aside>
    <main class="workspace-content"><slot /></main>
  </div>
</template>
