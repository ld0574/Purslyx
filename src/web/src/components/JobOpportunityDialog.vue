<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";

import AdminDialog from "@/components/AdminDialog.vue";
import JobHandlingDialog from "@/components/JobHandlingDialog.vue";
import { api, idempotencyKey } from "@/services/api";
import type { JsonMap } from "@/types";
import { errorMessage, formatDateTime, statusLabel } from "@/utils/format";

const props = defineProps<{ open: boolean; itemId: string; analysisId?: string }>();
const emit = defineEmits<{ close: []; handlingChanged: [change: JsonMap]; match: [item: JsonMap]; remove: [item: JsonMap] }>();
const item = ref<JsonMap | null>(null); const analysis = ref<JsonMap | null>(null);
const loading = ref(false); const applying = ref(false); const error = ref(""); const success = ref("");
const handlingOpen = ref(false);
let generation = 0;
const report = computed(() => analysis.value?.report || {});
const ready = computed(() => ["available", "succeeded"].includes(analysis.value?.status) && !!analysis.value?.report && !!analysis.value?.application_recommendation);
const snapshot = computed(() => ready.value ? analysis.value?.job_snapshot : null);
const fields = computed(() => (ready.value ? snapshot.value?.content || {} : item.value?.job_content || {}).job_fields || {});
const conditions = computed(() => ready.value ? snapshot.value?.conditions || {} : item.value?.job_conditions || {});
const updated = computed(() => snapshot.value?.source_update || item.value?.source_update || {});
const targetId = computed(() => updated.value.latest_pool_item_id || props.itemId);
const title = computed(() => ready.value ? snapshot.value?.title || '未披露' : item.value?.job_title || '岗位详情');
const recommendation = computed(() => analysis.value?.application_recommendation);
const active = computed(() => ["queued", "running", "retry_wait"].includes(item.value?.latest_analysis?.task?.status || item.value?.analysis_status));
function condition(key: string): string {
  const value = conditions.value[key];
  if (key === "work_mode") return ({ onsite: "现场办公", hybrid: "混合办公", remote: "远程办公" } as JsonMap)[value] || "未披露";
  return typeof value === "string" && value && value !== "unknown" ? value : "未披露";
}
function evidenceStatus(value: string): string { return ({ supported: "有依据", partially_supported: "部分依据", gap: "明确差距", needs_confirmation: "待确认", matched: "符合", conflict: "冲突", conflicted: "冲突", unknown: "未披露" } as JsonMap)[value] || "待确认"; }
async function load(quiet = false) {
  const current = ++generation;
  if (!quiet) { item.value = null; analysis.value = null; loading.value = true; }
  error.value = ""; success.value = "";
  const itemId = props.itemId; const analysisId = props.analysisId;
  try {
    const detail = await api<JsonMap>(`/api/v1/job-pool/items/${encodeURIComponent(itemId)}`);
    if (current !== generation) return;
    const reportId = analysisId || detail.analysis_history?.find((row: JsonMap) => row.readable !== false && ["available", "succeeded"].includes(row.status))?.id;
    const result = reportId ? await api<JsonMap>(`/api/v1/analyses/${encodeURIComponent(reportId)}`) : null;
    if (current !== generation) return;
    if (result?.job_pool_item_id !== undefined && result.job_pool_item_id !== detail.id) throw new Error("报告与岗位版本不一致，请刷新后重试");
    item.value = detail; analysis.value = result;
    return detail;
  } catch (value) { if (current === generation) error.value = errorMessage(value, "岗位详情读取失败"); }
  finally { if (current === generation) loading.value = false; }
}
watch(() => [props.open, props.itemId, props.analysisId], () => { generation++; handlingOpen.value = false; applying.value = false; if (props.open && props.itemId) void load(); }, { immediate: true });
onBeforeUnmount(() => { generation++; });
defineExpose({ refresh: () => props.open && props.itemId ? load(true) : Promise.resolve() });
function handlingChanged(change: JsonMap) { if (item.value) item.value.handling = change.handling; emit("handlingChanged", change); }
async function goApply() {
  if (!item.value?.source_url || applying.value) return;
  const current = generation;
  const destination = window.open("about:blank", "_blank");
  if (!destination) { error.value = "浏览器拦截了新标签页，请允许本站打开弹窗后重试"; return; }
  destination.opener = null; applying.value = true; error.value = "";
  try {
    const latest = targetId.value === item.value.id ? item.value : await api<JsonMap>(`/api/v1/job-pool/items/${encodeURIComponent(targetId.value)}`);
    if (current !== generation) { destination.close(); return; }
    if (!latest.apply_action?.available) throw new Error("原岗位链接当前不可用");
    const response = await api<Response>(`/api/v1/job-pool/items/${encodeURIComponent(latest.id)}/go-to-apply`, { method: "POST", idempotencyKey: idempotencyKey("opportunity-apply"), body: { click_token: latest.apply_action.click_token }, raw: true, redirect: "manual" });
    if (response.status !== 303 && response.type !== "opaqueredirect") {
      const body = await response.json().catch(() => ({})); const trace = body.error?.request_id || response.headers.get("X-Request-ID");
      throw new Error(`${body.error?.message || '去投递点击保存失败'}${trace ? `（请求 ID：${trace}）` : ''}`);
    }
    destination.location.replace(latest.source_url);
    if (current === generation) success.value = "已打开原岗位，处理状态未改变；提交后请手动确认已投。";
  } catch (value) { destination.close(); if (current === generation) error.value = errorMessage(value); }
  finally { if (current === generation) applying.value = false; }
}
</script>

