<script setup lang="ts">
import { computed, inject, onBeforeUnmount, onMounted, ref } from "vue";

import AutoMatchingDialog from "@/components/AutoMatchingDialog.vue";
import { api, idempotencyKey } from "@/services/api";
import type { JsonMap } from "@/types";
import { errorMessage } from "@/utils/format";

const data = ref<JsonMap>({ automation: {}, progress: {}, items: [], tier_counts: {} });
const loading = ref(true);
const error = ref("");
const success = ref("");
const settingsOpen = ref(false);
const tier = ref("all");
const applying = ref("");
const tiers = [{ key: "priority", label: "优先投" }, { key: "consider", label: "可以尝试" }, { key: "verify", label: "先核实" }, { key: "defer", label: "暂缓投" }];
const items = computed(() => (data.value.items || []).filter((item: JsonMap) => tier.value === "all" || item.recommendation?.tier === tier.value));
const policy = computed(() => data.value.automation || {});
const progress = computed(() => data.value.progress || {});
let timer: number | undefined;
let sequence = 0;
let disposed = false;
const refreshUsage = inject<(() => Promise<void>) | undefined>("refreshUsage", undefined);

async function load(quiet = false) {
  const current = ++sequence;
  if (!quiet) loading.value = true;
  error.value = "";
  try {
    const result = await api<JsonMap>("/api/v1/job-pool/recommendations");
    if (current !== sequence || disposed) return;
    const previousAvailable = policy.value.available_analysis;
    data.value = result;
    if (previousAvailable != null && previousAvailable !== policy.value.available_analysis) void refreshUsage?.();
  } catch (value) { if (current === sequence && !disposed) error.value = errorMessage(value, "今日建议读取失败"); }
  finally {
    if (current === sequence && !disposed) {
      loading.value = false;
      window.clearTimeout(timer);
      if (Number(progress.value.active) || policy.value.enabled) timer = window.setTimeout(() => { void load(true); }, Number(progress.value.active) || Number(progress.value.waiting) ? 10_000 : 30_000);
    }
  }
}
async function goApply(item: JsonMap) {
  if (applying.value || !item.apply_action?.available || !item.source_url) return;
  error.value = ""; success.value = "";
  const destination = window.open("about:blank", "_blank");
  if (!destination) { error.value = "浏览器拦截了新标签页，请允许本站打开弹窗后重试"; return; }
  destination.opener = null;
  applying.value = item.id;
  try {
    const response = await api<Response>(`/api/v1/job-pool/items/${encodeURIComponent(item.id)}/go-to-apply`, { method: "POST", idempotencyKey: idempotencyKey("recommendation-apply"), body: { click_token: item.apply_action.click_token }, raw: true, redirect: "manual" });
    if (response.status !== 303 && response.type !== "opaqueredirect") {
      const body = await response.json().catch(() => ({}));
      const trace = body.error?.request_id || response.headers.get("X-Request-ID");
      throw new Error(`${body.error?.message || "去投递点击未能保存，请刷新后重试"}${trace ? `（请求 ID：${trace}）` : ""}`);
    }
    destination.location.replace(item.source_url);
    success.value = "已打开原岗位，是否提交投递由你在招聘平台确认。";
  } catch (value) { destination.close(); error.value = errorMessage(value); }
  finally { applying.value = ""; }
}
async function settingsSaved() { success.value = "设置已保存；新岗位会按设置自动处理。"; await load(); }
onMounted(() => { void load(); });
onBeforeUnmount(() => { disposed = true; sequence++; window.clearTimeout(timer); });
defineExpose({ refresh: () => load() });
</script>

