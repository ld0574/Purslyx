<script setup lang="ts">
import { computed, onMounted, ref } from "vue";

import AppShell from "@/components/AppShell.vue";
import AsyncState from "@/components/AsyncState.vue";
import PageHeader from "@/components/PageHeader.vue";
import { api } from "@/services/api";
import { useAuthStore } from "@/stores/auth";
import type { JsonMap } from "@/types";
import { errorMessage } from "@/utils/format";

const auth = useAuthStore();
const loading = ref(true);
const error = ref("");
const days = ref(14);
const rangeOptions = [7, 14, 30] as const;
const dashboard = ref<JsonMap>({ counts: {}, balances: [], attention: {}, activity_series: [], practice_series: [] });
const role = computed(() => auth.role || "seeker");

const dimensionLabels: Record<string, string> = {
  relevance: "回答切题度", specificity: "事实具体度", ownership: "个人贡献清晰度",
  outcome_evidence: "结果证据力度", communication: "表达结构与清晰度",
};

const metrics = computed(() => {
  const counts = dashboard.value.counts || {};
  const base = role.value === "seeker"
    ? [[counts.confirmed_resumes || 0, "已确认简历"], [counts.job_pool_items || 0, "匹配池岗位"], [counts.completed_analyses || 0, "已完成分析"], [counts.active_tasks || 0, "进行中任务"]]
    : [[counts.confirmed_resumes || 0, "候选人资料"], [counts.confirmed_job_descriptions || 0, "岗位 JD"], [counts.completed_analyses || 0, "已完成分析"], [counts.active_tasks || 0, "进行中任务"]];
  return base.map(([value, label]) => ({ value, label, unit: "" }));
});

const activityDefinitions = computed(() => role.value === "seeker"
  ? [{ key: "captured_jobs", label: "抓取岗位", className: "brand" }, { key: "completed_analyses", label: "完成分析", className: "success" }, { key: "apply_clicks", label: "去投递点击", className: "attention" }]
  : [{ key: "candidate_documents", label: "新增候选人资料", className: "brand" }, { key: "job_descriptions", label: "新增岗位 JD", className: "opportunity" }, { key: "completed_analyses", label: "完成候选人分析", className: "success" }]);
const activityMax = computed(() => Math.max(1, ...(dashboard.value.activity_series || []).flatMap((row: JsonMap) => activityDefinitions.value.map((item) => Number(row[item.key] || 0)))));
const practiceRows = computed(() => (dashboard.value.practice_series || []) as JsonMap[]);
const practicePoints = computed(() => practiceRows.value.flatMap((row, index) => {
  const score = Number(row.practice_index);
  if (!Number.isFinite(score) || row.practice_index == null) return [];
  return [{ x: 12 + (index / Math.max(1, practiceRows.value.length - 1)) * 376, y: 108 - score, score, date: row.date }];
}));
const practiceLine = computed(() => practicePoints.value.map((point) => `${point.x},${point.y}`).join(" "));
const latestDimensions = computed(() => Object.entries(dashboard.value.latest_practice_dimensions || {}).map(([key, value]) => ({
  key, label: dimensionLabels[key] || String((value as JsonMap).label || key), score: Number((value as JsonMap).score || 0),
})));

