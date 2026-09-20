<script setup lang="ts">
import { computed, nextTick, onMounted, reactive, ref } from "vue";
import { useRoute } from "vue-router";

import AppShell from "@/components/AppShell.vue";
import AsyncState from "@/components/AsyncState.vue";
import PageHeader from "@/components/PageHeader.vue";
import { api, deleteWithImpact, idempotencyKey } from "@/services/api";
import type { JsonMap } from "@/types";
import { formatDateTime, statusClass, statusLabel } from "@/utils/format";

const route = useRoute();
const loading = ref(true);
const busy = ref(false);
const error = ref("");
const success = ref("");
const documents = ref<JsonMap[]>([]);
const preferences = ref<JsonMap[]>([]);
const items = ref<JsonMap[]>([]);
const selected = ref<JsonMap | null>(null);
const selectedIds = ref<string[]>([]);
const selectedResumeVersionId = ref("");
const searchText = ref("");
const jobTitleFilter = ref("");
const companyFilter = ref("");
const salaryMinFilter = ref("");
const salaryMaxFilter = ref("");
const createdFromFilter = ref("");
const createdToFilter = ref("");
const scoreMinFilter = ref("");
const scoreMaxFilter = ref("");
const statusFilter = ref("");
const platformFilter = ref("");
const sortFilter = ref("created_at");
const currentCursor = ref("");
const cursorHistory = ref<string[]>([]);
const page = reactive({ has_more: false, next_cursor: "", limit: 20 });
const resumeDialog = ref<HTMLDialogElement | null>(null);
const resumeDialogOpen = ref(false);
const resumeDialogSelection = ref("");
const matchingPoolIds = ref<string[]>([]);
const form = reactive({
  job_title: "",
  company_name: "",
  location_text: "",
  work_mode: "",
  salary_text: "",
  job_text: "",
});

const resumes = computed(() => documents.value.filter((item) => item.document_type === "resume" && item.latest_version));
const selectedFields = computed(() => selected.value?.job_content?.job_fields || selected.value?.job_content || {});
const selectedCount = computed(() => selectedIds.value.length);
const estimatedAnalysisCount = computed(() => selectedCount.value);
const allPageSelected = computed(() => items.value.length > 0 && items.value.every((item) => selectedIds.value.includes(item.id)));
const currentPageNumber = computed(() => cursorHistory.value.length + 1);
const hasFilters = computed(() => Boolean(
  searchText.value.trim()
  || jobTitleFilter.value.trim()
  || companyFilter.value.trim()
  || salaryMinFilter.value
  || salaryMaxFilter.value
  || createdFromFilter.value
  || createdToFilter.value
  || scoreMinFilter.value
  || scoreMaxFilter.value
  || statusFilter.value
  || platformFilter.value
  || sortFilter.value !== "created_at",
));

function message(value: unknown) {
  error.value = value instanceof Error ? value.message : "操作失败";
}

function analysisReady(item: JsonMap): boolean {
  return !analysisInProgress(item) && (item.analysis_summary?.available > 0 || ["available", "succeeded"].includes(String(item.latest_analysis?.status || "")));
}

function analysisInProgress(item: JsonMap): boolean {
  return item.analysis_summary?.active > 0 || ["queued", "running", "retry_wait"].includes(String(item.analysis_status || item.latest_analysis?.status || ""));
}

function analysisSummary(item: JsonMap): string {
  const summary = item.analysis_summary || {};
  if (summary.total > 1) return `已生成 ${summary.available || 0}/${summary.total} 份报告`;
  return item.match_score == null ? "尚无评分" : `${item.match_score} 分`;
}

function scoreText(value: unknown): string {
  return value == null || value === "" ? "未匹配" : `${value} 分`;
}

function platformLabel(item: JsonMap): string {
  if (item.platform === "boss") return "BOSS";
  if (item.platform === "liepin") return "猎聘";
  return "手动录入";
}

function conditionValue(item: JsonMap, key: string): string {
  const value = item.job_conditions?.[key];
  return value && value !== "unknown" ? String(value) : "未披露";
}

