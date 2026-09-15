<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";
import { useRoute } from "vue-router";

import AppShell from "@/components/AppShell.vue";
import AsyncState from "@/components/AsyncState.vue";
import PageHeader from "@/components/PageHeader.vue";
import { api, deleteWithImpact, idempotencyKey } from "@/services/api";
import type { JsonMap } from "@/types";

const route = useRoute();
const loading = ref(true); const busy = ref(false); const error = ref(""); const success = ref("");
const documents = ref<JsonMap[]>([]); const preferences = ref<JsonMap[]>([]); const items = ref<JsonMap[]>([]);
const selected = ref<JsonMap | null>(null); const browserDraft = ref<JsonMap | null>(null);
const form = reactive({ job_title: "前端开发工程师", company_name: "示例科技", job_text: "负责 Web 前端功能交付，要求熟悉 Vue、TypeScript、性能优化与自动化测试。", resume_version_id: "", preference_version_id: "", start_now: true });
const resumes = computed(() => documents.value.filter((item) => item.document_type === "resume" && item.latest_version));

function message(value: unknown) { error.value = value instanceof Error ? value.message : "操作失败"; }
async function load() {
  loading.value = true; error.value = "";
  try {
    const [documentResult, preferenceResult, poolResult] = await Promise.all([api<JsonMap>("/api/v1/documents"), api<JsonMap>("/api/v1/preferences"), api<JsonMap>("/api/v1/job-pool/items")]);
    documents.value = documentResult.items || []; preferences.value = preferenceResult.items || []; items.value = poolResult.items || [];
    form.resume_version_id ||= resumes.value[0]?.latest_version?.id || "";
    form.preference_version_id ||= preferences.value[0]?.version?.id || "";
    const draftId = String(route.query.browser_draft_id || "");
    if (draftId) browserDraft.value = await api<JsonMap>(`/api/v1/browser/job-drafts/${encodeURIComponent(draftId)}/web`);
  } catch (value) { message(value); }
  finally { loading.value = false; }
}
async function createJobVersion() {
  const created = await api<JsonMap>("/api/v1/documents", { method: "POST", idempotencyKey: idempotencyKey("pool-document"), body: { document_type: "job_description", subject_type: "job_description", title: form.job_title, text: form.job_text } });
  const detail = await api<JsonMap>(`/api/v1/documents/${encodeURIComponent(created.id)}`);
  const version = await api<JsonMap>(`/api/v1/documents/${encodeURIComponent(created.id)}/versions`, { method: "POST", idempotencyKey: idempotencyKey("pool-document-version"), body: { draft_id: detail.latest_draft.id, base_revision: detail.revision, content: detail.draft_content } });
  return version;
}
async function savePool() {
  busy.value = true; error.value = "";
  try {
    if (form.start_now && (!form.resume_version_id || !form.preference_version_id)) throw new Error("请先确认简历和岗位期望");
    const jobVersion = await createJobVersion();
    const result = await api<JsonMap>("/api/v1/job-pool/items", { method: "POST", idempotencyKey: idempotencyKey("pool"), body: {
      source: { type: "document_version", job_document_version_id: jobVersion.id }, preference_version_id: form.preference_version_id || null,
      analysis: form.start_now ? { start_now: true, resume_document_version_id: form.resume_version_id, confirm_usage: true } : {},
    } });
    selected.value = result.job_pool_item || result; success.value = result.analysis ? "岗位已入池，报告已生成" : "岗位已保存，可稍后分析"; await load();
  } catch (value) { message(value); }
  finally { busy.value = false; }
}
async function confirmBrowserDraft() {
  if (!browserDraft.value) return;
  busy.value = true; error.value = "";
  try {
    const result = await api<JsonMap>("/api/v1/job-pool/items", { method: "POST", idempotencyKey: idempotencyKey("browser-pool"), body: {
      source: { type: "browser_draft", browser_draft_id: browserDraft.value.id }, preference_version_id: form.preference_version_id || null,
      analysis: form.start_now ? { start_now: true, resume_document_version_id: form.resume_version_id, confirm_usage: true } : {},
    } });
    selected.value = result.job_pool_item || result; browserDraft.value = null; success.value = "浏览器岗位已确认入池"; await load();
  } catch (value) { message(value); }
  finally { busy.value = false; }
}
async function openItem(item: JsonMap) { try { selected.value = await api<JsonMap>(`/api/v1/job-pool/items/${encodeURIComponent(item.id)}`); } catch (value) { message(value); } }
async function analyzeSelected() {
  if (!selected.value) return;
  busy.value = true;
  try {
    const result = await api<JsonMap>(`/api/v1/job-pool/items/${encodeURIComponent(selected.value.id)}/analyze`, { method: "POST", idempotencyKey: idempotencyKey("pool-analysis"), body: { resume_document_version_id: form.resume_version_id, preference_version_id: form.preference_version_id, base_revision: selected.value.revision, confirm_usage: true } });
    selected.value = result.job_pool_item; success.value = "重新分析已完成"; await load();
  } catch (value) { message(value); }
  finally { busy.value = false; }
}
async function goApply() {
  if (!selected.value?.apply_action?.click_token) return;
  const target = selected.value.source_url;
  const response = await api<Response>(`/api/v1/job-pool/items/${encodeURIComponent(selected.value.id)}/go-to-apply`, { method: "POST", idempotencyKey: idempotencyKey("apply"), body: { click_token: selected.value.apply_action.click_token }, raw: true, redirect: "manual" });
  if (response.status === 303 || response.type === "opaqueredirect") window.open(target, "_blank", "noopener,noreferrer");
}
async function deletePoolItem(item: JsonMap) {
  busy.value = true; error.value = ""; success.value = "";
  try {
    const deleted = await deleteWithImpact(
      `/api/v1/job-pool/items/${encodeURIComponent(item.id)}`,
      (impact) => `删除岗位“${item.job_title || "未命名岗位"}”将同时删除 ${impact.affected?.analyses || 0} 份分析及其改写、面试和岗位版简历，并撤销 ${impact.affected?.apply_entry || 0} 条投递入口记录。确认删除？`,
    );
    if (!deleted) return;
    if (selected.value?.id === item.id) selected.value = null;
    success.value = "匹配池岗位及其关联结果已删除";
    await load();
  } catch (value) { message(value); }
  finally { busy.value = false; }
}
onMounted(load);
</script>