const nextActions = computed(() => {
  const attention = dashboard.value.attention || {};
  const actions: Array<{ title: string; description: string; to: string }> = [];
  const tasksPath = `/app/${role.value}/tasks`;
  if (Number(attention.failed_tasks) > 0) actions.push({ title: "处理失败任务", description: `${attention.failed_tasks} 个任务需要重试或检查。`, to: tasksPath });
  if (Number(attention.pending_documents) > 0) actions.push({ title: "确认待处理资料", description: `${attention.pending_documents} 份资料尚未确认。`, to: role.value === "seeker" ? "/app/seeker/resume" : "/app/recruiter/materials" });
  if (Number(attention.active_tasks) > 0) actions.push({ title: "查看任务进度", description: `${attention.active_tasks} 个任务正在执行。`, to: tasksPath });
  if (role.value === "seeker") {
    if (!Number(attention.confirmed_resumes)) actions.push({ title: "添加第一份简历", description: "确认简历后才能开始岗位匹配。", to: "/app/seeker/resume" });
    else if (!Number(attention.active_preferences)) actions.push({ title: "补充岗位期望", description: "保存岗位方向、地点和薪资边界。", to: "/app/seeker/resume" });
    else if (!Number(attention.job_pool_items)) actions.push({ title: "添加目标岗位", description: "从招聘平台抓取或手动添加岗位。", to: "/app/seeker/pool" });
    else if (!Number(attention.completed_analyses)) actions.push({ title: "开始岗位匹配", description: "选择简历并生成第一份分析。", to: "/app/seeker/pool" });
    else if (!Number(attention.completed_interviews)) actions.push({ title: "开始面试练习", description: "围绕目标岗位完成一次针对性练习。", to: "/app/seeker/interview" });
    if (latestDimensions.value.length) {
      const weakest = [...latestDimensions.value].sort((a, b) => a.score - b.score)[0];
      if (weakest.score < 75) actions.push({ title: `练习${weakest.label}`, description: `最近为 ${weakest.score}，下一场重点改善这一项。`, to: "/app/seeker/interview" });
    }
    actions.push({ title: "继续查看岗位", description: "更新岗位并继续匹配适合的机会。", to: "/app/seeker/pool" });
  } else if (!Number(attention.confirmed_resumes)) actions.push({ title: "导入候选人资料", description: "先确认候选人简历再发起分析。", to: "/app/recruiter/materials" });
  else if (!Number(attention.confirmed_job_descriptions)) actions.push({ title: "导入岗位 JD", description: "准备明确的岗位要求作为分析依据。", to: "/app/recruiter/materials" });
  else actions.push({ title: "发起单人分析", description: "选择候选人、岗位和明确期望生成报告。", to: "/app/recruiter/materials#recruiter-analysis-form" });
  return actions.slice(0, 3);
});

const quickActions = computed(() => role.value === "seeker"
  ? [["简历与岗位期望", "管理已确认简历和多套求职条件", "/app/seeker/resume"], ["岗位匹配", "整理岗位并生成有依据的匹配报告", "/app/seeker/pool"], ["任务中心", "查看耗时任务、失败原因和恢复入口", "/app/seeker/tasks"]]
  : [["候选人资料", "管理候选人简历、岗位 JD 和明确期望", "/app/recruiter/materials"], ["发起单人分析", "选择候选人与岗位生成证据化报告", "/app/recruiter/materials#recruiter-analysis-form"], ["任务中心", "查看分析任务、失败原因和恢复入口", "/app/recruiter/tasks"]]);

async function load() {
  loading.value = true; error.value = "";
  try { dashboard.value = await api<JsonMap>(`/api/v1/dashboard?days=${days.value}`); }
  catch (value) { error.value = errorMessage(value, "工作台读取失败"); }
  finally { loading.value = false; }
}
function selectRange(value: number) {
  if (days.value === value) return;
  days.value = value;
  void load();
}
onMounted(load);
</script>