function selectedCondition(key: string): string {
  const value = selected.value?.job_conditions?.[key] ?? selectedFields.value[key];
  if (key === "location" && !value && Array.isArray(selectedFields.value.locations)) return selectedFields.value.locations.join("、") || "未披露";
  if (typeof value === "object" && value !== null) {
    if (value.status === "specified" && value.min != null && value.max != null) return `${value.min} - ${value.max}`;
    return "未披露";
  }
  return value && value !== "unknown" ? String(value) : "未披露";
}

async function loadReferences() {
  const [documentResult, preferenceResult] = await Promise.all([
    api<JsonMap>("/api/v1/documents?document_type=resume&limit=100"),
    api<JsonMap>("/api/v1/preferences?limit=100"),
  ]);
  documents.value = documentResult.items || [];
  preferences.value = preferenceResult.items || [];
  if (!resumes.value.some((item) => item.latest_version?.id === selectedResumeVersionId.value)) {
    selectedResumeVersionId.value = resumes.value.length === 1 ? resumes.value[0]?.latest_version?.id || "" : "";
  }
}

function toIso(value: string): string {
  if (!value) return "";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "" : date.toISOString();
}

async function loadPool(reset = false) {
  if (reset) {
    currentCursor.value = "";
    cursorHistory.value = [];
  }
  const query = new URLSearchParams({ limit: String(page.limit) });
  if (searchText.value.trim()) query.set("search", searchText.value.trim());
  if (jobTitleFilter.value.trim()) query.set("job_title", jobTitleFilter.value.trim());
  if (companyFilter.value.trim()) query.set("company_name", companyFilter.value.trim());
  if (salaryMinFilter.value) query.set("salary_min", salaryMinFilter.value);
  if (salaryMaxFilter.value) query.set("salary_max", salaryMaxFilter.value);
  if (scoreMinFilter.value) query.set("match_score_min", scoreMinFilter.value);
  if (scoreMaxFilter.value) query.set("match_score_max", scoreMaxFilter.value);
  if (createdFromFilter.value && toIso(createdFromFilter.value)) query.set("created_from", toIso(createdFromFilter.value));
  if (createdToFilter.value && toIso(createdToFilter.value)) query.set("created_to", toIso(createdToFilter.value));
  if (statusFilter.value) query.set("analysis_status", statusFilter.value);
  if (platformFilter.value) query.set("platform", platformFilter.value);
  if (sortFilter.value !== "created_at") query.set("sort", sortFilter.value);
  if (currentCursor.value) query.set("cursor", currentCursor.value);
  const result = await api<JsonMap>(`/api/v1/job-pool/items?${query.toString()}`);
  items.value = result.items || [];
  page.has_more = Boolean(result.page?.has_more);
  page.next_cursor = result.page?.next_cursor || "";
}

async function refreshAll() {
  loading.value = true;
  error.value = "";
  try {
    await Promise.all([loadReferences(), loadPool()]);
    const poolId = String(route.query.pool_item_id || "");
    if (poolId) await openItem({ id: poolId });
  } catch (value) {
    message(value);
  } finally {
    loading.value = false;
  }
}

async function applyFilters() {
  selectedIds.value = [];
  selected.value = null;
  try {
    await loadPool(true);
  } catch (value) {
    message(value);
  }
}

async function nextPage() {
  if (!page.has_more || !page.next_cursor || busy.value) return;
  cursorHistory.value.push(currentCursor.value);
  currentCursor.value = page.next_cursor;
  try {
    await loadPool();
  } catch (value) {
    message(value);
  }
}

async function previousPage() {
  if (!cursorHistory.value.length || busy.value) return;
  currentCursor.value = cursorHistory.value.pop() || "";
  try {
    await loadPool();
  } catch (value) {
    message(value);
  }
}

function toggleAll() {
  if (allPageSelected.value) {
    selectedIds.value = selectedIds.value.filter((id) => !items.value.some((item) => item.id === id));
  } else {
    selectedIds.value = Array.from(new Set([...selectedIds.value, ...items.value.map((item) => item.id)]));
  }
}

function clearFilters() {
  searchText.value = "";
  jobTitleFilter.value = "";
  companyFilter.value = "";
  salaryMinFilter.value = "";
  salaryMaxFilter.value = "";
  createdFromFilter.value = "";
  createdToFilter.value = "";
  scoreMinFilter.value = "";
  scoreMaxFilter.value = "";
  statusFilter.value = "";
  platformFilter.value = "";
  sortFilter.value = "created_at";
  void applyFilters();
}