<template><AppShell><PageHeader eyebrow="MATCH POOL" title="匹配池" description="浏览器草稿和手动 JD 都先确认入池；分析前明确选择简历、期望并确认计次。"><button class="button outline small" @click="load">刷新</button></PageHeader><AsyncState :loading="loading" :error="error" :success="success" />
  <section v-if="browserDraft" class="card" style="margin-bottom:18px"><div class="card-head"><div><h3>待确认的浏览器岗位</h3><p>{{ browserDraft.platform }} · 自动获取只生成草稿，不入池也不计次。</p></div><span class="tag opportunity">待确认</span></div><div class="card-body"><h3>{{ browserDraft.job_title }}</h3><p class="micro">{{ browserDraft.company_name }} · {{ browserDraft.location_text }} · {{ browserDraft.salary_text }}</p><details class="raw-report"><summary>查看采集正文</summary><pre>{{ browserDraft.job_description_text }}</pre></details><button class="button primary" style="margin-top:14px" @click="confirmBrowserDraft">确认并入池</button></div></section>
  <div v-if="!loading" class="grid-2"><section class="card"><div class="card-head"><div><h3>保存一份手动岗位</h3><p>确认后立即分析，或只保存为待分析。</p></div></div><form id="pool-form" class="card-body form-card" @submit.prevent="savePool"><div class="form-row"><div class="field-group"><label>岗位名称</label><input v-model="form.job_title" class="field" required /></div><div class="field-group"><label>公司</label><input v-model="form.company_name" class="field" /></div></div><div class="field-group"><label>岗位 JD</label><textarea v-model="form.job_text" class="textarea" required /></div><div class="form-row"><div class="field-group"><label>简历版本</label><select v-model="form.resume_version_id" class="select"><option v-for="item in resumes" :key="item.id" :value="item.latest_version.id">{{ item.title }} · v{{ item.latest_version.version_no }}</option></select></div><div class="field-group"><label>岗位期望</label><select v-model="form.preference_version_id" class="select"><option v-for="item in preferences" :key="item.id" :value="item.version.id">{{ item.display_name }} · v{{ item.version.version_no }}</option></select></div></div><label class="micro"><input v-model="form.start_now" type="checkbox" name="start_now" /> 保存后立即分析（消耗 1 次）</label><button class="button primary" :disabled="busy" type="submit">保存岗位</button></form></section>
    <section class="card"><div class="card-head"><div><h3>我的岗位</h3><p>逐条查看状态、报告历史和原平台入口。</p></div><span class="tag neutral">{{ items.length }} 条</span></div><div class="card-body data-list"><div v-if="!items.length" class="empty"><div><strong>匹配池还是空的</strong><p>先保存一份岗位。</p></div></div><article v-for="item in items" :key="item.id" class="data-row pool-item"><div><strong>{{ item.job_title }}</strong><small>{{ item.company_name || '未标注公司' }} · {{ item.analysis_status }} · {{ item.latest_analysis?.ability_score ?? '—' }} 分</small></div><div class="item-actions"><button class="button soft small" type="button" @click="openItem(item)">查看岗位</button><RouterLink v-if="item.latest_analysis" class="button outline small" :to="{ path: '/app/seeker/report', query: { analysis_id: item.latest_analysis.id } }">完整报告</RouterLink><button class="button link-button small" :disabled="busy" type="button" @click="deletePoolItem(item)">删除</button></div></article></div></section></div>
  <section v-if="selected" class="card" style="margin-top:18px"><div class="card-head"><div><h3>{{ selected.job_title }}</h3><p>{{ selected.analysis_status }} · revision {{ selected.revision }}</p></div><button class="button link-button" @click="selected = null">关闭</button></div><div class="card-body"><div v-if="selected.blocking_reasons?.length" class="callout opportunity">{{ selected.blocking_reasons.join('；') }}</div><div class="version-strip"><span class="version-chip">岗位版本 · {{ selected.job_document_version?.id }}</span><span class="version-chip">简历版本 · {{ selected.resume_version_id || '待选择' }}</span></div><details class="raw-report"><summary>查看岗位结构化内容</summary><pre>{{ JSON.stringify(selected.job_content || {}, null, 2) }}</pre></details><div class="item-actions" style="margin-top:14px"><RouterLink v-if="selected.latest_analysis" class="button primary" :to="{ path: '/app/seeker/report', query: { analysis_id: selected.latest_analysis.id } }">打开完整报告</RouterLink><button v-if="selected.source_url" class="button soft" type="button" @click="goApply">去投递</button><button class="button outline" type="button" :disabled="busy" @click="analyzeSelected">用当前选择重新分析</button><button class="button link-button" type="button" :disabled="busy" @click="deletePoolItem(selected)">删除岗位</button></div></div></section>
</AppShell></template>