<template>
  <AppShell><PageHeader eyebrow="WORKBENCH" title="工作台" :description="role === 'seeker' ? '看清今天的进展，继续完成最重要的一步。' : '集中查看候选人分析进展和下一步工作。'"><button class="button outline small" type="button" @click="load">刷新</button></PageHeader><AsyncState :loading="loading" :error="error" />
    <template v-if="!loading">
      <section class="metric-grid dashboard-metrics"><article v-for="item in metrics" :key="String(item.label)" class="metric-card"><strong>{{ item.value }}<small v-if="item.unit"> {{ item.unit }}</small></strong><span>{{ item.label }}</span></article></section>
      <section class="dashboard-main-grid">
        <article class="card dashboard-trend-card"><div class="card-head dashboard-card-head"><div><h3>每日行动趋势</h3><p>按上海自然日统计，只展示系统能够确认的业务事实。</p></div><div class="dashboard-range" role="group" aria-label="趋势周期"><button v-for="value in rangeOptions" :key="value" type="button" :class="{ active: days === value }" :aria-pressed="days === value" @click="selectRange(value)">近 {{ value }} 天</button></div></div><div class="card-body"><div class="trend-legend"><span v-for="item in activityDefinitions" :key="item.key"><i :class="item.className" />{{ item.label }}</span></div><div class="activity-chart"><div v-for="row in dashboard.activity_series || []" :key="row.date" class="activity-day" :title="`${row.date}：${activityDefinitions.map(item => `${item.label} ${row[item.key] || 0}`).join('，')}`"><div class="activity-bars"><i v-for="item in activityDefinitions" :key="item.key" :class="item.className" :style="{ height: `${Math.max(Number(row[item.key] || 0) ? 4 : 0, Number(row[item.key] || 0) / activityMax * 100)}%` }" /></div><small>{{ String(row.date).slice(5) }}</small></div></div><p v-if="role === 'seeker'" class="micro dashboard-definition">“去投递点击”表示 Purslyx 已记录并发起原岗位跳转，不代表外部平台已经提交成功。</p></div></article>
        <article class="card dashboard-next-card"><div class="card-head"><div><h3>下一步</h3><p>优先处理会阻塞流程的事项。</p></div></div><div class="card-body next-action-list"><RouterLink v-for="(item, index) in nextActions" :key="item.title" class="next-action" :to="item.to"><span>{{ index + 1 }}</span><div><strong>{{ item.title }}</strong><p>{{ item.description }}</p></div><b>→</b></RouterLink></div></article>
      </section>
      <section v-if="role === 'seeker'" class="card practice-card"><div class="card-head"><div><h3>面试练习进步</h3><p>练习表现指数用于比较自己的变化，不代表招聘方评分或录用概率。</p></div><RouterLink class="button soft small" to="/app/seeker/interview">开始练习</RouterLink></div><div class="card-body practice-layout"><div v-if="practicePoints.length" class="practice-chart-wrap"><svg class="practice-chart" viewBox="0 0 400 120" role="img" aria-label="面试练习表现指数趋势"><line x1="12" y1="8" x2="12" y2="108" /><line x1="12" y1="108" x2="388" y2="108" /><polyline :points="practiceLine" /><circle v-for="point in practicePoints" :key="`${point.date}-${point.score}`" :cx="point.x" :cy="point.y" r="4"><title>{{ point.date }}：{{ point.score }}</title></circle></svg><div class="practice-axis"><span>100</span><span>50</span><span>0</span></div></div><div v-else class="empty dashboard-empty"><div><strong>还没有可比较的完整练习</strong><p>完整回答三道主问题后，这里会开始记录趋势。</p></div></div><div class="dimension-list"><div class="dimension-title"><strong>最近一次五维拆解</strong><small v-if="latestDimensions.length">固定规则等权计算</small></div><div v-if="!latestDimensions.length" class="micro">新评价体系上线后的完整练习会显示在这里。</div><article v-for="item in latestDimensions" :key="item.key"><div><span>{{ item.label }}</span><strong>{{ item.score }}</strong></div><div class="dimension-track"><i :style="{ width: `${item.score}%` }" /></div></article></div></div></section>
      <section class="dashboard-shortcuts"><div class="section-title"><div><h3>快捷入口</h3><p>从核心流程继续，不需要在历史记录里寻找入口。</p></div></div><div class="shortcut-grid"><RouterLink v-for="(item, index) in quickActions" :key="item[0]" class="shortcut-card" :to="item[2]"><span>0{{ index + 1 }}</span><div><strong>{{ item[0] }}</strong><p>{{ item[1] }}</p></div><b>→</b></RouterLink></div></section>
    </template>
  </AppShell>
</template>
