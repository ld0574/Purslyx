<script setup lang="ts">
import { onMounted, reactive, ref } from "vue";

import AppShell from "@/components/AppShell.vue";
import AsyncState from "@/components/AsyncState.vue";
import PageHeader from "@/components/PageHeader.vue";
import { api, idempotencyKey } from "@/services/api";
import type { JsonMap } from "@/types";
import { errorMessage } from "@/utils/format";

const loading = ref(true);
const busy = ref(false);
const error = ref("");
const success = ref("");
const stats = ref<JsonMap>({ summary: {}, metrics: [] });
const date = ref(new Intl.DateTimeFormat("sv-SE", { timeZone: "Asia/Shanghai" }).format(new Date()));
const feedback = reactive({ feedback_type: "suggestion", content: "", rating: 5, payment_intent: "not_answered" });

async function load() {
  loading.value = true; error.value = "";
  try { stats.value = await api<JsonMap>(`/api/v1/stats/me?date=${encodeURIComponent(date.value)}`); }
  catch (value) { error.value = errorMessage(value, "个人统计读取失败"); }
  finally { loading.value = false; }
}

async function submitFeedback() {
  busy.value = true; error.value = ""; success.value = "";
  try {
    await api("/api/v1/feedback", { method: "POST", idempotencyKey: idempotencyKey("feedback"), body: {
      feedback_type: feedback.feedback_type,
      content: feedback.content,
      rating: feedback.rating,
      payment_intent: feedback.feedback_type === "payment_intent" ? feedback.payment_intent : null,
      context_type: "general",
    } });
    feedback.content = ""; success.value = "感谢反馈，内容已进入产品反馈队列";
  } catch (value) { error.value = errorMessage(value); }
  finally { busy.value = false; }
}

onMounted(load);
</script>

<template>
  <AppShell>
    <PageHeader eyebrow="PERSONAL STATS" title="统计与反馈" description="去投递点击只代表 Purslyx 已记录并发起跳转，不冒充外部平台投递结果。"><input v-model="date" class="field compact-date" type="date" @change="load" /><button class="button outline small" type="button" @click="load">刷新</button></PageHeader>
    <AsyncState :loading="loading" :error="error" :success="success" />
    <section v-if="!loading" class="metric-grid"><article v-for="item in stats.metrics || []" :key="item.key" class="metric-card"><div class="eyebrow">{{ item.label }}</div><strong>{{ item.value }}</strong><span>{{ item.definition }}</span></article><article class="metric-card"><div class="eyebrow">岗位</div><strong>{{ stats.summary?.job_pool_items || 0 }}</strong><span>当前匹配池岗位</span></article><article class="metric-card"><div class="eyebrow">报告</div><strong>{{ stats.summary?.completed_analyses || 0 }}</strong><span>当日完成分析</span></article><article class="metric-card"><div class="eyebrow">采用</div><strong>{{ stats.summary?.adopted_rewrites || 0 }}</strong><span>当前采用改写</span></article><article v-if="stats.registration_role === 'seeker'" class="metric-card"><div class="eyebrow">面试</div><strong>{{ stats.summary?.interviews || 0 }}</strong><span>累计面试练习</span></article></section>
    <section class="card" style="margin-top:18px"><div class="card-head"><div><h3>给 Purslyx 一句话反馈</h3><p>反馈与统计分开记录，不会改变已经生成的报告。</p></div></div><form id="feedback-form" class="card-body form-card" @submit.prevent="submitFeedback"><div class="form-row"><div class="field-group"><label>反馈类型</label><select v-model="feedback.feedback_type" class="select"><option value="issue">遇到问题</option><option value="suggestion">产品建议</option><option value="payment_intent">付费意愿</option><option value="other">其他</option></select></div><div class="field-group"><label>整体评分</label><select v-model.number="feedback.rating" class="select"><option v-for="value in [5,4,3,2,1]" :key="value" :value="value">{{ value }} 分</option></select></div></div><div v-if="feedback.feedback_type === 'payment_intent'" class="field-group"><label>付费意愿</label><select v-model="feedback.payment_intent" class="select"><option value="willing">愿意</option><option value="depends_on_price">取决于价格</option><option value="unwilling">暂不愿意</option><option value="not_answered">暂不回答</option></select></div><div class="field-group"><label>反馈内容</label><textarea v-model="feedback.content" class="textarea" :required="feedback.feedback_type !== 'payment_intent'" placeholder="哪一步最有帮助？还有哪里需要更清楚？" /></div><button class="button primary" :disabled="busy" type="submit">提交反馈</button></form></section>
    <p class="footer-note">统计时区：{{ stats.timezone }} · 口径：{{ stats.rule_version }} · 日期：{{ stats.date }}</p>
  </AppShell>
</template>
