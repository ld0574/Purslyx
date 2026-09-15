<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";
import { useRouter } from "vue-router";

import AppShell from "@/components/AppShell.vue";
import AsyncState from "@/components/AsyncState.vue";
import PageHeader from "@/components/PageHeader.vue";
import { api, deleteWithImpact, idempotencyKey } from "@/services/api";
import { useAuthStore } from "@/stores/auth";
import type { JsonMap } from "@/types";

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
const pendingJson = ref("");
const uploadFile = ref<File | null>(null);
const selectedJobVersionId = ref("");
const selectedCandidateId = ref("");
const selectedPreferenceVersionId = ref("");
const editingPreference = ref<JsonMap | null>(null);
const documentForm = reactive({ document_type: "resume", title: "我的简历", text: "负责 Vue 与 TypeScript 项目开发，完成组件设计、接口联调和上线验证。" });
const preferenceForm = reactive({ job_title: "前端工程师", location: "杭州", work_mode: "hybrid", min_salary: 20, max_salary: 30, strength: "important", is_default: true });
const resumes = computed(() => documents.value.filter((item) => item.document_type === "resume" && item.latest_version));
const jobs = computed(() => documents.value.filter((item) => item.document_type === "job_description" && item.latest_version));

function message(value: unknown) { error.value = value instanceof Error ? value.message : "操作失败"; }
async function load() {
  loading.value = true; error.value = "";
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
    selectedJobVersionId.value ||= jobs.value[0]?.latest_version?.id || "";
  } catch (value) { message(value); }
  finally { loading.value = false; }
}

function onFile(event: Event) { uploadFile.value = (event.target as HTMLInputElement).files?.[0] || null; }
async function createDocument() {
  busy.value = true; error.value = ""; success.value = "";
  try {
    const type = documentForm.document_type;
    const subjectType = type === "resume" ? (seeker.value ? "self_resume" : "candidate_resume") : "job_description";
    const options: JsonMap = { method: "POST", idempotencyKey: idempotencyKey("document") };
    if (uploadFile.value) {
      const formData = new FormData();
      formData.append("document_type", type); formData.append("subject_type", subjectType); formData.append("title", documentForm.title); formData.append("file", uploadFile.value);
      options.formData = formData;
    } else options.body = { document_type: type, subject_type: subjectType, title: documentForm.title, text: documentForm.text };
    const created = await api<JsonMap>("/api/v1/documents", options);
    pending.value = await api<JsonMap>(`/api/v1/documents/${encodeURIComponent(created.id)}`);
    pendingJson.value = JSON.stringify(pending.value.draft_content, null, 2);
  } catch (value) { message(value); }
  finally { busy.value = false; }
}

async function confirmDraft() {
  if (!pending.value?.latest_draft) return;
  busy.value = true; error.value = "";
  try {
    let content: JsonMap;
    try { content = JSON.parse(pendingJson.value); }
    catch { throw new Error("草稿 JSON 格式不正确，请检查括号和引号"); }
    const version = await api<JsonMap>(`/api/v1/documents/${encodeURIComponent(pending.value.id)}/versions`, {
      method: "POST", idempotencyKey: idempotencyKey("document-version"), body: {
        draft_id: pending.value.latest_draft.id, base_revision: pending.value.revision,
        content, title: pending.value.title,
      },
    });
    success.value = `资料已确认，生成 v${version.version_no}`;
    pending.value = null; uploadFile.value = null; await load();
  } catch (value) { message(value); }
  finally { busy.value = false; }
}

