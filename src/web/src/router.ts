import { createRouter, createWebHistory, type RouteRecordRaw } from "vue-router";

import { useAuthStore } from "@/stores/auth";

const routes: RouteRecordRaw[] = [
  { path: "/", name: "home", component: () => import("@/views/HomeView.vue"), meta: { public: true, title: "Purslyx" } },
  { path: "/guide", name: "guide", component: () => import("@/views/GuideView.vue"), meta: { public: true, title: "使用说明" } },
  { path: "/app/login", name: "login", component: () => import("@/views/AuthView.vue"), meta: { public: true, mode: "login", title: "登录" } },
  { path: "/app/register", name: "register", component: () => import("@/views/AuthView.vue"), meta: { public: true, mode: "register", title: "注册" } },
  { path: "/app/forgot-password", name: "forgot-password", component: () => import("@/views/AuthView.vue"), meta: { public: true, mode: "forgot", title: "找回密码" } },
  { path: "/app/reset-password", name: "reset-password", component: () => import("@/views/AuthView.vue"), meta: { public: true, mode: "reset", title: "重置密码" } },
  { path: "/app/verify-email", name: "verify-email", component: () => import("@/views/AuthView.vue"), meta: { public: true, mode: "verify", title: "验证邮箱" } },
  { path: "/app/request-account-recovery", name: "request-account-recovery", component: () => import("@/views/AuthView.vue"), meta: { public: true, mode: "recovery-request", title: "恢复账号" } },
  { path: "/app/recover-account", name: "recover-account", component: () => import("@/views/AuthView.vue"), meta: { public: true, mode: "recover", title: "确认恢复账号" } },
  { path: "/app/:role(seeker|recruiter)/dashboard", name: "dashboard", component: () => import("@/views/DashboardView.vue"), meta: { roleRoute: true, title: "工作台" } },
  { path: "/app/seeker/resume", name: "seeker-materials", component: () => import("@/views/MaterialsView.vue"), meta: { role: "seeker", title: "简历与岗位期望" } },
  { path: "/app/recruiter/materials", name: "recruiter-materials", component: () => import("@/views/MaterialsView.vue"), meta: { role: "recruiter", title: "候选人资料" } },
  { path: "/app/seeker/pool", name: "pool", component: () => import("@/views/PoolView.vue"), meta: { role: "seeker", title: "匹配池" } },
  { path: "/app/:role(seeker|recruiter)/report", name: "report", component: () => import("@/views/ReportView.vue"), meta: { roleRoute: true, title: "匹配报告" } },
  { path: "/app/seeker/rewrite", name: "rewrite", component: () => import("@/views/RewriteView.vue"), meta: { role: "seeker", title: "事实与改写" } },
  { path: "/app/seeker/variants", name: "variants", component: () => import("@/views/VariantsView.vue"), meta: { role: "seeker", title: "岗位版简历" } },
  { path: "/app/seeker/interview", name: "interview", component: () => import("@/views/InterviewView.vue"), meta: { role: "seeker", title: "面试练习" } },
  { path: "/app/:role(seeker|recruiter)/tasks", name: "tasks", component: () => import("@/views/TasksView.vue"), meta: { roleRoute: true, title: "任务中心" } },
  { path: "/app/:role(seeker|recruiter)/usage", name: "usage", component: () => import("@/views/UsageView.vue"), meta: { roleRoute: true, title: "用量" } },
  { path: "/app/:role(seeker|recruiter)/stats", name: "stats", component: () => import("@/views/StatsView.vue"), meta: { roleRoute: true, title: "统计与反馈" } },
  { path: "/app/admin/metrics", name: "admin-metrics", component: () => import("@/views/admin/AdminMetricsView.vue"), meta: { permission: "admin.stats.read", title: "站点概况" } },
  { path: "/app/admin/users", name: "admin-users", component: () => import("@/views/admin/AdminUsersView.vue"), meta: { permission: "admin.users.read", title: "用户管理" } },
  { path: "/app/admin/roles", name: "admin-roles", component: () => import("@/views/admin/AdminRolesView.vue"), meta: { permission: "admin.roles.manage", title: "角色权限" } },
  { path: "/app/admin/usage", name: "admin-usage", component: () => import("@/views/admin/AdminUsageView.vue"), meta: { permission: "admin.usage.grant", title: "次数管理" } },
  { path: "/app/admin/job-pool", name: "admin-job-pool", component: () => import("@/views/admin/AdminJobPoolView.vue"), meta: { permission: "admin.job_pool.read", title: "抓取岗位管理" } },
  { path: "/app/admin/logs", name: "admin-logs", component: () => import("@/views/admin/AdminLogsView.vue"), meta: { permission: "admin", title: "日志管理" } },
  { path: "/app/forbidden", name: "forbidden", component: () => import("@/views/PermissionDeniedView.vue"), meta: { title: "没有访问权限" } },
  { path: "/:pathMatch(.*)*", component: () => import("@/views/NotFoundView.vue"), meta: { public: true, title: "页面不存在" } },
];

export const router = createRouter({
  history: createWebHistory(),
  routes,
  scrollBehavior: (to) => to.hash ? { el: to.hash, top: 88, behavior: "smooth" } : { top: 0 },
});

router.beforeEach(async (to) => {
  const auth = useAuthStore();
  await auth.restore();
  document.title = `${String(to.meta.title || "Purslyx")} · Purslyx`;
  if (to.meta.public) {
    if (auth.account && ["login", "register"].includes(String(to.name))) {
      return `/app/${auth.role}/dashboard`;
    }
    return true;
  }
  if (!auth.account) return { name: "login", query: { redirect: to.fullPath } };
  const expectedRole = (to.meta.role as string | undefined) || (to.meta.roleRoute ? String(to.params.role || "") : "");
  if (expectedRole && expectedRole !== auth.role) return `/app/${auth.role}/dashboard`;
  const permission = to.meta.permission as string | undefined;
  if (permission === "admin") {
    return [...auth.permissions].some((value) => value.startsWith("admin.logs.")) || { name: "forbidden", query: { from: to.fullPath } };
  }
  if (permission && !auth.permissions.has(permission)) return { name: "forbidden", query: { from: to.fullPath } };
  return true;
});
