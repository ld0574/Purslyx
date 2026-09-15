<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";
import { useRoute, useRouter } from "vue-router";

import AppShell from "@/components/AppShell.vue";
import AsyncState from "@/components/AsyncState.vue";
import PageHeader from "@/components/PageHeader.vue";
import { api, deleteWithImpact, idempotencyKey, waitForTask } from "@/services/api";
import type { JsonMap } from "@/types";
import { statusClass, statusLabel, taskLabel } from "@/utils/format";

const route = useRoute();
const router = useRouter();
const loading = ref(true);
const busy = ref(false);
const error = ref("");
const success = ref("");
const documents = ref<JsonMap[]>([]);
const preferences = ref<JsonMap[]>([]);
const items = ref<JsonMap[]>([]);
const selected = ref<JsonMap | null>(null);
const browserDraft = ref<JsonMap | null>(null);
const activeTask = ref<JsonMap | null>(null);
const form = reactive({
  job_title: "",
  company_name: "",
  location_text: "",
  work_mode: "",
  salary_text: "",
  job_text: "",
  resume_version_id: "",
  preference_version_id: "",
  start_now: true,
});

const resumes = computed(() => documents.value.filter((item) => item.document_type === "resume" && item.latest_version));
const selectedFields = computed(() => selected.value?.job_content?.job_fields || selected.value?.job_content || {});

function message(value: unknown) {
  error.value = value instanceof Error ? value.message : "操作失败";
}

function analysisReady(item: JsonMap): boolean {
  return ["available", "succeeded"].includes(String(item.latest_analysis?.status || ""));
}

async function refreshData() {
  const [documentResult, preferenceResult, poolResult] = await Promise.all([
    api<JsonMap>("/api/v1/documents"),
    api<JsonMap>("/api/v1/preferences"),
    api<JsonMap>("/api/v1/job-pool/items"),
  ]);
  documents.value = documentResult.items || [];
  preferences.value = preferenceResult.items || [];
  items.value = poolResult.items || [];
  if (!resumes.value.some((item) => item.latest_version?.id === form.resume_version_id)) {
    form.resume_version_id = resumes.value[0]?.latest_version?.id || "";
  }
  if (!preferences.value.some((item) => item.version?.id === form.preference_version_id)) {
    form.preference_version_id = preferences.value.find((item) => item.is_default)?.version?.id || preferences.value[0]?.version?.id || "";
  }
}

