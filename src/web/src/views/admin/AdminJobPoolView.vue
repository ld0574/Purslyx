<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";

import AppShell from "@/components/AppShell.vue";
import AsyncState from "@/components/AsyncState.vue";
import PageHeader from "@/components/PageHeader.vue";
import { api, deleteWithImpact } from "@/services/api";
import { useAuthStore } from "@/stores/auth";
import type { JsonMap } from "@/types";
import { errorMessage, formatDateTime, statusClass, statusLabel } from "@/utils/format";

const auth = useAuthStore();
const loading = ref(true);
const busy = ref(false);
const error = ref("");
const success = ref("");
const items = ref<JsonMap[]>([]);
const selected = ref<JsonMap | null>(null);
const page = ref<JsonMap>({});
const currentCursor = ref("");
const cursorHistory = ref<string[]>([]);
const filters = reactive({
  search: "",
  platform: "",
  source_type: "browser_capture",
  analysis_status: "",
  scope: "active",
  created_from: "",
  created_to: "",
});

const canManage = computed(() => auth.permissions.has("admin.job_pool.manage"));
const currentPageNumber = computed(() => cursorHistory.value.length + 1);
const hasFilters = computed(() => Boolean(
  filters.search || filters.platform || filters.source_type !== "browser_capture" || filters.analysis_status || filters.scope !== "active" || filters.created_from || filters.created_to,
));

function iso(value: string): string {
  return value ? new Date(value).toISOString() : "";
}

function sourceLabel(item: JsonMap): string {
  if (item.source_type === "browser_capture") return item.platform === "liepin" ? "猎聘插件" : "BOSS 插件";
  return "手动入池";
}

function conditionValue(item: JsonMap, key: string): string {
  const value = item.job_conditions?.[key];
  if (value && typeof value === "object") {
    if (value.status === "specified" && value.min != null && value.max != null) return `${value.min} - ${value.max}`;
    return "未披露";
  }
  return value && value !== "unknown" ? String(value) : "未披露";
}

function message(value: unknown, fallback = "操作失败") {
  error.value = errorMessage(value, fallback);
}

function resetPaging() {
  currentCursor.value = "";
  cursorHistory.value = [];
}

async function load(reset = false) {
  if (reset) resetPaging();
  loading.value = !items.value.length;
  error.value = "";
  const query = new URLSearchParams({ limit: "20" });
  if (filters.search.trim()) query.set("search", filters.search.trim());
  if (filters.platform) query.set("platform", filters.platform);
  if (filters.source_type) query.set("source_type", filters.source_type);
  if (filters.analysis_status) query.set("status", filters.analysis_status);
  if (filters.scope === "deleted") query.set("status", "deleted");
  if (filters.scope === "all") query.set("include_deleted", "true");
  if (filters.created_from) query.set("created_from", iso(filters.created_from));
  if (filters.created_to) query.set("created_to", iso(filters.created_to));
  if (currentCursor.value) query.set("cursor", currentCursor.value);
  try {
    const result = await api<JsonMap>(`/api/v1/admin/job-pool/items?${query.toString()}`);
    items.value = result.items || [];
    page.value = result.page || {};
  } catch (value) {
    message(value, "抓取岗位读取失败");
  } finally {
    loading.value = false;
  }
}

async function applyFilters() {
  selected.value = null;
  await load(true);
}

async function nextPage() {
  if (!page.value.has_more || !page.value.next_cursor || busy.value) return;
  cursorHistory.value.push(currentCursor.value);
  currentCursor.value = page.value.next_cursor;
  await load();
}

async function previousPage() {
  if (!cursorHistory.value.length || busy.value) return;
  currentCursor.value = cursorHistory.value.pop() || "";
  await load();
}

async function openItem(item: JsonMap) {
  busy.value = true;
  error.value = "";
  try {
    selected.value = await api<JsonMap>(`/api/v1/admin/job-pool/items/${encodeURIComponent(item.id)}`);
  } catch (value) {
    message(value, "岗位详情读取失败");
  } finally {
    busy.value = false;
  }
}

