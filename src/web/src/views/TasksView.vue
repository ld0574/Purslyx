<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref } from "vue";

import AppShell from "@/components/AppShell.vue";
import AsyncState from "@/components/AsyncState.vue";
import PageHeader from "@/components/PageHeader.vue";
import { api, idempotencyKey } from "@/services/api";
import { useAuthStore } from "@/stores/auth";
import type { JsonMap } from "@/types";
import { errorMessage, formatDate, statusClass, statusLabel, taskLabel } from "@/utils/format";

const loading = ref(true);
const auth = useAuthStore();
const busy = ref(false);
const error = ref("");
const success = ref("");
const tasks = ref<JsonMap[]>([]);
const selected = ref<JsonMap | null>(null);
const filters = reactive({ status: "", task_type: "" });
let pollingTimer: number | undefined;

const counts = computed(() => tasks.value.reduce<Record<string, number>>((result, item) => {
  const key = String(item.status || "unknown"); result[key] = (result[key] || 0) + 1; return result;
}, {}));
const hasActive = computed(() => tasks.value.some((item) => ["queued", "running", "retry_wait"].includes(item.status)));

async function load() {
  loading.value = true; error.value = "";
  try {
    const query = new URLSearchParams();
    if (filters.status) query.set("status", filters.status);
    if (filters.task_type) query.set("task_type", filters.task_type);
    const result = await api<JsonMap>(`/api/v1/tasks${query.size ? `?${query}` : ""}`);
    tasks.value = result.items || [];
    if (selected.value) selected.value = await api<JsonMap>(`/api/v1/tasks/${encodeURIComponent(selected.value.id)}`);
  } catch (value) { error.value = errorMessage(value, "任务读取失败"); }
  finally { loading.value = false; }
}

async function openTask(item: JsonMap) {
  busy.value = true; error.value = "";
  try { selected.value = await api<JsonMap>(`/api/v1/tasks/${encodeURIComponent(item.id)}`); }
  catch (value) { error.value = errorMessage(value); }
  finally { busy.value = false; }
}

async function retry(item: JsonMap) {
  busy.value = true; error.value = ""; success.value = "";
  try {
    const result = await api<JsonMap>(`/api/v1/tasks/${encodeURIComponent(item.id)}/retry`, {
      method: "POST", idempotencyKey: idempotencyKey("task-retry"), body: { reason: "manual_retry_from_task_center" },
    });
    selected.value = result.task; success.value = "重试已经受理，任务会沿用冻结输入"; await load();
  } catch (value) { error.value = errorMessage(value); }
  finally { busy.value = false; }
}

function resultLink(item: JsonMap): string | null {
  const result = item.result || {};
  const resourceId = result.resource_id || result.analysis_id || result.export_id;
  if (item.task_type === "analysis" && resourceId) return `/app/${auth.role || "seeker"}/report?analysis_id=${resourceId}`;
  if (item.task_type === "rewrite" && resourceId && auth.role === "seeker") return `/app/seeker/rewrite?rewrite_id=${resourceId}`;
  if (item.task_type.startsWith("interview_") && resourceId && auth.role === "seeker") return `/app/seeker/interview?interview_id=${resourceId}`;
  if (item.task_type === "resume_export" && resourceId) return `/api/v1/exports/${resourceId}/file`;
  return null;
}

onMounted(() => {
  load();
  pollingTimer = window.setInterval(() => { if (hasActive.value) load(); }, 3000);
});
onBeforeUnmount(() => { if (pollingTimer) window.clearInterval(pollingTimer); });
</script>

<template>
  <AppShell>
    <PageHeader eyebrow="TASK CENTER" title="任务中心" description="查看排队、执行、失败与重试；详情只保存版本引用和结果摘要，不复制简历或 JD 正文。"><button class="button outline small" type="button" @click="load">刷新</button></PageHeader>
    <AsyncState :loading="loading" :error="error" :success="success" />
    <form class="card filter-bar" @submit.prevent="load"><div class="field-group"><label>状态</label><select v-model="filters.status" class="select"><option value="">全部状态</option><option value="queued">排队中</option><option value="running">执行中</option><option value="succeeded">已完成</option><option value="failed">失败</option><option value="cancelled">已取消</option></select></div><div class="field-group"><label>任务类型</label><select v-model="filters.task_type" class="select"><option value="">全部类型</option><option value="document_parse">资料解析</option><option value="analysis">岗位分析</option><option value="rewrite">简历改写</option><option value="resume_export">PDF 导出</option><option value="interview_opening">面试开场</option><option value="interview_feedback">面试反馈</option><option value="interview_summary">面试总结</option></select></div><button class="button primary small" type="submit">筛选</button></form>

    <section v-if="selected" class="card pool-detail" style="margin-top:18px"><div class="card-head"><div><div class="eyebrow">TASK DETAIL</div><h3>{{ taskLabel(selected.task_type) }}</h3><p>{{ selected.id }} · {{ statusLabel(selected.status) }}</p></div><button class="button link-button small" type="button" @click="selected = null">收起</button></div><div class="item-meta"><span>当前步骤 {{ selected.current_step || '等待调度' }}</span><span>重试 {{ selected.retry_count || 0 }} 次</span><span>更新 {{ formatDate(selected.updated_at) }}</span><span v-if="selected.usage_reservation">预留 {{ selected.usage_reservation.count }} 次 {{ selected.usage_reservation.feature }}</span></div><div v-if="selected.failure" class="callout attention">{{ selected.failure.message || selected.failure.code || '任务失败' }}</div><div v-if="selected.input_versions?.length" class="version-strip"><span v-for="item in selected.input_versions" :key="`${item.resource_type}-${item.resource_id}`" class="version-chip">{{ item.resource_type }} · {{ item.resource_id }}<template v-if="item.version_no"> · v{{ item.version_no }}</template></span></div><details v-if="selected.result" class="raw-report"><summary>查看结果摘要</summary><pre>{{ JSON.stringify(selected.result, null, 2) }}</pre></details><div class="item-actions"><button v-if="selected.retryable" class="button primary small" :disabled="busy" type="button" data-action="retry-task" @click="retry(selected)">重试任务</button><a v-if="resultLink(selected)" class="button soft small" :href="resultLink(selected) || '#'">打开结果</a></div></section>

    <section class="card" style="margin-top:18px"><div class="card-head"><div><h3>最近任务</h3><p>{{ tasks.length }} 条 · 排队 {{ counts.queued || 0 }} · 失败 {{ counts.failed || 0 }} · 已完成 {{ counts.succeeded || 0 }}</p></div></div><div class="card-body task-list"><div v-if="!tasks.length" class="empty"><div><strong>暂无任务</strong><p>发起分析、改写、面试或 PDF 导出后会出现在这里。</p></div></div><article v-for="item in tasks" :key="item.id" class="task-row"><div><strong>{{ taskLabel(item.task_type) }}</strong><small>{{ statusLabel(item.status) }} · {{ item.current_step || '无当前步骤' }} · {{ formatDate(item.updated_at) }}</small><div class="progress-track"><i :style="{ width: `${item.progress?.display_percent || (item.status === 'succeeded' ? 100 : 0)}%` }" /></div></div><div class="item-actions"><span class="tag" :class="statusClass(item.status)">{{ statusLabel(item.status) }}</span><button class="button soft small" type="button" @click="openTask(item)">查看</button><button v-if="item.retryable" class="button outline small" :disabled="busy" type="button" data-action="retry-task" @click="retry(item)">重试</button></div></article></div></section>
  </AppShell>
</template>
