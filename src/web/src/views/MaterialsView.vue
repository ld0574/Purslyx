<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";
import { useRouter } from "vue-router";

import AppShell from "@/components/AppShell.vue";
import AsyncState from "@/components/AsyncState.vue";
import PageHeader from "@/components/PageHeader.vue";
import { api, deleteWithImpact, idempotencyKey, waitForTask } from "@/services/api";
import { useAuthStore } from "@/stores/auth";
import type { JsonMap } from "@/types";
import { statusLabel, taskLabel } from "@/utils/format";

type ConditionStatus = "specified" | "unknown" | "unrestricted";
type SalaryStatus = ConditionStatus | "negotiable";
type Strength = "prefer" | "important" | "required";

const auth = useAuthStore();
const router = useRouter();
const seeker = computed(() => auth.role === "seeker");
const loading = ref(true);
const busy = ref(false);
const error = ref("");
const success = ref("");
const documents = ref<JsonMap[]>([]);
const preferences = ref<JsonMap[]>([]);
const pending = ref<JsonMap | null>(null);
const pendingContent = ref<JsonMap>({});
const uploadFile = ref<File | null>(null);
const selectedJobVersionId = ref("");
const selectedCandidateId = ref("");
const selectedPreferenceVersionId = ref("");
const editingPreference = ref<JsonMap | null>(null);
const activeTask = ref<JsonMap | null>(null);

const documentForm = reactive({ document_type: "resume", title: "", text: "" });
const preferenceForm = reactive({
  display_name: "",
  job_title_status: "specified" as ConditionStatus,
  job_title: "",
  job_title_strength: "important" as Strength,
  location_status: "unknown" as ConditionStatus,
  location: "",
  location_strength: "important" as Strength,
  work_mode_status: "unknown" as ConditionStatus,
  work_mode: "hybrid",
  work_mode_strength: "prefer" as Strength,
  salary_status: "unknown" as SalaryStatus,
  min_salary: null as number | null,
  max_salary: null as number | null,
  currency: "CNY",
  period: "monthly",
  tax_basis: "pre_tax",
  salary_months: null as number | null,
  salary_strength: "important" as Strength,
  is_default: false,
  candidate_source_confirmed: false,
});

const resumes = computed(() => documents.value.filter((item) => item.document_type === "resume" && item.latest_version));
const jobs = computed(() => documents.value.filter((item) => item.document_type === "job_description" && item.latest_version));
const draftSections = computed<JsonMap[]>(() => pendingContent.value.sections || []);
const draftJobFields = computed<JsonMap | null>(() => pendingContent.value.job_fields || null);
const conditionStatusOptions = [
  { value: "specified", label: "填写具体条件" },
  { value: "unknown", label: "暂不填写（未知）" },
  { value: "unrestricted", label: "明确不限" },
];
const strengthOptions = [
  { value: "prefer", label: "可协商" },
  { value: "important", label: "重要" },
  { value: "required", label: "必须符合" },
];

function message(value: unknown) {
  error.value = value instanceof Error ? value.message : "操作失败";
}

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value));
}

async function load() {
  loading.value = true;
  error.value = "";
  try {
    const result = await api<JsonMap>("/api/v1/documents");
    documents.value = result.items || [];
    if (seeker.value) {
      preferences.value = (await api<JsonMap>("/api/v1/preferences")).items || [];
    } else {
      if (!resumes.value.some((item) => item.id === selectedCandidateId.value)) {
        selectedCandidateId.value = resumes.value[0]?.id || "";
      }
      preferences.value = selectedCandidateId.value
        ? (await api<JsonMap>(`/api/v1/preferences?subject_document_id=${encodeURIComponent(selectedCandidateId.value)}`)).items || []
        : [];
    }
    if (!preferences.value.some((item) => item.version?.id === selectedPreferenceVersionId.value)) {
      selectedPreferenceVersionId.value = preferences.value[0]?.version?.id || "";
    }
    if (!jobs.value.some((item) => item.latest_version?.id === selectedJobVersionId.value)) {
      selectedJobVersionId.value = jobs.value[0]?.latest_version?.id || "";
    }
  } catch (value) {
    message(value);
  } finally {
    loading.value = false;
  }
}