async function retire(item: JsonMap) {
  busy.value = true;
  error.value = "";
  success.value = "";
  try {
    const deleted = await deleteWithImpact(
      `/api/v1/admin/job-pool/items/${encodeURIComponent(item.id)}`,
      (impact) => `下架“${item.job_title || "未命名岗位"}”将同时关闭 ${impact.affected?.analyses || 0} 份分析、${impact.affected?.rewrites || 0} 份改写和 ${impact.affected?.interviews || 0} 个面试，并取消 ${impact.affected?.running_tasks || 0} 个执行中任务。确认下架？`,
    );
    if (!deleted) return;
    selected.value = null;
    success.value = "岗位已下架，关联分析和任务已按影响范围处理";
    await load(true);
  } catch (value) {
    message(value, "岗位下架失败");
  } finally {
    busy.value = false;
  }
}

function clearFilters() {
  Object.assign(filters, { search: "", platform: "", source_type: "browser_capture", analysis_status: "", scope: "active", created_from: "", created_to: "" });
  void applyFilters();
}

onMounted(() => load(true));
</script>

<template>
  <AppShell>
    <PageHeader eyebrow="ADMIN JOB POOL" title="抓取岗位管理" description="统一查看插件抓取的 JD、来源账号、抓取时间和待补充条件；需要清理时可按影响范围安全下架。"><button class="button outline small" type="button" :disabled="busy" @click="load(true)">刷新</button></PageHeader>
    <AsyncState :loading="loading" :error="error" :success="success" />

    <form id="admin-job-pool-search" class="card filter-grid" @submit.prevent="applyFilters">
      <div class="field-group"><label for="admin-job-pool-search-text">搜索 JD、公司或来源账号</label><input id="admin-job-pool-search-text" v-model="filters.search" class="field" placeholder="岗位、公司、邮箱或正文关键词" /></div>
      <div class="field-group"><label>来源平台</label><select v-model="filters.platform" class="select"><option value="">全部平台</option><option value="boss">BOSS</option><option value="liepin">猎聘</option><option value="manual">手动入池</option></select></div>
      <div class="field-group"><label>来源类型</label><select v-model="filters.source_type" class="select"><option value="browser_capture">插件抓取</option><option value="manual">手动入池</option><option value="">全部类型</option></select></div>
      <div class="field-group"><label>匹配状态</label><select v-model="filters.analysis_status" class="select"><option value="">全部状态</option><option value="awaiting_requirements">待补齐条件</option><option value="queued">排队中</option><option value="running">执行中</option><option value="available">已有结果</option><option value="failed">匹配失败</option></select></div>
      <div class="field-group"><label>记录范围</label><select v-model="filters.scope" class="select"><option value="active">当前在池</option><option value="all">全部（含已下架）</option><option value="deleted">仅已下架</option></select></div>
      <div class="field-group"><label>入池时间起</label><input v-model="filters.created_from" class="field" type="datetime-local" /></div>
      <div class="field-group"><label>入池时间止</label><input v-model="filters.created_to" class="field" type="datetime-local" /></div>
      <div class="item-actions filter-actions"><button class="button primary small" type="submit">应用筛选</button><button v-if="hasFilters" class="button link-button small" type="button" @click="clearFilters">清空筛选</button></div>
    </form>

    <section class="card" style="margin-top:18px">
      <div class="card-head"><div><h3>JD 列表</h3><p>按入池时间倒序；列表只展示摘要，打开详情查看完整 JD。{{ page.has_more ? "可继续翻页。" : "" }}</p></div><span class="tag neutral">本页 {{ items.length }} 条</span></div>
      <div v-if="!items.length && !loading" class="empty"><div><strong>没有符合条件的岗位</strong><p>调整来源、状态或时间范围后重试。</p></div></div>
      <div v-else class="table-scroll admin-job-table"><table class="mini-table"><thead><tr><th>岗位 / 公司</th><th>来源账号</th><th>平台</th><th>抓取与入池时间</th><th>状态 / 待补充</th><th>操作</th></tr></thead><tbody><tr v-for="item in items" :key="item.id"><td><strong>{{ item.job_title }}</strong><small>{{ item.company_name || "未标注公司" }}</small><a v-if="item.source_url" class="micro admin-source-link" :href="item.source_url" target="_blank" rel="noreferrer">打开原岗位</a></td><td>{{ item.source_account?.email_masked || "—" }}<small>{{ item.source_account?.registration_role === "seeker" ? "求职账号" : "招聘账号" }}</small></td><td><span class="tag neutral">{{ sourceLabel(item) }}</span></td><td><strong>{{ formatDateTime(item.captured_at) }}</strong><small>入池 {{ formatDateTime(item.created_at) }}</small></td><td><span class="tag" :class="statusClass(item.analysis_status)">{{ item.in_pool ? statusLabel(item.analysis_status) : "已下架" }}</span><small v-if="item.missing_conditions?.length">待补充：{{ item.missing_conditions.join("、") }}</small><small v-else>条件字段完整或未标记缺失</small></td><td><div class="item-actions"><button class="button soft small" type="button" @click="openItem(item)">查看详情</button><button v-if="canManage && item.in_pool" class="button link-button small" type="button" :disabled="busy" @click="retire(item)">下架</button></div></td></tr></tbody></table></div>
      <div class="pool-pagination"><button class="button outline small" type="button" :disabled="!cursorHistory.length || busy" @click="previousPage">上一页</button><span>第 {{ currentPageNumber }} 页 · 本页 {{ items.length }} 条</span><button class="button outline small" type="button" :disabled="!page.has_more || busy" @click="nextPage">下一页</button></div>
    </section>

    <section v-if="selected" class="card admin-job-detail" style="margin-top:18px">
      <div class="card-head"><div><div class="eyebrow">JD DETAIL</div><h3>{{ selected.job_title }}</h3><p>{{ selected.company_name || "未标注公司" }} · {{ selected.source_account?.email_masked || "未知来源账号" }} · 抓取 {{ formatDateTime(selected.captured_at) }}</p></div><div class="item-actions"><span class="tag" :class="statusClass(selected.in_pool ? selected.analysis_status : 'deleted')">{{ selected.in_pool ? statusLabel(selected.analysis_status) : "已下架" }}</span><button class="button link-button small" type="button" @click="selected = null">关闭</button></div></div>
      <div class="card-body">
        <div class="job-condition-summary"><div><strong>来源</strong><span>{{ sourceLabel(selected) }}</span></div><div><strong>地点</strong><span>{{ conditionValue(selected, "location") }}</span></div><div><strong>办公方式</strong><span>{{ statusLabel(conditionValue(selected, "work_mode")) }}</span></div><div><strong>薪资</strong><span>{{ conditionValue(selected, "salary") }}</span></div><div><strong>分析数量</strong><span>{{ selected.analysis_count || selected.analysis_summary?.total || 0 }} 份</span></div><div><strong>待补充条件</strong><span>{{ selected.missing_conditions?.join("、") || "无" }}</span></div></div>
        <div v-if="selected.source_url" class="callout"><strong>原岗位链接</strong><a class="admin-source-link" :href="selected.source_url" target="_blank" rel="noreferrer">{{ selected.source_url }}</a></div>
        <div v-if="selected.job_description_text" class="admin-job-description"><h4>完整 JD 正文</h4><pre>{{ selected.job_description_text }}</pre></div>
        <details class="raw-report"><summary>查看采集字段与结构化内容</summary><pre>{{ JSON.stringify({ captured_fields: selected.captured_fields, job_content: selected.job_content, source_draft: selected.source_draft }, null, 2) }}</pre></details>
        <div v-if="selected.blocking_reasons?.length" class="callout opportunity"><strong>当前阻塞：</strong>{{ selected.blocking_reasons.join("；") }}</div>
        <section v-if="selected.analysis_history?.length" class="history-list" style="margin-top:18px"><h4>匹配历史</h4><div v-for="analysis in selected.analysis_history" :key="analysis.id" class="history-row"><div><strong>{{ analysis.id }}</strong><small>{{ statusLabel(analysis.status) }} · {{ analysis.ability_score == null ? "暂无评分" : `${analysis.ability_score} 分` }} · {{ formatDateTime(analysis.completed_at) }}</small></div><span v-if="analysis.deleted_at" class="tag attention">已清理</span></div></section>
        <div class="item-actions" style="margin-top:18px"><button v-if="canManage && selected.in_pool" class="button primary" type="button" :disabled="busy" @click="retire(selected)">下架这个岗位</button><span v-if="!canManage" class="micro">当前账号只有查看权限。</span></div>
      </div>
    </section>
  </AppShell>
</template>