function manualJobText(): string {
  const workMode = ({ onsite: "现场办公", hybrid: "混合办公", remote: "远程办公" } as JsonMap)[form.work_mode];
  return [
    form.job_title.trim(),
    form.company_name.trim() ? `公司：${form.company_name.trim()}` : "",
    form.location_text.trim() ? `工作地点：${form.location_text.trim()}` : "",
    workMode || "",
    form.salary_text.trim() ? `薪资：${form.salary_text.trim()}` : "",
    form.job_text.trim(),
  ].filter(Boolean).join("\n");
}

async function createJobVersion() {
  const created = await api<JsonMap>("/api/v1/documents", {
    method: "POST",
    idempotencyKey: idempotencyKey("pool-document"),
    body: { document_type: "job_description", subject_type: "job_description", title: form.job_title, text: manualJobText() },
  });
  const detail = await api<JsonMap>(`/api/v1/documents/${encodeURIComponent(created.id)}`);
  return api<JsonMap>(`/api/v1/documents/${encodeURIComponent(created.id)}/versions`, {
    method: "POST",
    idempotencyKey: idempotencyKey("pool-document-version"),
    body: { draft_id: detail.latest_draft.id, base_revision: detail.revision, content: detail.draft_content },
  });
}

async function savePool() {
  busy.value = true;
  error.value = "";
  success.value = "";
  try {
    const jobVersion = await createJobVersion();
    await api<JsonMap>("/api/v1/job-pool/items", {
      method: "POST",
      idempotencyKey: idempotencyKey("pool"),
      body: { source: { type: "document_version", job_document_version_id: jobVersion.id } },
    });
    success.value = "岗位已保存到匹配池；选择岗位后再批量匹配。";
    await loadPool(true);
    Object.assign(form, { job_title: "", company_name: "", location_text: "", work_mode: "", salary_text: "", job_text: "" });
  } catch (value) {
    message(value);
  } finally {
    busy.value = false;
  }
}

async function openItem(item: JsonMap) {
  error.value = "";
  try {
    selected.value = await api<JsonMap>(`/api/v1/job-pool/items/${encodeURIComponent(item.id)}`);
  } catch (value) {
    message(value);
  }
}

async function submitBatchMatch(ids: string[], resumeVersionId: string) {
  const poolIds = Array.from(new Set(ids));
  if (!poolIds.length) {
    error.value = "请先勾选至少一个岗位";
    return;
  }
  if (!resumeVersionId) {
    error.value = "请先选择本次匹配使用的简历";
    return;
  }
  if (!preferences.value.length) {
    error.value = "还没有有效岗位期望，请先到“简历”页面创建至少一条";
    return;
  }
  busy.value = true;
  error.value = "";
  success.value = "";
  try {
    const result = await api<JsonMap>("/api/v1/job-pool/items/batch-analyze", {
      method: "POST",
      idempotencyKey: idempotencyKey("pool-batch-analysis"),
      body: {
        pool_item_ids: poolIds,
        resume_document_version_id: resumeVersionId,
        confirm_usage: true,
      },
    });
    const blocked = result.blocked || [];
    const baseMessage = `已为 ${result.requested_items} 个岗位合并 ${result.preference_count} 条有效岗位期望，提交 ${result.started_count} 次匹配`;
    const blockedMessage = blocked.length ? `；${blocked.length} 次未启动：${blocked.slice(0, 3).map((item: JsonMap) => item.reason).join("；")}` : "";
    if (result.started_count) success.value = `${baseMessage}${blockedMessage}`;
    else error.value = `${baseMessage}${blockedMessage}`;
    const startedPoolIds = new Set((result.tasks || []).map((item: JsonMap) => item.pool_item_id));
    selectedIds.value = selectedIds.value.filter((id) => !startedPoolIds.has(id));
    await loadPool();
    if (selected.value && poolIds.includes(selected.value.id)) await openItem(selected.value);
  } catch (value) {
    message(value);
  } finally {
    busy.value = false;
  }
}

