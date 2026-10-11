<script setup lang="ts">
import { computed, inject, onBeforeUnmount, onMounted, ref } from "vue";
import { useRouter } from "vue-router";

import AutoMatchingDialog from "@/components/AutoMatchingDialog.vue";
import JobHandlingDialog from "@/components/JobHandlingDialog.vue";
import JobHandlingUndo from "@/components/JobHandlingUndo.vue";
import JobOpportunityDialog from "@/components/JobOpportunityDialog.vue";
import { api, deleteWithImpact, idempotencyKey } from "@/services/api";
import type { JsonMap } from "@/types";
import { errorMessage, formatDateTime } from "@/utils/format";

const router = useRouter();
const data = ref<JsonMap>({ automation: {}, progress: {}, items: [], counts: { tiers: {}, statuses: {} }, page: {} });
const loading = ref(true); const error = ref(""); const success = ref("");
const settingsOpen = ref(false); const tier = ref(""); const view = ref("unhandled");
const searchDraft = ref(""); const search = ref(""); const cursor = ref(""); const history = ref<string[]>([]);
const hasUpdates = ref(false); const applying = ref("");
const detail = ref<JsonMap | null>(null); const handlingItem = ref<JsonMap | null>(null); const lastChange = ref<JsonMap | null>(null);
const detailDialog = ref<InstanceType<typeof JobOpportunityDialog> | null>(null);
const tiers = [{ key: "priority", label: "优先投" }, { key: "consider", label: "可以尝试" }, { key: "verify", label: "先核实" }, { key: "defer", label: "暂缓投" }];
const items = computed<JsonMap[]>(() => data.value.items || []);
const policy = computed(() => data.value.automation || {}); const progress = computed(() => data.value.progress || {});
const refreshUsage = inject<(() => Promise<void>) | undefined>("refreshUsage", undefined);
let timer: number | undefined; let sequence = 0; let disposed = false; let firstFingerprint = "";
function fingerprint(value: JsonMap): string { return JSON.stringify([value.range, value.counts, (value.items || []).map((item: JsonMap) => [item.id, item.analysis_id, item.handling, item.source_update])]); }
function query(first = false): string {
  const params = new URLSearchParams({ view: view.value, limit: "20" });
  if (tier.value) params.set("tier", tier.value);
  if (search.value) params.set("search", search.value);
  if (!first && cursor.value) params.set("cursor", cursor.value);
  return params.toString();
}
async function load(quiet = false) {
  const current = ++sequence; const backgroundPage = quiet && Boolean(cursor.value);
  window.clearTimeout(timer);
  if (!quiet) loading.value = true;
  error.value = "";
  try {
    const result = await api<JsonMap>("/api/v1/job-pool/opportunities?" + query(backgroundPage));
    if (current !== sequence || disposed) return false;
    const previousAvailable = policy.value.available_analysis;
    if (backgroundPage) {
      if (fingerprint(result) !== firstFingerprint) hasUpdates.value = true;
      data.value = { ...data.value, automation: result.automation, progress: result.progress, waiting_reasons: result.waiting_reasons };
    } else {
      data.value = result;
      if (!cursor.value) { firstFingerprint = fingerprint(result); hasUpdates.value = false; }
    }
    if (previousAvailable != null && previousAvailable !== policy.value.available_analysis) void refreshUsage?.();
    return true;
  } catch (value) { if (current === sequence && !disposed) error.value = errorMessage(value, "待投机会读取失败"); return false; }
  finally {
    if (current === sequence && !disposed) {
      loading.value = false; window.clearTimeout(timer);
      if (Number(progress.value.active) || policy.value.enabled) timer = window.setTimeout(() => { void load(true); }, Number(progress.value.active) || Number(progress.value.waiting) ? 10_000 : 30_000);
    }
  }
}
async function firstPage() { cursor.value = ""; history.value = []; await load(); }
async function setView(value: string) { view.value = value; await firstPage(); }
async function setTier(value: string) { tier.value = value; await firstPage(); }
async function submitSearch() { search.value = searchDraft.value.trim(); await firstPage(); }
async function nextPage() {
  if (!data.value.page?.has_more || loading.value) return;
  const old = cursor.value; cursor.value = data.value.page.next_cursor;
  const generation = sequence + 1; const loaded = await load();
  if (generation !== sequence) return;
  if (loaded) history.value.push(old); else cursor.value = old;
}
async function previousPage() {
  if (!history.value.length || loading.value) return;
  const old = cursor.value; cursor.value = history.value[history.value.length - 1];
  const generation = sequence + 1; const loaded = await load();
  if (generation !== sequence) return;
  if (loaded) history.value.pop(); else cursor.value = old;
}
function condition(item: JsonMap, key: string): string {
  const value = item.job_conditions?.[key];
  if (key === "work_mode") return ({ onsite: "现场办公", hybrid: "混合办公", remote: "远程办公" } as JsonMap)[value] || "未披露";
  return value && value !== "unknown" ? String(value) : "未披露";
}
async function handlingChanged(change: JsonMap) {
  lastChange.value = change;
  data.value.items = items.value.map(item => item.id === change.item_id ? { ...item, handling: change.handling } : item).filter(item => view.value === "all" || (view.value === "unhandled" ? ["pending", "verifying"].includes(item.handling?.status || "pending") : ["applied", "dismissed"].includes(item.handling?.status)));
  success.value = "处理状态已保存为" + change.handling.label + "；没有消耗次数。";
  await load();
}
async function undone(change: JsonMap) { lastChange.value = null; success.value = "已恢复为" + change.handling.label; await Promise.all([load(), detailDialog.value?.refresh()]); }
async function remove(item: JsonMap) {
  try {
    if (!await deleteWithImpact("/api/v1/job-pool/items/" + encodeURIComponent(item.id), impact => "删除岗位将同时删除 " + (impact.affected?.analyses || 0) + " 份报告及关联结果。确认删除？")) return;
    detail.value = null; await load();
  } catch (value) { error.value = errorMessage(value); }
}
async function goApply(item: JsonMap) {
  if (applying.value || !item.apply_action?.available || !item.source_url) return;
  error.value = ""; success.value = "";
  const destination = window.open("about:blank", "_blank");
  if (!destination) { error.value = "浏览器拦截了新标签页，请允许本站打开弹窗后重试"; return; }
  destination.opener = null; applying.value = item.id;
  try {
    const response = await api<Response>("/api/v1/job-pool/items/" + encodeURIComponent(item.apply_pool_item_id || item.id) + "/go-to-apply", { method: "POST", idempotencyKey: idempotencyKey("opportunity-apply"), body: { click_token: item.apply_action.click_token }, raw: true, redirect: "manual" });
    if (response.status !== 303 && response.type !== "opaqueredirect") {
      const body = await response.json().catch(() => ({})); const trace = body.error?.request_id || response.headers.get("X-Request-ID");
      throw new Error((body.error?.message || "去投递点击未能保存，请刷新后重试") + (trace ? "（请求 ID：" + trace + "）" : ""));
    }
    destination.location.replace(item.source_url);
    if (!disposed) success.value = "已打开原岗位，是否提交投递由你在招聘平台确认；处理状态未改变。";
  } catch (value) { destination.close(); if (!disposed) error.value = errorMessage(value); }
  finally { if (!disposed) applying.value = ""; }
}
async function settingsSaved() { success.value = "设置已保存；新岗位会按设置自动处理。"; await firstPage(); }
onMounted(() => { void load(); });
onBeforeUnmount(() => { disposed = true; sequence++; window.clearTimeout(timer); });
defineExpose({ refresh: () => cursor.value ? load(true) : load() });
</script>