async function load() {
  loading.value = true;
  error.value = "";
  try {
    await refreshData();
    const draftId = String(route.query.browser_draft_id || "");
    if (draftId) browserDraft.value = await api<JsonMap>(`/api/v1/browser/job-drafts/${encodeURIComponent(draftId)}/web`);
  } catch (value) {
    message(value);
  } finally {
    loading.value = false;
  }
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

async function settleAnalysis(result: JsonMap, savedMessage: string) {
  selected.value = result.job_pool_item || result;
  const poolId = selected.value?.id;
  if (!result.task) {
    success.value = selected.value?.blocking_reasons?.length ? `${savedMessage}，暂未分析：${selected.value.blocking_reasons.join("；")}` : savedMessage;
    return;
  }
  activeTask.value = result.task;
  success.value = `${savedMessage}，分析任务已受理`;
  await waitForTask(result.task, { onUpdate: (task) => { activeTask.value = task; } });
  activeTask.value = null;
  if (!poolId) throw new Error("分析完成，但没有返回匹配池岗位引用");
  selected.value = await api<JsonMap>(`/api/v1/job-pool/items/${encodeURIComponent(poolId)}`);
  success.value = `${savedMessage}，完整报告已生成`;
}

async function savePool() {
  busy.value = true;
  error.value = "";
  success.value = "";
  try {
    if (form.start_now && (!form.resume_version_id || !form.preference_version_id)) throw new Error("请先确认简历和岗位期望");
    const jobVersion = await createJobVersion();
    const result = await api<JsonMap>("/api/v1/job-pool/items", {
      method: "POST",
      idempotencyKey: idempotencyKey("pool"),
      body: {
        source: { type: "document_version", job_document_version_id: jobVersion.id },
        preference_version_id: form.preference_version_id || null,
        analysis: form.start_now ? { start_now: true, resume_document_version_id: form.resume_version_id, confirm_usage: true } : {},
      },
    });
    await settleAnalysis(result, "岗位已保存到匹配池");
    await refreshData();
    form.job_title = "";
    form.company_name = "";
    form.location_text = "";
    form.work_mode = "";
    form.salary_text = "";
    form.job_text = "";
  } catch (value) {
    message(value);
  } finally {
    busy.value = false;
  }
}

async function confirmBrowserDraft() {
  if (!browserDraft.value) return;
  busy.value = true;
  error.value = "";
  success.value = "";
  try {
    if (!browserDraft.value.job_title?.trim()) throw new Error("请检查并补充岗位名称");
    if (!browserDraft.value.job_description_text?.trim()) throw new Error("岗位正文不能为空");
    if (form.start_now && (!form.resume_version_id || !form.preference_version_id)) throw new Error("请先选择已确认简历和岗位期望");
    const result = await api<JsonMap>("/api/v1/job-pool/items", {
      method: "POST",
      idempotencyKey: idempotencyKey("browser-pool"),
      body: {
        source: {
          type: "browser_draft",
          browser_draft_id: browserDraft.value.id,
          corrections: {
            job_title: browserDraft.value.job_title,
            company_name: browserDraft.value.company_name,
            location_text: browserDraft.value.location_text,
            work_mode: browserDraft.value.work_mode,
            salary_text: browserDraft.value.salary_text,
            job_description_text: browserDraft.value.job_description_text,
          },
        },
        preference_version_id: form.preference_version_id || null,
        analysis: form.start_now ? { start_now: true, resume_document_version_id: form.resume_version_id, confirm_usage: true } : {},
      },
    });
    const draftId = browserDraft.value.id;
    browserDraft.value = null;
    await router.replace({ query: { ...route.query, browser_draft_id: undefined } });
    await settleAnalysis(result, `浏览器岗位 ${draftId} 已确认入池`);
    await refreshData();
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

async function analyzeSelected() {
  if (!selected.value) return;
  busy.value = true;
  error.value = "";
  success.value = "";
  try {
    if (!form.resume_version_id || !form.preference_version_id) throw new Error("请先选择已确认简历和岗位期望");
    const result = await api<JsonMap>(`/api/v1/job-pool/items/${encodeURIComponent(selected.value.id)}/analyze`, {
      method: "POST",
      idempotencyKey: idempotencyKey("pool-analysis"),
      body: {
        resume_document_version_id: form.resume_version_id,
        preference_version_id: form.preference_version_id,
        base_revision: selected.value.revision,
        confirm_usage: true,
      },
    });
    await settleAnalysis(result, "重新分析已提交");
    await refreshData();
  } catch (value) {
    message(value);
  } finally {
    busy.value = false;
  }
}

async function goApply() {
  if (!selected.value?.apply_action?.click_token) return;
  const target = selected.value.source_url;
  const response = await api<Response>(`/api/v1/job-pool/items/${encodeURIComponent(selected.value.id)}/go-to-apply`, {
    method: "POST",
    idempotencyKey: idempotencyKey("apply"),
    body: { click_token: selected.value.apply_action.click_token },
    raw: true,
    redirect: "manual",
  });
  if (response.status === 303 || response.type === "opaqueredirect") window.open(target, "_blank", "noopener,noreferrer");
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
    success.value = "匹配池岗位及其关联结果已删除";
    await refreshData();
  } catch (value) {
    message(value);
  } finally {
    busy.value = false;
  }
}

onMounted(load);
</script>

<template>
  <AppShell>
    <PageHeader eyebrow="MATCH POOL" title="匹配池" description="浏览器草稿和手动 JD 都先检查确认；分析前明确选择简历、完整期望并确认计次。"><button class="button outline small" type="button" @click="load">刷新</button></PageHeader>
    <AsyncState :loading="loading" :error="error" :success="success" />
    <div v-if="activeTask" class="callout opportunity task-inline-state"><span class="spinner" />{{ taskLabel(activeTask.task_type) }}：{{ statusLabel(activeTask.status) }} · {{ activeTask.current_step || "等待执行" }}</div>

    <section v-if="browserDraft" class="card browser-draft-review" style="margin-bottom:18px">
      <div class="card-head"><div><h3>检查浏览器岗位草稿</h3><p>{{ browserDraft.platform }} · 自动获取只生成草稿，修改后才确认入池。</p></div><span class="tag opportunity">待确认</span></div>
      <form class="card-body form-card" @submit.prevent="confirmBrowserDraft">
        <div v-if="browserDraft.missing_field_codes?.length" class="callout opportunity">仍需检查：{{ browserDraft.missing_field_codes.join("、") }}</div>
        <div class="form-row"><div class="field-group"><label>岗位名称</label><input v-model="browserDraft.job_title" class="field" required /></div><div class="field-group"><label>公司</label><input v-model="browserDraft.company_name" class="field" placeholder="未披露可留空" /></div></div>
        <div class="form-row"><div class="field-group"><label>工作地点</label><input v-model="browserDraft.location_text" class="field" placeholder="未披露可留空" /></div><div class="field-group"><label>办公方式</label><select v-model="browserDraft.work_mode" class="select"><option :value="null">未披露</option><option value="onsite">现场</option><option value="hybrid">混合</option><option value="remote">远程</option></select></div></div>
        <div class="field-group"><label>薪资原文</label><input v-model="browserDraft.salary_text" class="field" placeholder="面议或未披露请如实保留" /></div>
        <div class="field-group"><label>岗位正文</label><textarea v-model="browserDraft.job_description_text" class="textarea" required /></div>
        <p class="micro">原岗位链接：<a :href="browserDraft.source_url" target="_blank" rel="noreferrer">{{ browserDraft.source_url }}</a></p>
        <div class="form-row"><div class="field-group"><label>本次简历</label><select v-model="form.resume_version_id" class="select" :required="form.start_now"><option value="" disabled>请选择</option><option v-for="item in resumes" :key="item.id" :value="item.latest_version.id">{{ item.title }} · v{{ item.latest_version.version_no }}</option></select></div><div class="field-group"><label>本次岗位期望</label><select v-model="form.preference_version_id" class="select" :required="form.start_now"><option value="" disabled>请选择完整的一条期望</option><option v-for="item in preferences" :key="item.id" :value="item.version.id">{{ item.display_name }} · v{{ item.version.version_no }}</option></select></div></div>
        <label class="micro"><input v-model="form.start_now" type="checkbox" /> 入池后立即分析（确认消耗 1 次分析）</label>
        <button class="button primary" :disabled="busy" type="submit">确认修正并入池</button>
      </form>
    </section>

    <div v-if="!loading" class="grid-2">
      <section class="card"><div class="card-head"><div><h3>保存手动岗位</h3><p>明确填写采集不到的条件；只保存岗位本身不消耗次数。</p></div></div><form id="pool-form" class="card-body form-card" @submit.prevent="savePool"><div class="form-row"><div class="field-group"><label>岗位名称</label><input v-model="form.job_title" class="field" placeholder="例如：高级前端工程师" required /></div><div class="field-group"><label>公司</label><input v-model="form.company_name" class="field" placeholder="未披露可留空" /></div></div><div class="form-row"><div class="field-group"><label>工作地点</label><input v-model="form.location_text" class="field" placeholder="例如：杭州、上海" /></div><div class="field-group"><label>办公方式</label><select v-model="form.work_mode" class="select"><option value="">未披露</option><option value="onsite">现场</option><option value="hybrid">混合</option><option value="remote">远程</option></select></div></div><div class="field-group"><label>薪资原文</label><input v-model="form.salary_text" class="field" placeholder="例如：20–30K/月·14薪；未知可留空" /></div><div class="field-group"><label>岗位 JD</label><textarea v-model="form.job_text" class="textarea" placeholder="粘贴完整职责和任职要求" required /></div><div class="form-row"><div class="field-group"><label>简历版本</label><select v-model="form.resume_version_id" class="select" :required="form.start_now"><option value="" disabled>请选择</option><option v-for="item in resumes" :key="item.id" :value="item.latest_version.id">{{ item.title }} · v{{ item.latest_version.version_no }}</option></select></div><div class="field-group"><label>岗位期望</label><select v-model="form.preference_version_id" class="select" :required="form.start_now"><option value="" disabled>请选择完整的一条期望</option><option v-for="item in preferences" :key="item.id" :value="item.version.id">{{ item.display_name }} · v{{ item.version.version_no }}</option></select></div></div><label class="micro"><input v-model="form.start_now" type="checkbox" name="start_now" /> 保存后立即分析（确认消耗 1 次）</label><button class="button primary" :disabled="busy" type="submit">{{ form.start_now ? "保存并开始分析" : "仅保存岗位" }}</button></form></section>
      <section class="card"><div class="card-head"><div><h3>我的岗位</h3><p>逐条查看状态、报告历史和原平台入口。</p></div><span class="tag neutral">{{ items.length }} 条</span></div><div class="card-body data-list"><div v-if="!items.length" class="empty"><div><strong>匹配池还是空的</strong><p>先保存一份岗位。</p></div></div><article v-for="item in items" :key="item.id" class="data-row pool-item"><div><strong>{{ item.job_title }}</strong><small>{{ item.company_name || "未标注公司" }} · {{ statusLabel(item.analysis_status) }} · {{ item.latest_analysis?.ability_score ?? "—" }} 分</small></div><div class="item-actions"><span class="tag" :class="statusClass(item.analysis_status)">{{ statusLabel(item.analysis_status) }}</span><button class="button soft small" type="button" @click="openItem(item)">查看岗位</button><RouterLink v-if="analysisReady(item)" class="button outline small" :to="{ path: '/app/seeker/report', query: { analysis_id: item.latest_analysis.id } }">完整报告</RouterLink><button class="button link-button small" :disabled="busy" type="button" @click="deletePoolItem(item)">删除</button></div></article></div></section>
    </div>

    <section v-if="selected" class="card" style="margin-top:18px">
      <div class="card-head"><div><h3>{{ selected.job_title }}</h3><p>{{ selected.company_name || "未标注公司" }} · {{ statusLabel(selected.analysis_status) }} · revision {{ selected.revision }}</p></div><button class="button link-button" type="button" @click="selected = null">关闭</button></div>
      <div class="card-body"><div v-if="selected.blocking_reasons?.length" class="callout opportunity">{{ selected.blocking_reasons.join("；") }}</div><div class="job-condition-summary"><div><strong>地点</strong><span>{{ selectedFields.location_text || (selectedFields.locations || []).join("、") || "未披露" }}</span></div><div><strong>办公方式</strong><span>{{ statusLabel(selectedFields.work_mode || "unknown") }}</span></div><div><strong>薪资</strong><span>{{ selectedFields.salary_text || "未披露" }}</span></div></div><div class="version-strip"><span class="version-chip">岗位版本 · {{ selected.job_document_version?.id }}</span><span class="version-chip">简历版本 · {{ selected.resume_version_id || "待选择" }}</span></div><details class="raw-report"><summary>查看岗位结构化高级信息</summary><pre>{{ JSON.stringify(selected.job_content || {}, null, 2) }}</pre></details><div class="item-actions" style="margin-top:14px"><RouterLink v-if="analysisReady(selected)" class="button primary" :to="{ path: '/app/seeker/report', query: { analysis_id: selected.latest_analysis.id } }">打开完整报告</RouterLink><button v-if="selected.source_url" class="button soft" type="button" @click="goApply">去投递</button><button class="button outline" type="button" :disabled="busy" @click="analyzeSelected">用当前选择重新分析</button><button class="button link-button" type="button" :disabled="busy" @click="deletePoolItem(selected)">删除岗位</button></div></div>
    </section>
  </AppShell>
</template>