<template>
  <AdminDialog :open="open" :title="title" wide @close="emit('close')">
    <p v-if="loading" role="status">正在读取岗位与报告…</p>
    <p v-if="error" class="error-text" role="alert">{{ error }}</p>
    <button v-if="error && !item" class="button outline small" type="button" @click="load()">重试读取</button>
    <p v-if="success" role="status">{{ success }}</p>
    <template v-if="item && !loading">
      <p>{{ (ready ? snapshot?.company_name : item.company_name) || '未披露' }} · 报告时间 {{ formatDateTime(analysis?.completed_at) }}</p>
      <div class="opportunity-conditions"><span>薪资：{{ condition('salary') }}</span><span>地点：{{ condition('location') }}</span><span>办公方式：{{ condition('work_mode') }}</span></div>
      <div v-if="updated.updated" class="callout attention">JD 已更新，建议基于旧版。以下 JD 和匹配依据均来自该报告的旧版本；重新匹配仍需确认扣次。</div>
      <div class="item-actions"><span class="tag neutral">{{ item.handling?.label || '待投' }}</span><button class="button soft small" type="button" @click="handlingOpen = true">修改处理状态</button><button v-if="item.source_url" class="button primary small" type="button" :disabled="applying" @click="goApply">去投递</button><RouterLink v-if="analysis?.id" class="button outline small" :to="{ path: '/app/seeker/report', query: { analysis_id: analysis.id } }">独立报告</RouterLink><button class="button outline small" type="button" :disabled="active" @click="emit('match', { ...item, id: targetId })">{{ ready ? '重新匹配（需确认扣次）' : '匹配（需确认扣次）' }}</button><button class="button link-button small" type="button" @click="emit('remove', item)">删除岗位</button></div>
      <p class="micro">去投递点击不等于外部平台提交成功。</p>
      <section v-if="recommendation && ready"><h4>{{ recommendation.label }}{{ updated.updated ? '（旧版）' : '' }}</h4><p>{{ recommendation.reason }}</p>
        <article v-for="(strength, index) in recommendation.strengths || []" :key="index" class="recommendation-evidence"><strong>{{ strength.requirement }}</strong><p>{{ strength.explanation }}</p><blockquote>原文依据：{{ strength.evidence }}</blockquote></article>
        <h4 v-if="recommendation.risks?.length">风险与边界</h4><ul><li v-for="risk in recommendation.risks || []" :key="risk">{{ risk }}</li></ul>
        <h4 v-if="recommendation.verification_items?.length">待核实事项</h4><ul><li v-for="question in recommendation.verification_items || []" :key="question">{{ question }}</li></ul>
      </section>
      <section v-if="!ready" class="callout opportunity"><strong>{{ item.latest_analysis?.status === 'failed' ? '匹配尚未完成' : statusLabel(item.analysis_status) }}</strong><p>{{ item.latest_analysis?.task?.failure?.message || item.blocking_reasons?.join('；') || '可从任务中心查看进度或重试原任务。' }}</p><p v-if="item.latest_analysis?.task?.failure?.request_id" class="micro">请求 ID：{{ item.latest_analysis.task.failure.request_id }}</p><RouterLink class="button outline small" to="/app/seeker/tasks">查看任务</RouterLink></section>
      <details class="opportunity-jd"><summary>{{ ready ? '报告使用的岗位 JD' : '岗位 JD' }}{{ updated.updated ? '（旧版）' : '' }}</summary><pre>{{ fields.source_text || '未披露' }}</pre></details>
      <details v-if="ready" class="opportunity-report"><summary>完整匹配依据{{ updated.updated ? '（旧版）' : '' }}</summary>
        <p>{{ report.overall_advice?.text }}</p><p>{{ report.ai_insights?.summary }}</p>
        <section v-for="dimension in report.dimensions || []" :key="dimension.key" class="dimension"><h4>{{ dimension.label }} · {{ dimension.score ?? '待补充' }}</h4><article v-for="requirement in dimension.requirements || []" :key="requirement.requirement_id" class="requirement"><strong>{{ requirement.job_quote }}</strong><span class="tag neutral">{{ evidenceStatus(requirement.status) }}</span><p>{{ requirement.explanation }}</p><blockquote v-for="(evidence, index) in requirement.evidence || []" :key="index" class="evidence">{{ evidence.quote }}</blockquote></article></section>
        <h4>岗位条件对照</h4><div v-for="(row, index) in report.conditions || []" :key="index" class="condition-row"><strong>{{ ({ job_title: '岗位方向', location: '地点', work_mode: '办公方式', salary: '薪资' } as JsonMap)[row.condition] || '岗位条件' }}</strong><span class="tag neutral">{{ evidenceStatus(row.status) }}</span><p>{{ row.explanation }}</p></div>
        <h4>待核实事项</h4><p v-for="(row, index) in report.verification_items || []" :key="index">{{ row.question }} · {{ row.reason || row.explanation }}</p>
        <details class="raw-report"><summary>版本与完整报告数据</summary><pre>{{ JSON.stringify(analysis, null, 2) }}</pre></details>
      </details>
    </template>
  </AdminDialog>
  <JobHandlingDialog :open="handlingOpen && open" :item="item" @close="handlingOpen = false" @saved="handlingChanged" />
</template>
