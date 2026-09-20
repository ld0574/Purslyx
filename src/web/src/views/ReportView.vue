<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";

import AppShell from "@/components/AppShell.vue";
import AsyncState from "@/components/AsyncState.vue";
import PageHeader from "@/components/PageHeader.vue";
import { api, deleteWithImpact } from "@/services/api";
import { useAuthStore } from "@/stores/auth";
import type { JsonMap } from "@/types";

const route = useRoute(); const router = useRouter(); const auth = useAuthStore();
const loading = ref(true); const busy = ref(false); const error = ref(""); const success = ref(""); const analysis = ref<JsonMap | null>(null);
const role = computed(() => auth.role || "seeker");
const report = computed(() => analysis.value?.report || {});
const freshness = computed(() => analysis.value?.input_freshness?.preference || null);
function statusLabel(value: string) { return ({ supported: "有依据", partially_supported: "部分依据", gap: "明确差距", needs_confirmation: "待确认", matched: "符合", conflict: "冲突", unknown: "未知" } as JsonMap)[value] || value; }
function conditionLabel(value: string) { return ({ job_title: "岗位方向", location: "地点", work_mode: "办公方式", salary: "薪资" } as JsonMap)[value] || value; }
function coverageText(value: unknown): string {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? `${Math.round(numeric * 100)}%` : "—";
}
function scoreText(value: unknown, coverage: unknown): string {
  const numeric = Number(value);
  return !Number.isFinite(numeric) || (numeric === 0 && Number(coverage) === 0) ? "待补充" : String(numeric);
}
const scoreStyle = computed(() => ({ "--score": String(Number(analysis.value?.ability_score || 0)) }));
async function load() {
  loading.value = true; error.value = "";
  try {
    let id = String(route.query.analysis_id || "");
    if (!id) id = ((await api<JsonMap>("/api/v1/analyses")).items || [])[0]?.id || "";
    if (!id) throw new Error("还没有可查看的分析报告");
    analysis.value = await api<JsonMap>(`/api/v1/analyses/${encodeURIComponent(id)}`);
  } catch (value) { error.value = value instanceof Error ? value.message : "报告读取失败"; }
  finally { loading.value = false; }
}
async function deleteAnalysis() {
  if (!analysis.value) return;
  busy.value = true; error.value = ""; success.value = "";
  try {
    const deleted = await deleteWithImpact(
      `/api/v1/analyses/${encodeURIComponent(analysis.value.id)}`,
      (impact) => `删除这份报告将同时删除 ${impact.affected?.rewrites || 0} 次改写和 ${impact.affected?.interviews || 0} 场面试；岗位和输入资料仍会保留。确认删除？`,
    );
    if (!deleted) return;
    success.value = "报告及其关联结果已删除";
    analysis.value = null;
    await router.replace(`/app/${role.value}/dashboard`);
  } catch (value) { error.value = value instanceof Error ? value.message : "报告删除失败"; }
  finally { busy.value = false; }
}
onMounted(load);
</script>