function preferenceContent() {
  const strength = preferenceForm.strength;
  return {
    job_title: { status: "specified", value: preferenceForm.job_title, strength },
    locations: { status: "specified", values: preferenceForm.location.split(/[、,，]/).map((item) => item.trim()).filter(Boolean), strength },
    work_mode: { status: "specified", value: preferenceForm.work_mode, strength },
    salary: { status: "specified", min: preferenceForm.min_salary * 1000, max: preferenceForm.max_salary * 1000, currency: "CNY", period: "monthly", tax_basis: "pre_tax", salary_months: 12, strength },
  };
}
async function savePreference() {
  busy.value = true; error.value = "";
  try {
    const subject = seeker.value ? null : resumes.value.find((item) => item.id === selectedCandidateId.value);
    if (!seeker.value && !subject) throw new Error("请先确认候选人简历");
    const body = {
      display_name: `${preferenceForm.job_title} · ${preferenceForm.location}`, context: seeker.value ? "self" : "candidate",
      ...(subject ? { subject_document_id: subject.id } : {}), is_default: preferenceForm.is_default, preference: preferenceContent(),
    };
    const result = editingPreference.value
      ? await api<JsonMap>(`/api/v1/preferences/${encodeURIComponent(editingPreference.value.id)}`, { method: "PUT", idempotencyKey: idempotencyKey("preference-version"), body: { ...body, status: "active", base_revision: editingPreference.value.revision } })
      : await api<JsonMap>("/api/v1/preferences", { method: "POST", idempotencyKey: idempotencyKey("preference"), body });
    editingPreference.value = null;
    success.value = result.version?.version_no > 1 ? `岗位期望 v${result.version.version_no} 已保存` : "岗位期望已保存";
    await load();
  } catch (value) { message(value); }
  finally { busy.value = false; }
}

function editPreference(item: JsonMap) {
  const content = item.version?.content || {};
  editingPreference.value = item;
  preferenceForm.job_title = content.job_title?.value || "";
  preferenceForm.location = (content.locations?.values || []).join("、");
  preferenceForm.work_mode = content.work_mode?.value || "hybrid";
  preferenceForm.min_salary = Number(content.salary?.min || 0) / 1000;
  preferenceForm.max_salary = Number(content.salary?.max || 0) / 1000;
  preferenceForm.strength = content.job_title?.strength || "important";
  preferenceForm.is_default = Boolean(item.is_default);
}

async function archivePreference(item: JsonMap) {
  if (!window.confirm(`归档岗位期望“${item.display_name}”？旧报告仍保留冻结快照。`)) return;
  busy.value = true; error.value = "";
  try {
    await api(`/api/v1/preferences/${encodeURIComponent(item.id)}`, { method: "DELETE", idempotencyKey: idempotencyKey("preference-delete") });
    if (editingPreference.value?.id === item.id) editingPreference.value = null;
    success.value = "岗位期望已归档";
    await load();
  } catch (value) { message(value); }
  finally { busy.value = false; }
}

async function deleteDocument(item: JsonMap) {
  busy.value = true; error.value = ""; success.value = "";
  try {
    const deleted = await deleteWithImpact(
      `/api/v1/documents/${encodeURIComponent(item.id)}`,
      (impact) => {
        const affected = impact.affected || {};
        return `删除资料“${item.title}”将同时撤销 ${affected.versions || 0} 个版本、${affected.analyses || 0} 份报告和 ${affected.job_pool_items || 0} 个匹配池岗位；${affected.running_tasks || 0} 个执行中任务会被取消。确认删除？`;
      },
    );
    if (!deleted) return;
    if (pending.value?.id === item.id) pending.value = null;
    if (selectedCandidateId.value === item.id) selectedCandidateId.value = "";
    success.value = "资料及其受影响结果已删除";
    await load();
  } catch (value) { message(value); }
  finally { busy.value = false; }
}

async function changeCandidate() {
  preferences.value = selectedCandidateId.value
    ? (await api<JsonMap>(`/api/v1/preferences?subject_document_id=${encodeURIComponent(selectedCandidateId.value)}`)).items || []
    : [];
  selectedPreferenceVersionId.value = preferences.value[0]?.version?.id || "";
}