async function requestBatchMatch(ids: string[] = selectedIds.value) {
  const poolIds = Array.from(new Set(ids));
  if (!poolIds.length) {
    error.value = "请先勾选至少一个岗位";
    return;
  }
  if (!resumes.value.length) {
    error.value = "请先上传简历后才能匹配";
    return;
  }
  if (!preferences.value.length) {
    error.value = "还没有有效岗位期望，请先到“简历”页面创建至少一条";
    return;
  }
  if (resumes.value.length === 1) {
    selectedResumeVersionId.value = resumes.value[0].latest_version.id;
    await submitBatchMatch(poolIds, selectedResumeVersionId.value);
    return;
  }
  matchingPoolIds.value = poolIds;
  resumeDialogSelection.value = resumes.value.some((item) => item.latest_version?.id === selectedResumeVersionId.value)
    ? selectedResumeVersionId.value
    : "";
  resumeDialogOpen.value = true;
  await nextTick();
  if (resumeDialog.value && !resumeDialog.value.open) resumeDialog.value.showModal();
}

function closeResumeDialog() {
  if (resumeDialog.value?.open) resumeDialog.value.close();
  resumeDialogOpen.value = false;
  resumeDialogSelection.value = "";
  matchingPoolIds.value = [];
}

async function confirmResumeMatch() {
  const poolIds = [...matchingPoolIds.value];
  const resumeVersionId = resumeDialogSelection.value;
  if (!resumeVersionId) {
    error.value = "请选择一份简历后再匹配";
    return;
  }
  selectedResumeVersionId.value = resumeVersionId;
  closeResumeDialog();
  await submitBatchMatch(poolIds, resumeVersionId);
}

async function goApply() {
  if (!selected.value?.apply_action?.click_token || !selected.value.source_url) return;
  const destination = window.open("about:blank", "_blank");
  if (!destination) {
    error.value = "浏览器拦截了新标签页，请允许本站打开弹窗后重试";
    return;
  }
  destination.opener = null;
  try {
    const response = await api<Response>(`/api/v1/job-pool/items/${encodeURIComponent(selected.value.id)}/go-to-apply`, {
      method: "POST",
      idempotencyKey: idempotencyKey("apply"),
      body: { click_token: selected.value.apply_action.click_token },
      raw: true,
      redirect: "manual",
    });
    if (response.status !== 303 && response.type !== "opaqueredirect") throw new Error("去投递记录未能保存，请稍后重试");
    destination.location.replace(selected.value.source_url);
    success.value = "已打开原岗位页面；是否投递由你在招聘平台确认";
  } catch (value) {
    destination.close();
    message(value);
  }
}

async function deletePoolItem(item: JsonMap) {
  busy.value = true;
  error.value = "";
  success.value = "";
  try {
    const deleted = await deleteWithImpact(
      `/api/v1/job-pool/items/${encodeURIComponent(item.id)}`,
      (impact) => `删除岗位“${item.job_title || "未命名岗位"}”将同时删除 ${impact.affected?.analyses || 0} 份分析及其改写、面试和岗位版简历，并撤销 ${impact.affected?.apply_entry || 0} 条投递入口记录。确认删除？`,
    );
    if (!deleted) return;
    if (selected.value?.id === item.id) selected.value = null;
    selectedIds.value = selectedIds.value.filter((id) => id !== item.id);
    success.value = "匹配池岗位及其关联结果已删除";
    await loadPool();
  } catch (value) {
    message(value);
  } finally {
    busy.value = false;
  }
}

onMounted(refreshAll);
</script>