function onFile(event: Event) {
  uploadFile.value = (event.target as HTMLInputElement).files?.[0] || null;
}

function ensureDraftShape(content: JsonMap, documentType: string): JsonMap {
  const result = clone(content || {});
  result.schema_version ||= "document-content-v1";
  result.sections = Array.isArray(result.sections) ? result.sections : [];
  if (documentType === "job_description") {
    result.job_fields ||= {};
    result.job_fields.locations = Array.isArray(result.job_fields.locations) ? result.job_fields.locations : [];
    result.job_fields.requirements = Array.isArray(result.job_fields.requirements) ? result.job_fields.requirements : [];
    result.job_fields.responsibilities = Array.isArray(result.job_fields.responsibilities) ? result.job_fields.responsibilities : [];
    result.job_fields.salary ||= { status: "unknown", min: null, max: null, currency: null, period: null, tax_basis: null, salary_months: null };
  }
  return result;
}

async function createDocument() {
  busy.value = true;
  error.value = "";
  success.value = "";
  try {
    if (!uploadFile.value && !documentForm.text.trim()) throw new Error("请粘贴资料正文或选择文件");
    const type = documentForm.document_type;
    const subjectType = type === "resume" ? (seeker.value ? "self_resume" : "candidate_resume") : "job_description";
    const options: JsonMap = { method: "POST", idempotencyKey: idempotencyKey("document") };
    if (uploadFile.value) {
      const formData = new FormData();
      formData.append("document_type", type);
      formData.append("subject_type", subjectType);
      formData.append("title", documentForm.title);
      formData.append("file", uploadFile.value);
      options.formData = formData;
    } else {
      options.body = { document_type: type, subject_type: subjectType, title: documentForm.title, text: documentForm.text };
    }
    const created = await api<JsonMap>("/api/v1/documents", options);
    pending.value = await api<JsonMap>(`/api/v1/documents/${encodeURIComponent(created.id)}`);
    pendingContent.value = ensureDraftShape(pending.value.draft_content, type);
    success.value = "解析完成，请逐项检查后确认";
  } catch (value) {
    message(value);
  } finally {
    busy.value = false;
  }
}

function addDraftSection() {
  const position = draftSections.value.length + 1;
  draftSections.value.push({ section_key: `section-${crypto.randomUUID().slice(0, 8)}`, section_type: "custom", title: "新模块", position, segments: [] });
}

function addDraftSegment(section: JsonMap) {
  section.segments ||= [];
  section.segments.push({ segment_key: `segment-${crypto.randomUUID().slice(0, 8)}`, text: "", source: "user_confirmed" });
}

function removeDraftSegment(section: JsonMap, index: string | number) {
  section.segments.splice(Number(index), 1);
}

function updateDraftLocations(event: Event) {
  if (!draftJobFields.value) return;
  draftJobFields.value.locations = (event.target as HTMLInputElement).value.split(/[、,，]/).map((item) => item.trim()).filter(Boolean);
  draftJobFields.value.location_text = draftJobFields.value.locations.join("、") || null;
}

function updateDraftList(field: "requirements" | "responsibilities", event: Event) {
  if (!draftJobFields.value) return;
  draftJobFields.value[field] = (event.target as HTMLTextAreaElement).value.split("\n").map((item) => item.trim()).filter(Boolean);
}

async function confirmDraft() {
  if (!pending.value?.latest_draft) return;
  busy.value = true;
  error.value = "";
  try {
    draftSections.value.forEach((section, index) => { section.position = index + 1; });
    const version = await api<JsonMap>(`/api/v1/documents/${encodeURIComponent(pending.value.id)}/versions`, {
      method: "POST",
      idempotencyKey: idempotencyKey("document-version"),
      body: {
        draft_id: pending.value.latest_draft.id,
        base_revision: pending.value.revision,
        content: clone(pendingContent.value),
        title: pending.value.title,
      },
    });
    success.value = `资料已确认，生成 v${version.version_no}`;
    pending.value = null;
    pendingContent.value = {};
    uploadFile.value = null;
    documentForm.text = "";
    await load();
  } catch (value) {
    message(value);
  } finally {
    busy.value = false;
  }
}