async function createRecruiterAnalysis() {
  busy.value = true; error.value = "";
  try {
    const candidate = resumes.value.find((item) => item.id === selectedCandidateId.value);
    if (!candidate?.latest_version?.id) throw new Error("请先选择已确认的候选人简历");
    const result = await api<JsonMap>("/api/v1/analyses", { method: "POST", idempotencyKey: idempotencyKey("analysis"), body: {
      context_type: "recruiter_single", resume_document_version_id: candidate.latest_version.id,
      job_document_version_id: selectedJobVersionId.value,
      ...(selectedPreferenceVersionId.value ? { preference_version_id: selectedPreferenceVersionId.value } : {}), confirm_usage: true,
    } });
    await router.push({ path: "/app/recruiter/report", query: { analysis_id: result.analysis.id } });
  } catch (value) { message(value); }
  finally { busy.value = false; }
}
onMounted(load);
</script>

<template><AppShell><PageHeader eyebrow="MATERIALS" :title="seeker ? '简历与岗位期望' : '候选人资料'" :description="seeker ? '导入并确认简历，保存多条互不拼接的岗位期望。' : '分别确认岗位 JD、候选人简历和候选人明确提供的期望。'"><button class="button outline small" @click="load">刷新</button></PageHeader><AsyncState :loading="loading" :error="error" :success="success" />
  <div v-if="!loading" class="grid-2"><section class="card"><div class="card-head"><div><h3>{{ seeker ? '导入简历' : '导入候选人资料或 JD' }}</h3><p>支持文本、PDF、DOC、DOCX；解析后必须人工确认。</p></div></div><div class="card-body"><form id="formal-document-form" class="form-card" @submit.prevent="createDocument"><div v-if="!seeker" class="field-group"><label>资料类型</label><select v-model="documentForm.document_type" class="select" name="document_type"><option value="resume">候选人简历</option><option value="job_description">岗位 JD</option></select></div><div class="field-group"><label>标题</label><input v-model="documentForm.title" class="field" name="title" required /></div><div class="field-group"><label>粘贴文本</label><textarea v-model="documentForm.text" class="textarea" name="text" /></div><div class="field-group"><label>或上传文件</label><input class="field" type="file" accept=".pdf,.doc,.docx,.txt" @change="onFile" /></div><button class="button primary" :disabled="busy" type="submit">解析并检查</button></form></div></section>
    <section class="card"><div class="card-head"><div><h3>已确认资料</h3><p>{{ documents.length }} 份，删除前会列出关联影响。</p></div></div><div class="card-body data-list"><div v-if="!documents.length" class="empty"><div><strong>还没有资料</strong><p>从左侧导入第一份内容。</p></div></div><article v-for="item in documents" :key="item.id" class="data-row document-item"><div><strong>{{ item.title }}</strong><small>{{ item.document_type }} · {{ item.status }} · {{ item.latest_version ? `v${item.latest_version.version_no}` : '待确认' }}</small></div><div class="item-actions"><span class="tag" :class="item.latest_version ? 'success' : 'opportunity'">{{ item.latest_version ? '已确认' : '草稿' }}</span><a v-if="item.source_type && item.source_type !== 'text'" class="button soft small" :href="`/api/v1/documents/${item.id}/file`" target="_blank">下载原文件</a><button class="button link-button small" :disabled="busy" type="button" @click="deleteDocument(item)">删除</button></div></article></div></section>
  </div>
  <section v-if="pending" class="card draft-review" style="margin-top:18px"><div class="card-head"><div><h3>检查解析草稿</h3><p>可以直接编辑结构化 JSON；确认后生成不可变版本。</p></div></div><form id="document-draft-review" class="card-body form-card" @submit.prevent="confirmDraft"><textarea v-model="pendingJson" class="textarea json" aria-label="结构化资料草稿" /><div class="item-actions"><button class="button primary" :disabled="busy" type="submit">确认这个版本</button><button class="button soft" type="button" @click="pending = null">稍后处理</button></div></form></section>
  <section class="card" style="margin-top:18px"><div class="card-head"><div><h3>{{ seeker ? '岗位期望' : '候选人明确期望' }}</h3><p>岗位方向、地点、办公方式和薪资保存在同一条版本中。</p></div></div><div class="card-body grid-2"><form id="preference-form" class="form-card" @submit.prevent="savePreference"><div v-if="!seeker" class="field-group"><label>候选人</label><select v-model="selectedCandidateId" class="select" @change="changeCandidate"><option v-for="item in resumes" :key="item.id" :value="item.id">{{ item.title }}</option></select></div><div class="form-row"><div class="field-group"><label>岗位方向</label><input v-model="preferenceForm.job_title" class="field" name="job_title" required /></div><div class="field-group"><label>城市（可多个）</label><input v-model="preferenceForm.location" class="field" name="location" required /></div></div><div class="form-row"><div class="field-group"><label>办公方式</label><select v-model="preferenceForm.work_mode" class="select"><option value="onsite">现场</option><option value="hybrid">混合</option><option value="remote">远程</option></select></div><div class="field-group"><label>条件强度</label><select v-model="preferenceForm.strength" class="select"><option value="prefer">倾向</option><option value="important">重要</option><option value="required">必须</option></select></div></div><div class="form-row"><div class="field-group"><label>最低月薪（K）</label><input v-model.number="preferenceForm.min_salary" class="field" type="number" min="0" /></div><div class="field-group"><label>最高月薪（K）</label><input v-model.number="preferenceForm.max_salary" class="field" type="number" min="0" /></div></div><label class="micro"><input v-model="preferenceForm.is_default" type="checkbox" /> 设为默认期望</label><div class="item-actions"><button class="button primary" :disabled="busy" type="submit">{{ editingPreference ? '保存新版本' : '保存独立期望' }}</button><button v-if="editingPreference" class="button soft" type="button" @click="editingPreference = null">取消编辑</button></div></form><div class="data-list"><div v-if="!preferences.length" class="empty"><div><strong>还没有期望</strong><p>未知和不限不会混为一谈。</p></div></div><article v-for="item in preferences" :key="item.id" class="data-row"><div><strong>{{ item.display_name }}</strong><small>v{{ item.version?.version_no }} · {{ item.is_default ? '默认' : '备用' }}</small></div><div class="item-actions"><button class="button soft small" type="button" data-action="edit-preference" @click="editPreference(item)">编辑</button><button class="button link-button small" type="button" @click="archivePreference(item)">归档</button></div></article></div></div></section>
  <section v-if="!seeker" class="card" style="margin-top:18px"><div class="card-head"><div><h3>单人深入分析</h3><p>先选候选人，再只显示该候选人明确提供的岗位期望。</p></div></div><form id="recruiter-analysis-form" class="card-body form-card" @submit.prevent="createRecruiterAnalysis"><div class="form-row"><div class="field-group"><label>候选人简历</label><select v-model="selectedCandidateId" class="select" name="candidate_id" @change="changeCandidate"><option v-for="item in resumes" :key="item.id" :value="item.id">{{ item.title }} · v{{ item.latest_version.version_no }}</option></select></div><div class="field-group"><label>岗位 JD</label><select v-model="selectedJobVersionId" class="select" name="job_version_id"><option v-for="item in jobs" :key="item.id" :value="item.latest_version.id">{{ item.title }} · v{{ item.latest_version.version_no }}</option></select></div></div><div class="field-group"><label>候选人岗位期望（可选）</label><select v-model="selectedPreferenceVersionId" class="select" name="preference_version_id"><option value="">不使用岗位期望</option><option v-for="item in preferences" :key="item.id" :value="item.version.id">{{ item.display_name }} · v{{ item.version.version_no }}</option></select></div><button class="button primary" :disabled="busy || !selectedCandidateId || !selectedJobVersionId" type="submit">确认消耗 1 次并生成报告</button></form></section>
</AppShell></template>