<template>
  <AppShell>
    <PageHeader eyebrow="MATCH POOL" title="匹配池" description="先把岗位批量收进池子，再选一份简历；系统会自动使用全部有效岗位期望进行匹配。"><button class="button outline small" type="button" :disabled="loading || busy" @click="refreshAll">刷新</button></PageHeader>
    <AsyncState :loading="loading" :error="error" :success="success" />

    <template v-if="!loading">
      <section class="card pool-workbench">
        <form class="pool-filter-panel" @submit.prevent="applyFilters">
          <div class="pool-filter-grid pool-filter-grid-primary">
            <div class="field-group pool-filter-keyword"><label for="pool-search">快速搜索岗位名称或公司名称</label><input id="pool-search" v-model="searchText" class="field" placeholder="岗位名称或公司名称" /></div>
            <div class="field-group"><label for="pool-status">匹配状态</label><select id="pool-status" v-model="statusFilter" class="select"><option value="">全部状态</option><option value="awaiting_requirements">待匹配</option><option value="queued">排队中</option><option value="running">执行中</option><option value="available">已有结果</option><option value="failed">失败可重试</option></select></div>
            <div class="field-group"><label for="pool-platform">来源平台</label><select id="pool-platform" v-model="platformFilter" class="select"><option value="">全部来源</option><option value="boss">BOSS</option><option value="liepin">猎聘</option><option value="manual">手动录入</option></select></div>
            <div class="field-group"><label for="pool-sort">排序</label><select id="pool-sort" v-model="sortFilter" class="select"><option value="created_at">入池时间（新到旧）</option><option value="match_score">匹配分数（高到低）</option></select></div>
          </div>
          <details class="pool-advanced-filters">
            <summary class="button outline small">更多筛选：薪资、入池时间、匹配分数</summary>
            <div class="pool-filter-grid pool-filter-grid-advanced">
              <div class="field-group"><label for="pool-job-title">岗位名称</label><input id="pool-job-title" v-model="jobTitleFilter" class="field" placeholder="可单独筛选" /></div>
              <div class="field-group"><label for="pool-company">公司名称</label><input id="pool-company" v-model="companyFilter" class="field" placeholder="可单独筛选" /></div>
              <div class="field-group"><label for="pool-salary-min">薪资最低（元/月）</label><input id="pool-salary-min" v-model="salaryMinFilter" class="field" type="number" min="0" step="1000" placeholder="不限" /></div>
              <div class="field-group"><label for="pool-salary-max">薪资最高（元/月）</label><input id="pool-salary-max" v-model="salaryMaxFilter" class="field" type="number" min="0" step="1000" placeholder="不限" /></div>
              <div class="field-group"><label for="pool-created-from">入池时间起</label><input id="pool-created-from" v-model="createdFromFilter" class="field" type="datetime-local" /></div>
              <div class="field-group"><label for="pool-created-to">入池时间止</label><input id="pool-created-to" v-model="createdToFilter" class="field" type="datetime-local" /></div>
              <div class="field-group"><label for="pool-score-min">匹配分数最低</label><input id="pool-score-min" v-model="scoreMinFilter" class="field" type="number" min="0" max="100" step="0.1" placeholder="不限" /></div>
              <div class="field-group"><label for="pool-score-max">匹配分数最高</label><input id="pool-score-max" v-model="scoreMaxFilter" class="field" type="number" min="0" max="100" step="0.1" placeholder="不限" /></div>
            </div>
          </details>
          <div class="pool-toolbar-actions"><button class="button primary" type="submit">搜索</button><button v-if="hasFilters" class="button link-button" type="button" @click="clearFilters">清空筛选</button><span class="micro">搜索不包含原岗位链接；默认按入池时间倒序。</span></div>
        </form>

        <div class="card-head pool-list-head"><div><h3>岗位列表</h3><p>勾选岗位后点击“匹配”；只有多份简历时才会弹窗让你选择，岗位期望默认全部参与。</p></div><div class="item-actions"><span class="tag neutral">本页 {{ items.length }} 条</span><details class="manual-job-details"><summary class="button outline small">手动新增岗位</summary><form id="pool-form" class="manual-job-form" @submit.prevent="savePool"><div class="form-row"><div class="field-group"><label>岗位名称</label><input v-model="form.job_title" class="field" required placeholder="例如：高级前端工程师" /></div><div class="field-group"><label>公司</label><input v-model="form.company_name" class="field" placeholder="未披露可留空" /></div></div><div class="form-row"><div class="field-group"><label>工作地点</label><input v-model="form.location_text" class="field" placeholder="例如：杭州、上海" /></div><div class="field-group"><label>办公方式</label><select v-model="form.work_mode" class="select"><option value="">未披露</option><option value="onsite">现场</option><option value="hybrid">混合</option><option value="remote">远程</option></select></div></div><div class="field-group"><label>薪资原文</label><input v-model="form.salary_text" class="field" placeholder="例如：20–30K/月·14薪；未知可留空" /></div><div class="field-group"><label>岗位 JD</label><textarea v-model="form.job_text" class="textarea" required placeholder="粘贴完整职责和任职要求" /></div><div class="item-actions"><button class="button primary" :disabled="busy" type="submit">保存岗位</button><span class="micro">保存后不会自动消耗匹配次数。</span></div></form></details></div></div>

        <div class="batch-toolbar">
          <div class="batch-selection"><label class="select-all-control"><input type="checkbox" :checked="allPageSelected" :indeterminate="selectedCount > 0 && !allPageSelected" @change="toggleAll" /><span>全选本页</span></label><span class="batch-selected-count">已选 {{ selectedCount }} 个岗位</span></div>
          <div class="batch-submit"><button class="button primary" :disabled="busy || !selectedCount" type="button" @click="requestBatchMatch()">匹配<span v-if="selectedCount">（{{ selectedCount }} 个岗位，共 {{ estimatedAnalysisCount }} 次<span v-if="preferences.length">；{{ preferences.length }} 条期望合并请求</span>）</span></button><small v-if="!resumes.length" class="batch-hint">还没有简历，点击匹配后会提示先上传简历。</small><RouterLink v-if="!resumes.length" class="button outline small" to="/app/seeker/resume">去上传简历</RouterLink><small v-else-if="!preferences.length" class="batch-hint">请先创建岗位期望。</small><small v-else class="batch-hint">每个岗位只发起一次匹配请求，全部岗位期望会合并输入。</small></div>
        </div>

        <div v-if="!items.length" class="empty pool-empty"><div><strong>{{ hasFilters ? "没有符合筛选条件的岗位" : "匹配池还是空的" }}</strong><p>{{ hasFilters ? "换个筛选条件或清空筛选后重试。" : "可以用浏览器插件抓取岗位，或点击右上角手动新增。" }}</p></div></div>
        <div v-else class="pool-table" role="table" aria-label="匹配池岗位列表">
          <div class="pool-table-head" role="row"><span>岗位 / 公司及条件</span><span>薪资</span><span>入池时间</span><span>匹配分数</span><span>匹配状态</span><span>来源平台</span><span>操作</span></div>
          <article v-for="item in items" :key="item.id" class="pool-table-row" :class="{ selected: selectedIds.includes(item.id) }" role="row">
            <div class="pool-job-cell"><input v-model="selectedIds" type="checkbox" :value="item.id" :aria-label="`选择岗位 ${item.job_title}`" /><div class="pool-job-title"><strong>{{ item.job_title }}</strong><span class="pool-job-subline">{{ item.company_name || "未标注公司" }} · {{ conditionValue(item, "location") }} · {{ conditionValue(item, "experience") }} · {{ conditionValue(item, "education") }}</span></div></div>
            <div class="pool-salary-cell"><strong>{{ conditionValue(item, "salary") }}</strong><small>岗位薪资</small></div>
            <div class="pool-date-cell"><strong>{{ formatDateTime(item.captured_at || item.created_at) }}</strong><small>{{ item.source_type === "browser_capture" ? "插件抓取" : "手动入池" }}</small></div>
            <div class="pool-score-cell"><strong>{{ scoreText(item.match_score) }}</strong><small>{{ item.match_score == null ? "尚未完成匹配" : "最高有效分" }}</small></div>
            <div class="pool-status-cell"><span class="tag" :class="statusClass(item.analysis_status)">{{ analysisInProgress(item) ? "匹配执行中" : analysisReady(item) ? "已有结果" : statusLabel(item.analysis_status) }}</span><small>{{ analysisSummary(item) }}</small></div>
            <div class="pool-source-cell"><span class="tag neutral">{{ platformLabel(item) }}</span></div>
            <div class="item-actions pool-row-actions"><button class="button soft small" type="button" @click="openItem(item)">详情</button><RouterLink v-if="analysisReady(item) && item.latest_analysis?.id" class="button outline small" :to="{ path: '/app/seeker/report', query: { analysis_id: item.latest_analysis.id } }">报告</RouterLink><button class="button link-button small" :disabled="busy" type="button" @click="deletePoolItem(item)">删除</button></div>
          </article>
        </div>

        <div class="pool-pagination"><button class="button outline small" type="button" :disabled="!cursorHistory.length || busy" @click="previousPage">上一页</button><span>第 {{ currentPageNumber }} 页 · 本页 {{ items.length }} 条</span><button class="button outline small" type="button" :disabled="!page.has_more || busy" @click="nextPage">下一页</button></div>
      </section>
    </template>

    <section v-if="selected" class="card pool-detail" style="margin-top:18px">
      <div class="card-head"><div><div class="eyebrow">JOB DETAIL</div><h3>{{ selected.job_title }}</h3><p>{{ selected.company_name || "未标注公司" }} · 入池 {{ formatDateTime(selected.captured_at || selected.created_at) }} · 版本 {{ selected.revision }}</p></div><button class="button link-button" type="button" @click="selected = null">关闭</button></div>
      <div class="card-body">
        <div v-if="selected.blocking_reasons?.length" class="callout opportunity">{{ selected.blocking_reasons.join("；") }}</div>
        <div class="job-condition-summary"><div><strong>地点</strong><span>{{ selectedCondition("location") }}</span></div><div><strong>经验</strong><span>{{ selectedCondition("experience") }}</span></div><div><strong>学历</strong><span>{{ selectedCondition("education") }}</span></div><div><strong>薪资</strong><span>{{ selectedCondition("salary") }}</span></div></div>
        <div class="version-strip"><span class="version-chip">岗位版本 · {{ selected.job_document_version?.id }}</span><span class="version-chip">最近使用简历 · {{ selected.resume_version_id || "待选择" }}</span><span class="version-chip">有效岗位期望 · {{ preferences.length }} 条，合并为 1 次请求</span></div>
        <details class="raw-report"><summary>查看岗位结构化高级信息</summary><pre>{{ JSON.stringify(selected.job_content || {}, null, 2) }}</pre></details>
        <section v-if="analysisInProgress(selected)" class="callout opportunity" style="margin-top:18px">匹配任务正在执行；本岗位只会发起一次模型请求，全部岗位期望会合并输入。</section>
        <section v-else-if="!analysisReady(selected)" class="match-panel" style="margin-top:18px"><div class="card-head"><div><h3>待匹配</h3><p>点击下方“匹配”后选择简历；系统会把全部 {{ preferences.length }} 条有效岗位期望合并为一次请求。</p></div><span class="tag opportunity">尚无结果</span></div></section>
        <div class="item-actions" style="margin-top:14px"><RouterLink v-if="analysisReady(selected) && selected.latest_analysis?.id" class="button primary" :to="{ path: '/app/seeker/report', query: { analysis_id: selected.latest_analysis.id } }">打开最近报告</RouterLink><button v-if="selected.apply_action?.available" class="button soft" type="button" @click="goApply">去投递</button><span v-else-if="selected.source_url" class="tag neutral">原岗位链接当前不可用</span><button v-if="!analysisInProgress(selected)" class="button primary" type="button" :disabled="busy" @click="requestBatchMatch([selected.id])">{{ analysisReady(selected) ? "重新匹配" : "匹配" }}</button><button class="button link-button" type="button" :disabled="busy" @click="deletePoolItem(selected)">删除岗位</button></div>
      </div>
    </section>

    <dialog v-if="resumeDialogOpen" ref="resumeDialog" class="pool-resume-dialog" @cancel.prevent="closeResumeDialog" @close="resumeDialogOpen = false">
      <form class="pool-resume-dialog-form" @submit.prevent="confirmResumeMatch">
        <div class="card-head"><div><div class="eyebrow">SELECT RESUME</div><h3>选择匹配简历</h3><p>本次将匹配 {{ matchingPoolIds.length }} 个岗位；每个岗位只请求一次，并合并全部 {{ preferences.length }} 条有效岗位期望。</p></div><button class="button link-button small" type="button" @click="closeResumeDialog">关闭</button></div>
        <div class="resume-choice-list"><label v-for="item in resumes" :key="item.latest_version.id" class="resume-choice" :class="{ selected: resumeDialogSelection === item.latest_version.id }"><input v-model="resumeDialogSelection" type="radio" name="pool-resume-choice" :value="item.latest_version.id" /><span><strong>{{ item.title }}</strong><small>简历版本 v{{ item.latest_version.version_no }}</small></span></label></div>
        <div class="item-actions pool-dialog-actions"><button class="button soft" type="button" @click="closeResumeDialog">取消</button><button class="button primary" type="submit" :disabled="busy || !resumeDialogSelection">确认匹配</button></div>
      </form>
    </dialog>
  </AppShell>
</template>