<template><AppShell><PageHeader eyebrow="MATCH REPORT" title="匹配报告" description="能力评分与岗位条件分开呈现；每条结论保留输入和规则版本。"><button class="button outline small" @click="load">刷新</button></PageHeader><AsyncState :loading="loading" :error="error" :success="success" />
  <template v-if="analysis"><div v-if="freshness?.status === 'outdated'" class="callout opportunity" style="margin-bottom:18px"><strong>岗位期望已有新版本。</strong>&nbsp;本报告仍使用 v{{ freshness.used_version_no }}；最新 v{{ freshness.latest_version_no }} 尚未应用。旧报告不被覆盖，重新分析会消耗 1 次。</div><div v-else-if="freshness?.status === 'source_archived'" class="callout attention" style="margin-bottom:18px">原岗位期望已归档；本报告继续展示生成时冻结的版本。</div>
  <section class="card"><div class="card-body"><div class="report-top"><div class="score-block" :style="scoreStyle"><div class="score-copy"><strong>{{ scoreText(analysis.ability_score, analysis.evidence_coverage) }}</strong><span>能力综合分</span></div></div><div class="report-summary"><span class="tag brand">{{ statusLabel(report.overall_advice?.status || analysis.status) }}</span><h3>{{ report.overall_advice?.text || '报告已生成' }}</h3><p>{{ (report.overall_advice?.next_steps || []).join(' ') || '请结合证据和条件对照做人工判断。' }}</p><div class="report-metrics"><div class="metric-pill"><strong>{{ coverageText(analysis.evidence_coverage) }}</strong><span>证据覆盖率</span></div><div class="metric-pill"><strong>{{ analysis.job_category }}</strong><span>岗位类别</span></div><div class="metric-pill"><strong>{{ analysis.scoring_rule_version }}</strong><span>评分规则</span></div></div><div v-if="role === 'seeker'" class="item-actions" style="margin-top:16px"><RouterLink class="button primary small" :to="{ path: '/app/seeker/rewrite', query: { analysis_id: analysis.id } }">补充事实与改写</RouterLink><RouterLink class="button soft small" :to="{ path: '/app/seeker/variants', query: { analysis_id: analysis.id } }">制作岗位版简历</RouterLink><RouterLink class="button soft small" :to="{ path: '/app/seeker/interview', query: { analysis_id: analysis.id } }">开始面试练习</RouterLink></div></div></div>
    <div v-if="report.ai_insights?.summary" class="ai-insight"><div class="ai-insight-head"><div><div class="eyebrow">AI EVIDENCE SYNTHESIS</div><h3>模型洞察（可回到下方原文证据）</h3></div><div class="item-actions"><span class="tag brand">真实模型</span><span v-if="report.ai_insights.model_score != null" class="tag neutral">模型参考 {{ report.ai_insights.model_score }} 分</span></div></div><p class="ai-insight-summary">{{ report.ai_insights.summary }}</p><div class="insight-columns"><div v-if="report.ai_insights.strengths?.length"><strong>可迁移优势</strong><ul><li v-for="item in report.ai_insights.strengths" :key="`strength-${item}`">{{ item }}</li></ul></div><div v-if="report.ai_insights.risks?.length"><strong>风险与缺口</strong><ul><li v-for="item in report.ai_insights.risks" :key="`risk-${item}`">{{ item }}</li></ul></div><div v-if="report.ai_insights.recommended_actions?.length"><strong>下一步动作</strong><ul><li v-for="item in report.ai_insights.recommended_actions" :key="`action-${item}`">{{ item }}</li></ul></div></div></div>
    <div class="report-grid"><div class="report-card"><h3>能力维度与逐条依据</h3><section v-for="dimension in report.dimensions || []" :key="dimension.key" class="dimension"><div class="dimension-head"><strong>{{ dimension.label }}</strong><span>{{ dimension.score ?? '—' }} 分</span></div><div class="bar"><i :style="{ width: `${dimension.score || 0}%` }" /></div><article v-for="requirement in dimension.requirements || []" :key="requirement.requirement_id" class="requirement"><div class="requirement-top"><span class="tag" :class="requirement.status === 'supported' ? 'success' : requirement.status === 'needs_confirmation' ? 'opportunity' : 'attention'">{{ statusLabel(requirement.status) }}</span><p>{{ requirement.job_quote }}</p></div><div v-for="evidence in requirement.evidence || []" :key="evidence.segment_key" class="evidence">{{ evidence.quote }}</div><div v-if="!requirement.evidence?.length" class="evidence missing">{{ requirement.explanation }}</div></article></section></div>
      <div class="report-card"><h3>岗位条件对照</h3><div v-for="condition in report.conditions || []" :key="condition.condition" class="condition-row"><strong>{{ conditionLabel(condition.condition) }}</strong><span class="tag" :class="condition.status === 'matched' ? 'success' : condition.status === 'conflict' ? 'attention' : 'opportunity'">{{ statusLabel(condition.status) }}</span><p>{{ condition.explanation }}</p></div></div>
      <div class="report-card"><h3>待核实事项</h3><div class="followup-list"><article v-for="item in report.verification_items || []" :key="item.code || item.question" class="followup-item"><strong>{{ item.question || item.title }}</strong><p>{{ item.reason || item.explanation }}</p></article></div></div><div class="report-card"><h3>针对性面试问题</h3><div class="followup-list"><article v-for="item in report.interview_questions || []" :key="item.question_text" class="followup-item"><strong>{{ item.question_text }}</strong><small>{{ item.basis?.reason || '来自本次报告的证据缺口' }}</small></article></div></div>
      <div class="report-card full"><h3>输入版本与可追溯信息</h3><div class="version-strip"><span v-for="item in analysis.input_versions || []" :key="item.id" class="version-chip">{{ item.type }} · v{{ item.version_no || '—' }} · {{ item.id }}</span><span class="version-chip">analysis · {{ analysis.id }}</span></div><details class="raw-report"><summary>展开完整报告 JSON</summary><pre>{{ JSON.stringify(analysis, null, 2) }}</pre></details><div class="item-actions" style="margin-top:14px"><button class="button link-button small" :disabled="busy" type="button" @click="deleteAnalysis">删除这份报告</button></div></div>
    </div></div></section></template>
</AppShell></template>