function condition(status: ConditionStatus, strength: Strength, value: JsonMap): JsonMap {
  if (status !== "specified") return { status, strength };
  return { status, strength, ...value };
}

function preferenceContent(): JsonMap {
  if (preferenceForm.job_title_status === "specified" && !preferenceForm.job_title.trim()) throw new Error("请填写期望岗位，或明确选择未知／不限");
  const locations = preferenceForm.location.split(/[、,，]/).map((item) => item.trim()).filter(Boolean);
  if (preferenceForm.location_status === "specified" && !locations.length) throw new Error("请填写至少一个城市，或明确选择未知／不限");
  if (preferenceForm.salary_status === "specified") {
    if (preferenceForm.min_salary === null || preferenceForm.max_salary === null) throw new Error("请填写薪资上下限");
    if (preferenceForm.max_salary < preferenceForm.min_salary) throw new Error("薪资上限不能低于下限");
  }
  const salary = preferenceForm.salary_status === "specified"
    ? {
        status: "specified",
        min: preferenceForm.min_salary,
        max: preferenceForm.max_salary,
        currency: preferenceForm.currency,
        period: preferenceForm.period,
        tax_basis: preferenceForm.tax_basis,
        salary_months: preferenceForm.salary_months || null,
        strength: preferenceForm.salary_strength,
      }
    : { status: preferenceForm.salary_status, strength: preferenceForm.salary_strength };
  return {
    job_title: condition(preferenceForm.job_title_status, preferenceForm.job_title_strength, { value: preferenceForm.job_title.trim() }),
    locations: condition(preferenceForm.location_status, preferenceForm.location_strength, { values: locations }),
    work_mode: condition(preferenceForm.work_mode_status, preferenceForm.work_mode_strength, { value: preferenceForm.work_mode }),
    salary,
  };
}

function resetPreferenceForm() {
  editingPreference.value = null;
  Object.assign(preferenceForm, {
    display_name: "",
    job_title_status: "specified",
    job_title: "",
    job_title_strength: "important",
    location_status: "unknown",
    location: "",
    location_strength: "important",
    work_mode_status: "unknown",
    work_mode: "hybrid",
    work_mode_strength: "prefer",
    salary_status: "unknown",
    min_salary: null,
    max_salary: null,
    currency: "CNY",
    period: "monthly",
    tax_basis: "pre_tax",
    salary_months: null,
    salary_strength: "important",
    is_default: false,
    candidate_source_confirmed: false,
  });
}

async function savePreference() {
  busy.value = true;
  error.value = "";
  success.value = "";
  try {
    const subject = seeker.value ? null : resumes.value.find((item) => item.id === selectedCandidateId.value);
    if (!seeker.value && !subject) throw new Error("请先确认候选人简历");
    if (!seeker.value && !preferenceForm.candidate_source_confirmed) throw new Error("请确认这些条件由候选人明确提供");
    const body = {
      display_name: preferenceForm.display_name.trim(),
      context: seeker.value ? "self" : "candidate",
      ...(subject ? { subject_document_id: subject.id } : {}),
      is_default: preferenceForm.is_default,
      preference: preferenceContent(),
    };
    const result = editingPreference.value
      ? await api<JsonMap>(`/api/v1/preferences/${encodeURIComponent(editingPreference.value.id)}`, {
          method: "PUT",
          idempotencyKey: idempotencyKey("preference-version"),
          body: { ...body, status: "active", base_revision: editingPreference.value.revision },
        })
      : await api<JsonMap>("/api/v1/preferences", { method: "POST", idempotencyKey: idempotencyKey("preference"), body });
    success.value = result.version?.version_no > 1 ? `岗位期望 v${result.version.version_no} 已保存` : "岗位期望已保存";
    resetPreferenceForm();
    await load();
  } catch (value) {
    message(value);
  } finally {
    busy.value = false;
  }
}