<template>
  <section class="card daily-recommendations">
    <div class="card-head daily-recommendations-head"><div><h3>待投机会</h3><p>近 30 个上海自然日内完成匹配的岗位，跨日保留到你手动处理。</p></div><div class="item-actions"><button class="button outline small" type="button" @click="firstPage">刷新第一页</button><button class="button soft small" type="button" @click="settingsOpen = true">设置自动处理</button></div></div>
    <div class="card-body">
      <div class="auto-processing-state"><span class="tag" :class="policy.enabled && policy.valid ? 'success' : 'neutral'">{{ policy.enabled ? (policy.valid ? '自动处理已开启' : '设置需要更新') : '自动处理未开启' }}</span><span class="micro">今日已安排 {{ policy.today_scheduled || 0 }}/{{ policy.daily_limit || 10 }} 个，余额在左侧用量入口查看</span></div>
      <div class="daily-progress"><span>今日抓取<strong>{{ progress.captured || 0 }}</strong></span><span>今日完成匹配<strong>{{ progress.completed || 0 }}</strong></span><span>处理中<strong>{{ progress.active || 0 }}</strong></span><span>等待<strong>{{ progress.waiting || 0 }}</strong></span><RouterLink v-if="progress.failed" class="daily-failures" to="/app/seeker/tasks">失败 {{ progress.failed }} 个 · 查看并重试</RouterLink></div>
      <p v-for="reason in data.waiting_reasons || []" :key="reason.code" class="auto-waiting-reason">{{ reason.message }}（{{ reason.count }} 个）</p>
      <div class="recommendation-filters opportunity-views" role="group" aria-label="处理视图"><button v-for="option in [{ key: 'unhandled', label: '未处理' }, { key: 'handled', label: '已处理' }, { key: 'all', label: '全部' }]" :key="option.key" type="button" :class="{ active: view === option.key }" :aria-pressed="view === option.key" @click="setView(option.key)">{{ option.label }}</button></div>
      <div class="recommendation-filters" role="group" aria-label="投递建议筛选"><button type="button" :class="{ active: !tier }" :aria-pressed="!tier" @click="setTier('')">全部建议</button><button v-for="option in tiers" :key="option.key" type="button" :class="{ active: tier === option.key }" :aria-pressed="tier === option.key" @click="setTier(option.key)">{{ option.label }} {{ data.counts?.tiers?.[option.key] || 0 }}</button></div>
      <form class="opportunity-search" @submit.prevent="submitSearch"><label class="sr-only" for="opportunity-search">岗位或公司关键词</label><input id="opportunity-search" v-model="searchDraft" class="field" maxlength="120" placeholder="搜索岗位或公司" /><button class="button outline small" type="submit">搜索</button></form>
      <p v-if="error" class="banner error" role="alert">{{ error }}</p><p v-if="success" class="banner success" role="status">{{ success }}</p>
      <JobHandlingUndo :change="lastChange" @undone="undone" />
      <div v-if="hasUpdates" class="callout opportunity" role="status">有新结果或处理状态更新。<button class="button link-button small" type="button" @click="firstPage">返回第一页刷新</button></div>
      <p v-if="loading" role="status">正在读取待投机会…</p>
      <div v-if="!items.length && !loading && !error" class="empty daily-empty"><div><strong>{{ view === 'unhandled' && !search && !tier ? '近 30 天暂无未处理机会' : '没有符合筛选的机会' }}</strong><p>更早的结果仍保留在岗位池，可以继续查看和管理。</p><div class="item-actions"><button class="button soft small" type="button" @click="settingsOpen = true">设置自动处理</button><RouterLink class="button outline small" to="/guide">查看抓取指引</RouterLink><RouterLink class="button outline small" to="/app/seeker/pool">进入岗位池</RouterLink></div></div></div>
      <div class="recommendation-list opportunity-list" :aria-busy="loading">
        <article v-for="item in items" :key="item.id" class="recommendation-item opportunity-row" :class="'recommendation-' + item.recommendation.tier">
          <div class="recommendation-title"><div><h4>{{ item.job_title || '未披露' }}</h4><p>{{ item.company_name || '未披露' }}</p></div><span class="tag" :class="item.recommendation.tier === 'priority' ? 'success' : 'neutral'">{{ item.recommendation.label }}{{ item.source_update?.updated ? '（旧版）' : '' }}</span></div>
          <div class="opportunity-conditions"><span>薪资：{{ condition(item, 'salary') }}</span><span>地点：{{ condition(item, 'location') }}</span><span>办公方式：{{ condition(item, 'work_mode') }}</span></div>
          <p class="recommendation-reason">{{ item.recommendation.reason }}</p><p v-if="item.source_update?.updated" class="opportunity-version-warning">JD 已更新，建议基于旧版</p>
          <div class="opportunity-row-footer"><div><span class="tag neutral">{{ item.handling?.label || '待投' }}</span><span class="micro">报告 {{ formatDateTime(item.completed_at) }}</span></div><div class="item-actions"><button class="button soft small" type="button" @click="detail = item">详情</button><button class="button outline small" type="button" @click="handlingItem = item">{{ view === 'handled' ? '恢复 / 修改状态' : '修改状态' }}</button><button v-if="item.apply_action?.available" class="button primary small" type="button" :disabled="!!applying" @click="goApply(item)">去投递</button></div></div>
        </article>
      </div>
      <div class="pool-pagination"><button class="button outline small" type="button" :disabled="!history.length || loading" @click="previousPage">上一页</button><span>第 {{ history.length + 1 }} 页 · 共 {{ data.counts?.total || 0 }} 条</span><button class="button outline small" type="button" :disabled="!data.page?.has_more || loading" @click="nextPage">下一页</button></div>
      <p class="micro daily-disclaimer">仅用于个人投递排序，不代表招聘结果；去投递点击不等于外部平台提交成功。</p>
    </div>
  </section>
  <AutoMatchingDialog :open="settingsOpen" @close="settingsOpen = false" @saved="settingsSaved" />
  <JobHandlingDialog :open="!!handlingItem" :item="handlingItem" @close="handlingItem = null" @saved="handlingChanged" />
  <JobOpportunityDialog ref="detailDialog" :open="!!detail" :item-id="detail?.id || ''" :analysis-id="detail?.analysis_id" @close="detail = null" @handling-changed="handlingChanged" @remove="remove" @match="item => router.push({ path: '/app/seeker/pool', query: { pool_item_id: item.id } })" />
</template>
