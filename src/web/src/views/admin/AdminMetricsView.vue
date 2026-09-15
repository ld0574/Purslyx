<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";

import AppShell from "@/components/AppShell.vue";
import AsyncState from "@/components/AsyncState.vue";
import PageHeader from "@/components/PageHeader.vue";
import { api, idempotencyKey } from "@/services/api";
import type { JsonMap } from "@/types";
import { errorMessage, formatDate, statusClass, statusLabel } from "@/utils/format";

function shanghaiDate(offsetDays = 0) {
  const date = new Date(Date.now() + offsetDays * 86400000);
  return new Intl.DateTimeFormat("sv-SE", { timeZone: "Asia/Shanghai" }).format(date);
}

const loading = ref(true);
const busy = ref(false);
const error = ref("");
const success = ref("");
const metrics = ref<JsonMap>({ totals: {}, series: [] });
const costs = ref<JsonMap>({ by_feature: [] });
const feedbackItems = ref<JsonMap[]>([]);
const filters = reactive({ date_from: shanghaiDate(-6), date_to: shanghaiDate(), registration_role: "all", feature: "all" });

const totalCards = computed(() => [
  ["注册账号", metrics.value.totals?.registered_accounts || 0, "所选日期范围内"],
  ["活跃账号", metrics.value.totals?.active_accounts || 0, "状态为正常"],
  ["成功任务", metrics.value.totals?.tasks_succeeded || 0, "完成并可读取"],
  ["失败任务", metrics.value.totals?.tasks_failed || 0, "最终失败"],
  ["次数结算", metrics.value.totals?.usage_settled || 0, "成功收费操作"],
  ["去投递点击", metrics.value.totals?.go_to_apply_clicks || 0, "仅代表发起跳转"],
  ["产品反馈", metrics.value.totals?.feedback_submitted || 0, "用户主动提交"],
  ["已知成本", costs.value.known_cost_usd == null ? "未知" : `$${Number(costs.value.known_cost_usd).toFixed(4)}`, `${costs.value.unknown_cost_calls || 0} 次成本未知`],
]);

async function load() {
  loading.value = true; error.value = "";
  try {
    const query = new URLSearchParams(filters);
    const [metricResult, costResult, feedbackResult] = await Promise.all([
      api<JsonMap>(`/api/v1/admin/metrics?${query}`),
      api<JsonMap>(`/api/v1/admin/costs?${query}`),
      api<JsonMap>("/api/v1/admin/feedback?limit=10"),
    ]);
    metrics.value = metricResult; costs.value = costResult; feedbackItems.value = feedbackResult.items || [];
  } catch (value) { error.value = errorMessage(value, "站点指标读取失败"); }
  finally { loading.value = false; }
}

async function updateFeedback(item: JsonMap, status: "reviewed" | "closed") {
  busy.value = true; error.value = "";
  try {
    const result = await api<JsonMap>(`/api/v1/admin/feedback/${encodeURIComponent(item.id)}`, {
      method: "PUT", idempotencyKey: idempotencyKey("feedback-status"), body: { status, base_revision: item.revision, reason: "管理端复核产品反馈" },
    });
    item.status = result.status; item.revision = result.revision; success.value = status === "closed" ? "反馈已关闭" : "反馈已标记为已复核";
  } catch (value) { error.value = errorMessage(value); }
  finally { busy.value = false; }
}

onMounted(load);
</script>

<template><AppShell><PageHeader eyebrow="ADMIN METRICS" title="站点概况" description="按上海自然日、注册身份和功能查看去标识聚合指标、任务质量与模型成本。"><button class="button outline small" type="button" @click="load">刷新</button></PageHeader><AsyncState :loading="loading" :error="error" :success="success" />
  <form class="card filter-grid" @submit.prevent="load"><div class="field-group"><label>开始日期</label><input v-model="filters.date_from" class="field" type="date" required /></div><div class="field-group"><label>结束日期</label><input v-model="filters.date_to" class="field" type="date" required /></div><div class="field-group"><label>注册身份</label><select v-model="filters.registration_role" class="select"><option value="all">全部</option><option value="seeker">求职</option><option value="recruiter">招聘</option></select></div><div class="field-group"><label>功能</label><select v-model="filters.feature" class="select"><option value="all">全部</option><option value="analysis">岗位分析</option><option value="rewrite">简历改写</option><option value="interview">面试</option><option value="import">资料导入</option></select></div><button class="button primary small" type="submit">应用筛选</button></form>
  <template v-if="!loading"><section class="admin-grid" style="margin-top:18px"><article v-for="card in totalCards" :key="String(card[0])" class="admin-stat"><div class="eyebrow">{{ card[0] }}</div><strong>{{ card[1] }}</strong><span>{{ card[2] }}</span></article></section>
  <div class="grid-2" style="margin-top:18px"><section class="card"><div class="card-head"><div><h3>每日任务趋势</h3><p>成功、失败、重试、平均耗时与 P95。</p></div></div><div class="card-body table-scroll"><table class="mini-table"><thead><tr><th>日期</th><th>成功</th><th>失败</th><th>重试</th><th>P95</th></tr></thead><tbody><tr v-for="item in metrics.series || []" :key="item.date"><td>{{ item.date }}</td><td>{{ item.tasks_succeeded }}</td><td>{{ item.tasks_failed }}</td><td>{{ item.task_retries }}</td><td>{{ item.p95_duration_ms == null ? '—' : `${item.p95_duration_ms}ms` }}</td></tr></tbody></table></div></section><section class="card"><div class="card-head"><div><h3>分功能模型成本</h3><p>未知成本不会被当成 0。</p></div></div><div class="card-body table-scroll"><table class="mini-table"><thead><tr><th>功能</th><th>调用</th><th>成本 USD</th></tr></thead><tbody><tr v-for="item in costs.by_feature || []" :key="item.feature"><td>{{ item.feature }}</td><td>{{ item.calls }}</td><td>{{ item.cost_usd_exact || '未知' }}</td></tr></tbody></table></div></section></div>
  <section class="card" style="margin-top:18px"><div class="card-head"><div><h3>最近产品反馈</h3><p>内容仅用于产品复核，可更新处理状态。</p></div></div><div class="card-body admin-list"><div v-if="!feedbackItems.length" class="empty"><div><strong>暂无反馈</strong></div></div><article v-for="item in feedbackItems" :key="item.id" class="admin-row"><div><strong>{{ item.feedback_type }} · {{ item.rating ? `${item.rating} 分` : '未评分' }}</strong><small>{{ item.account || '匿名摘要' }} · {{ item.content_preview || item.payment_intent || '无文字内容' }} · {{ formatDate(item.created_at) }}</small></div><div class="item-actions"><span class="tag" :class="statusClass(item.status)">{{ statusLabel(item.status) }}</span><button v-if="item.status === 'new'" class="button soft small" :disabled="busy" @click="updateFeedback(item, 'reviewed')">标记已复核</button><button v-if="item.status !== 'closed'" class="button link-button small" :disabled="busy" @click="updateFeedback(item, 'closed')">关闭</button></div></article></div></section><p class="footer-note">指标水位：{{ formatDate(metrics.source_watermark_at) }} · 成本价格水位：{{ formatDate(costs.pricing_watermark_at) }}</p></template>
</AppShell></template>