<template>
  <section class="card daily-recommendations">
    <div class="card-head daily-recommendations-head"><div><h3>今日投递建议</h3><p>抓到岗位即自动解析与匹配，把时间留给值得投递的机会。</p></div><div class="item-actions"><button class="button outline small" type="button" @click="load()">刷新建议</button><button class="button primary small" type="button" @click="settingsOpen = true">{{ policy.enabled ? "自动处理设置" : "开启自动处理" }}</button></div></div>
    <div class="card-body">
      <p v-if="error" class="banner error" role="alert">{{ error }}</p><p v-if="success" class="banner success" role="status">{{ success }}</p>
      <p v-if="loading" class="micro" role="status">读取今日建议…</p>
      <template v-else>
        <div class="auto-processing-state"><span class="tag" :class="policy.enabled && policy.valid ? 'success' : 'neutral'">{{ policy.enabled ? policy.valid ? "自动处理已开启" : "设置需要更新" : policy.confirmed_at ? "自动处理已暂停" : "尚未开启自动处理" }}</span><span v-if="policy.enabled" class="micro">今日自动领取 {{ policy.today_scheduled || 0 }} / {{ policy.daily_limit || 10 }} 个</span><span class="micro">余额在左侧用量入口查看</span></div>
        <div class="daily-progress" aria-label="自动处理进度"><span>今日抓取 <strong>{{ progress.captured || 0 }}</strong></span><span>可查看建议 <strong>{{ progress.completed || 0 }}</strong></span><span>处理中 <strong>{{ progress.active || 0 }}</strong></span><span>保留待处理 <strong>{{ progress.waiting || 0 }}</strong></span><RouterLink v-if="progress.failed" to="/app/seeker/tasks" class="daily-failures">失败 {{ progress.failed }} 个，查看并重试 →</RouterLink></div>
        <p v-for="reason in data.waiting_reasons || []" :key="reason.code" class="auto-waiting-reason">{{ reason.count }} 个岗位：{{ reason.message }}</p>
        <div class="recommendation-filters" role="group" aria-label="投递建议筛选"><button type="button" :aria-pressed="tier === 'all'" :class="{ active: tier === 'all' }" @click="tier = 'all'">全部</button><button v-for="item in tiers" :key="item.key" type="button" :aria-pressed="tier === item.key" :class="{ active: tier === item.key }" @click="tier = item.key">{{ item.label }} {{ data.tier_counts?.[item.key] || 0 }}</button></div>
        <div v-if="!items.length" class="empty daily-empty"><div><strong>{{ data.items?.length ? "此分类暂时没有岗位" : progress.active ? "岗位正在处理，建议会自动更新" : progress.waiting ? "岗位已保留，恢复处理后会出现建议" : "今天还没有投递建议" }}</strong><p v-if="!data.items?.length">{{ policy.enabled ? "抓取新岗位后，系统逐条处理；处理中不会提前给出投递结论。" : "先确认简历和岗位期望，再开启自动处理。仍可在匹配池手动匹配。" }}</p><div class="item-actions"><button v-if="!policy.enabled" class="button primary small" type="button" @click="settingsOpen = true">设置自动处理</button><RouterLink class="button soft small" to="/guide">查看抓取指引</RouterLink><RouterLink class="button outline small" to="/app/seeker/pool">打开岗位池</RouterLink></div></div></div>
        <div class="recommendation-list"><article v-for="item in items" :key="item.id" class="recommendation-item" :class="`recommendation-${item.recommendation.tier}`">
          <div class="recommendation-title"><div><h4>{{ item.job_title }}</h4><p>{{ item.company_name || "公司信息待核实" }}</p></div><span class="tag" :class="item.recommendation.tier === 'priority' ? 'success' : item.recommendation.tier === 'consider' ? 'opportunity' : 'neutral'">{{ item.recommendation.label }}</span></div>
          <p class="recommendation-reason">{{ item.recommendation.reason }}</p><p class="micro">匹配依据分 {{ item.recommendation.ability_score ?? "暂不可判断" }} · 证据覆盖率 {{ item.recommendation.evidence_coverage == null ? "待确认" : `${Math.round(item.recommendation.evidence_coverage * 100)}%` }}</p>
          <div v-for="strength in item.recommendation.strengths || []" :key="strength.segment_key" class="recommendation-evidence"><strong>{{ strength.requirement }}</strong><p>{{ strength.explanation }}</p><blockquote>原文依据：{{ strength.evidence }}</blockquote></div>
          <div v-if="item.recommendation.risks?.length" class="recommendation-risks"><strong>风险与边界</strong><ul><li v-for="risk in item.recommendation.risks" :key="risk">{{ risk }}</li></ul></div>
          <details v-if="item.recommendation.verification_items?.length"><summary>投递前需要核实</summary><ul><li v-for="question in item.recommendation.verification_items" :key="question">{{ question }}</li></ul></details>
          <div class="item-actions"><button v-if="item.apply_action?.available" class="button primary small" type="button" :disabled="Boolean(applying)" @click="goApply(item)">{{ applying === item.id ? "记录点击中…" : "去投递" }}</button><RouterLink class="button soft small" :to="{ path: '/app/seeker/report', query: { analysis_id: item.analysis_id } }">查看完整依据</RouterLink><RouterLink class="button link-button small" :to="{ path: '/app/seeker/pool', query: { pool_item_id: item.id } }">岗位详情</RouterLink></div>
        </article></div>
        <p v-if="data.truncated" class="micro">当前展示部分建议，可到岗位池查看全部岗位与分析。</p>
        <p class="micro daily-disclaimer">按上海自然日显示今日新增或今日完成分析的岗位，待处理与失败包含之前保留的任务。建议仅用于个人投递排序，不代表录用概率；去投递点击不等于外部平台提交成功。</p>
      </template>
    </div>
    <AutoMatchingDialog :open="settingsOpen" @close="settingsOpen = false" @saved="settingsSaved" />
  </section>
</template>