function editPreference(item: JsonMap) {
  const content = item.version?.content || {};
  const salary = content.salary || {};
  editingPreference.value = item;
  preferenceForm.display_name = item.display_name || "";
  preferenceForm.job_title_status = content.job_title?.status || "unknown";
  preferenceForm.job_title = content.job_title?.value || "";
  preferenceForm.job_title_strength = content.job_title?.strength || "prefer";
  preferenceForm.location_status = content.locations?.status || "unknown";
  preferenceForm.location = (content.locations?.values || []).join("、");
  preferenceForm.location_strength = content.locations?.strength || "prefer";
  preferenceForm.work_mode_status = content.work_mode?.status || "unknown";
  preferenceForm.work_mode = content.work_mode?.value || "hybrid";
  preferenceForm.work_mode_strength = content.work_mode?.strength || "prefer";
  preferenceForm.salary_status = salary.status || "unknown";
  preferenceForm.min_salary = salary.min === null || salary.min === undefined ? null : Number(salary.min);
  preferenceForm.max_salary = salary.max === null || salary.max === undefined ? null : Number(salary.max);
  preferenceForm.currency = salary.currency || "CNY";
  preferenceForm.period = salary.period || "monthly";
  preferenceForm.tax_basis = salary.tax_basis || "pre_tax";
  preferenceForm.salary_months = salary.salary_months === null || salary.salary_months === undefined ? null : Number(salary.salary_months);
  preferenceForm.salary_strength = salary.strength || "prefer";
  preferenceForm.is_default = Boolean(item.is_default);
  preferenceForm.candidate_source_confirmed = !seeker.value;
}

function valueStatus(value: JsonMap | undefined): string {
  return ({ specified: "已填写", unknown: "未知", unrestricted: "不限", negotiable: "面议" } as JsonMap)[value?.status] || "未知";
}

function preferenceSummary(item: JsonMap): string {
  const content = item.version?.content || {};
  const title = content.job_title?.status === "specified" ? content.job_title.value : valueStatus(content.job_title);
  const locations = content.locations?.status === "specified" ? (content.locations.values || []).join("、") : valueStatus(content.locations);
  const workMode = content.work_mode?.status === "specified"
    ? ({ onsite: "现场", hybrid: "混合", remote: "远程" } as JsonMap)[content.work_mode.value]
    : valueStatus(content.work_mode);
  const salary = content.salary?.status === "specified"
    ? `${content.salary.min}–${content.salary.max} ${content.salary.currency}/${content.salary.period === "yearly" ? "年" : "月"}`
    : valueStatus(content.salary);
  return `${title} · ${locations} · ${workMode} · ${salary}`;
}

async function archivePreference(item: JsonMap) {
  if (!window.confirm(`归档岗位期望“${item.display_name}”？旧报告仍保留冻结快照。`)) return;
  busy.value = true;
  error.value = "";
  try {
    await api(`/api/v1/preferences/${encodeURIComponent(item.id)}`, { method: "DELETE", idempotencyKey: idempotencyKey("preference-delete") });
    if (editingPreference.value?.id === item.id) resetPreferenceForm();
    success.value = "岗位期望已归档";
    await load();
  } catch (value) {
    message(value);
  } finally {
    busy.value = false;
  }
}

async function deleteDocument(item: JsonMap) {
  busy.value = true;
  error.value = "";
  success.value = "";
  try {
    const deleted = await deleteWithImpact(`/api/v1/documents/${encodeURIComponent(item.id)}`, (impact) => {
      const affected = impact.affected || {};
      return `删除资料“${item.title}”将同时撤销 ${affected.versions || 0} 个版本、${affected.analyses || 0} 份报告和 ${affected.job_pool_items || 0} 个匹配池岗位；${affected.running_tasks || 0} 个执行中任务会被取消。确认删除？`;
    });
    if (!deleted) return;
    if (pending.value?.id === item.id) pending.value = null;
    if (selectedCandidateId.value === item.id) selectedCandidateId.value = "";
    success.value = "资料及其受影响结果已删除";
    await load();
  } catch (value) {
    message(value);
  } finally {
    busy.value = false;
  }
}

async function changeCandidate() {
  preferences.value = selectedCandidateId.value
    ? (await api<JsonMap>(`/api/v1/preferences?subject_document_id=${encodeURIComponent(selectedCandidateId.value)}`)).items || []
    : [];
  selectedPreferenceVersionId.value = preferences.value[0]?.version?.id || "";
  resetPreferenceForm();
}

async function createRecruiterAnalysis() {
  busy.value = true;
  error.value = "";
  success.value = "";
  try {
    const candidate = resumes.value.find((item) => item.id === selectedCandidateId.value);
    if (!candidate?.latest_version?.id) throw new Error("请先选择已确认的候选人简历");
    const result = await api<JsonMap>("/api/v1/analyses", {
      method: "POST",
      idempotencyKey: idempotencyKey("analysis"),
      body: {
        context_type: "recruiter_single",
        resume_document_version_id: candidate.latest_version.id,
        job_document_version_id: selectedJobVersionId.value,
        ...(selectedPreferenceVersionId.value ? { preference_version_id: selectedPreferenceVersionId.value } : {}),
        confirm_usage: true,
      },
    });
    activeTask.value = result.task || null;
    if (result.task) {
      success.value = "分析任务已受理，正在生成报告";
      await waitForTask(result.task, { onUpdate: (task) => { activeTask.value = task; } });
    }
    activeTask.value = null;
    await router.push({ path: "/app/recruiter/report", query: { analysis_id: result.analysis.id } });
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
    <PageHeader eyebrow="MATERIALS" :title="seeker ? '简历与岗位期望' : '候选人资料'" :description="seeker ? '导入并确认简历，保存多条互不拼接的岗位期望。' : '分别确认岗位 JD、候选人简历和候选人明确提供的期望。'">
      <button class="button outline small" type="button" @click="load">刷新</button>
    </PageHeader>
    <AsyncState :loading="loading" :error="error" :success="success" />
    <div v-if="activeTask" class="callout opportunity task-inline-state"><span class="spinner" />{{ taskLabel(activeTask.task_type) }}：{{ statusLabel(activeTask.status) }} · {{ activeTask.current_step || "等待执行" }}</div>

    <div v-if="!loading" class="grid-2">
      <section class="card">
        <div class="card-head"><div><h3>{{ seeker ? "导入简历" : "导入候选人资料或 JD" }}</h3><p>支持文本、PDF、DOC、DOCX；解析后逐项人工确认。</p></div></div>
        <div class="card-body"><form id="formal-document-form" class="form-card" @submit.prevent="createDocument"><div v-if="!seeker" class="field-group"><label>资料类型</label><select v-model="documentForm.document_type" class="select" name="document_type"><option value="resume">候选人简历</option><option value="job_description">岗位 JD</option></select></div><div class="field-group"><label>标题</label><input v-model="documentForm.title" class="field" name="title" placeholder="例如：2026 秋招简历" required /></div><div class="field-group"><label>粘贴文本</label><textarea v-model="documentForm.text" class="textarea" name="text" placeholder="粘贴完整简历或岗位 JD；也可以只选择下方文件。" /></div><div class="field-group"><label>或上传文件</label><input class="field" type="file" accept=".pdf,.doc,.docx,.txt" @change="onFile" /></div><button class="button primary" :disabled="busy" type="submit">解析并检查</button></form></div>
      </section>
      <section class="card"><div class="card-head"><div><h3>已确认资料</h3><p>{{ documents.length }} 份，删除前会列出关联影响。</p></div></div><div class="card-body data-list"><div v-if="!documents.length" class="empty"><div><strong>还没有资料</strong><p>从左侧导入第一份内容。</p></div></div><article v-for="item in documents" :key="item.id" class="data-row document-item"><div><strong>{{ item.title }}</strong><small>{{ item.document_type }} · {{ item.status }} · {{ item.latest_version ? `v${item.latest_version.version_no}` : "待确认" }}</small></div><div class="item-actions"><span class="tag" :class="item.latest_version ? 'success' : 'opportunity'">{{ item.latest_version ? "已确认" : "草稿" }}</span><a v-if="item.source_type && item.source_type !== 'text'" class="button soft small" :href="`/api/v1/documents/${item.id}/file`" target="_blank">下载原文件</a><button class="button link-button small" :disabled="busy" type="button" @click="deleteDocument(item)">删除</button></div></article></div></section>
    </div>

    <section v-if="pending" class="card draft-review" style="margin-top:18px">
      <div class="card-head"><div><h3>检查解析草稿</h3><p>按字段修正后再确认；原始文件不会被这些修改覆盖。</p></div><span class="tag opportunity">待确认</span></div>
      <form id="document-draft-review" class="card-body form-card" @submit.prevent="confirmDraft">
        <template v-if="draftJobFields">
          <div class="form-row"><div class="field-group"><label>岗位名称</label><input v-model="draftJobFields.title" class="field" /></div><div class="field-group"><label>公司</label><input v-model="draftJobFields.company_name" class="field" /></div></div>
          <div class="form-row"><div class="field-group"><label>工作城市（可多个）</label><input class="field" :value="(draftJobFields.locations || []).join('、')" placeholder="杭州、上海" @input="updateDraftLocations" /></div><div class="field-group"><label>办公方式</label><select v-model="draftJobFields.work_mode" class="select"><option :value="null">未披露</option><option value="onsite">现场</option><option value="hybrid">混合</option><option value="remote">远程</option></select></div></div>
          <div class="field-group"><label>薪资原文</label><input v-model="draftJobFields.salary_text" class="field" placeholder="保留 JD 原始描述；未披露可留空" /></div>
          <div class="condition-editor"><div class="condition-editor-head"><div><strong>可比较薪资字段</strong><small>只填写 JD 明确披露的口径，不自动补 12 薪、税率或汇率。</small></div><select v-model="draftJobFields.salary.status" class="select compact"><option value="specified">明确区间</option><option value="unknown">未披露／不可读</option><option value="negotiable">面议</option></select></div><div v-if="draftJobFields.salary.status === 'specified'" class="salary-grid"><div class="field-group"><label>下限</label><input v-model="draftJobFields.salary.min" class="field" type="number" min="0" /></div><div class="field-group"><label>上限</label><input v-model="draftJobFields.salary.max" class="field" type="number" min="0" /></div><div class="field-group"><label>币种</label><select v-model="draftJobFields.salary.currency" class="select"><option>CNY</option><option>USD</option><option>EUR</option><option>HKD</option><option>JPY</option></select></div><div class="field-group"><label>周期</label><select v-model="draftJobFields.salary.period" class="select"><option value="monthly">月薪</option><option value="yearly">年薪</option></select></div><div class="field-group"><label>税制</label><select v-model="draftJobFields.salary.tax_basis" class="select"><option :value="null">未披露</option><option value="pre_tax">税前</option><option value="post_tax">税后</option></select></div><div class="field-group"><label>发薪月数</label><input v-model.number="draftJobFields.salary.salary_months" class="field" type="number" min="1" step="0.5" placeholder="未知留空" /></div></div></div>
          <div class="form-row"><div class="field-group"><label>岗位职责（每行一项）</label><textarea class="textarea" :value="(draftJobFields.responsibilities || []).join('\n')" @input="updateDraftList('responsibilities', $event)" /></div><div class="field-group"><label>任职要求（每行一项）</label><textarea class="textarea" :value="(draftJobFields.requirements || []).join('\n')" @input="updateDraftList('requirements', $event)" /></div></div>
        </template>
        <template v-else>
          <section v-for="section in draftSections" :key="section.section_key" class="draft-section-editor"><div class="condition-editor-head"><input v-model="section.title" class="field section-title-input" aria-label="简历模块名称" /><button class="button soft small" type="button" @click="addDraftSegment(section)">添加段落</button></div><div v-for="(segment, index) in section.segments" :key="segment.segment_key" class="segment-editor"><textarea v-model="segment.text" class="textarea" aria-label="简历段落正文" /><button class="button link-button small" type="button" @click="removeDraftSegment(section, index)">删除段落</button></div></section>
          <button class="button soft" type="button" @click="addDraftSection">添加简历模块</button>
        </template>
        <details class="raw-report"><summary>查看结构化草稿（高级信息）</summary><pre>{{ JSON.stringify(pendingContent, null, 2) }}</pre></details>
        <div class="item-actions"><button class="button primary" :disabled="busy" type="submit">确认这个版本</button><button class="button soft" type="button" @click="pending = null">稍后处理</button></div>
      </form>
    </section>

    <section class="card" style="margin-top:18px">
      <div class="card-head"><div><h3>{{ seeker ? "岗位期望" : "候选人明确期望" }}</h3><p>每个字段分别记录具体值、未知或不限，并保存自己的条件强度。</p></div></div>
      <div class="card-body grid-2 preference-layout">
        <form id="preference-form" class="form-card" @submit.prevent="savePreference">
          <div v-if="!seeker" class="field-group"><label>候选人</label><select v-model="selectedCandidateId" class="select" required @change="changeCandidate"><option value="" disabled>请选择候选人</option><option v-for="item in resumes" :key="item.id" :value="item.id">{{ item.title }}</option></select></div>
          <div class="field-group"><label>期望名称</label><input v-model="preferenceForm.display_name" class="field" placeholder="例如：前端方向 · 华东" required /></div>
          <section class="condition-editor"><div class="condition-editor-head"><div><strong>岗位方向</strong><small>一个或多个可接受岗位方向。</small></div><select v-model="preferenceForm.job_title_status" class="select compact"><option v-for="option in conditionStatusOptions" :key="option.value" :value="option.value">{{ option.label }}</option></select></div><div v-if="preferenceForm.job_title_status === 'specified'" class="form-row"><div class="field-group"><label>岗位名称</label><input v-model="preferenceForm.job_title" class="field" placeholder="例如：前端工程师" /></div><div class="field-group"><label>条件强度</label><select v-model="preferenceForm.job_title_strength" class="select"><option v-for="option in strengthOptions" :key="option.value" :value="option.value">{{ option.label }}</option></select></div></div></section>
          <section class="condition-editor"><div class="condition-editor-head"><div><strong>工作地点</strong><small>多个城市用逗号或顿号分隔。</small></div><select v-model="preferenceForm.location_status" class="select compact"><option v-for="option in conditionStatusOptions" :key="option.value" :value="option.value">{{ option.label }}</option></select></div><div v-if="preferenceForm.location_status === 'specified'" class="form-row"><div class="field-group"><label>可接受城市</label><input v-model="preferenceForm.location" class="field" placeholder="杭州、上海" /></div><div class="field-group"><label>条件强度</label><select v-model="preferenceForm.location_strength" class="select"><option v-for="option in strengthOptions" :key="option.value" :value="option.value">{{ option.label }}</option></select></div></div></section>
          <section class="condition-editor"><div class="condition-editor-head"><div><strong>办公方式</strong><small>远程不等于接受任意异地现场。</small></div><select v-model="preferenceForm.work_mode_status" class="select compact"><option v-for="option in conditionStatusOptions" :key="option.value" :value="option.value">{{ option.label }}</option></select></div><div v-if="preferenceForm.work_mode_status === 'specified'" class="form-row"><div class="field-group"><label>可接受方式</label><select v-model="preferenceForm.work_mode" class="select"><option value="onsite">现场</option><option value="hybrid">混合</option><option value="remote">远程</option></select></div><div class="field-group"><label>条件强度</label><select v-model="preferenceForm.work_mode_strength" class="select"><option v-for="option in strengthOptions" :key="option.value" :value="option.value">{{ option.label }}</option></select></div></div></section>
          <section class="condition-editor"><div class="condition-editor-head"><div><strong>薪资范围</strong><small>默认填写口径为人民币、税前月薪；发薪月数未知时留空。</small></div><select v-model="preferenceForm.salary_status" class="select compact"><option value="specified">填写明确区间</option><option value="negotiable">明确面议</option><option value="unknown">暂不填写（未知）</option><option value="unrestricted">明确不限</option></select></div><div v-if="preferenceForm.salary_status === 'specified'" class="salary-grid"><div class="field-group"><label>下限</label><input v-model.number="preferenceForm.min_salary" class="field" type="number" min="0" /></div><div class="field-group"><label>上限</label><input v-model.number="preferenceForm.max_salary" class="field" type="number" min="0" /></div><div class="field-group"><label>币种</label><select v-model="preferenceForm.currency" class="select"><option>CNY</option><option>USD</option><option>EUR</option><option>HKD</option><option>JPY</option></select></div><div class="field-group"><label>周期</label><select v-model="preferenceForm.period" class="select"><option value="monthly">月薪</option><option value="yearly">年薪</option></select></div><div class="field-group"><label>税制</label><select v-model="preferenceForm.tax_basis" class="select"><option value="pre_tax">税前</option><option value="post_tax">税后</option></select></div><div class="field-group"><label>发薪月数</label><input v-model.number="preferenceForm.salary_months" class="field" type="number" min="1" step="0.5" placeholder="未知留空" /></div></div><div class="field-group"><label>薪资条件强度</label><select v-model="preferenceForm.salary_strength" class="select"><option v-for="option in strengthOptions" :key="option.value" :value="option.value">{{ option.label }}</option></select></div></section>
          <label v-if="!seeker" class="source-confirm"><input v-model="preferenceForm.candidate_source_confirmed" type="checkbox" /><span><strong>候选人明确提供</strong><small>我确认以上内容不是从候选人现居地、旧薪资、JD 或招聘者自己的偏好推断。</small></span></label>
          <label class="micro"><input v-model="preferenceForm.is_default" type="checkbox" /> 设为默认期望</label>
          <div class="item-actions"><button class="button primary" :disabled="busy" type="submit">{{ editingPreference ? "保存新版本" : "保存独立期望" }}</button><button v-if="editingPreference" class="button soft" type="button" @click="resetPreferenceForm">取消编辑</button></div>
        </form>
        <div class="data-list preference-list"><div v-if="!preferences.length" class="empty"><div><strong>还没有期望</strong><p>未知和不限不会混为一谈；不同岗位方向分别保存。</p></div></div><article v-for="item in preferences" :key="item.id" class="data-row preference-row"><div><strong>{{ item.display_name }}</strong><small>{{ preferenceSummary(item) }}</small><small>v{{ item.version?.version_no }} · {{ item.is_default ? "默认" : "备用" }} · {{ item.context === "candidate" ? "候选人明确提供" : "本人确认" }}</small></div><div class="item-actions"><button class="button soft small" type="button" data-action="edit-preference" @click="editPreference(item)">编辑</button><button class="button link-button small" type="button" @click="archivePreference(item)">归档</button></div></article></div>
      </div>
    </section>

    <section v-if="!seeker" class="card" style="margin-top:18px">
      <div class="card-head"><div><h3>单人深入分析</h3><p>先选候选人，再只显示该候选人明确提供的岗位期望；缺少时按未知处理。</p></div></div>
      <form id="recruiter-analysis-form" class="card-body form-card" @submit.prevent="createRecruiterAnalysis"><div class="form-row"><div class="field-group"><label>候选人简历</label><select v-model="selectedCandidateId" class="select" name="candidate_id" required @change="changeCandidate"><option v-for="item in resumes" :key="item.id" :value="item.id">{{ item.title }} · v{{ item.latest_version.version_no }}</option></select></div><div class="field-group"><label>岗位 JD</label><select v-model="selectedJobVersionId" class="select" name="job_version_id" required><option v-for="item in jobs" :key="item.id" :value="item.latest_version.id">{{ item.title }} · v{{ item.latest_version.version_no }}</option></select></div></div><div class="field-group"><label>候选人岗位期望（可选）</label><select v-model="selectedPreferenceVersionId" class="select" name="preference_version_id"><option value="">候选人未提供，报告标为未知</option><option v-for="item in preferences" :key="item.id" :value="item.version.id">{{ item.display_name }} · v{{ item.version.version_no }}</option></select></div><button class="button primary" :disabled="busy || !selectedCandidateId || !selectedJobVersionId" type="submit">确认消耗 1 次并生成报告</button></form>
    </section>
  </AppShell>
</template>
