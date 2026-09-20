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
const activeTask = ref<JsonMap | null>(null);
const form = reactive({
  job_title: "",
  company_name: "",
  location_text: "",
  work_mode: "",
  salary_text: "",
  job_text: "",
});
const matchForm = reactive({
  resume_version_id: "",
  preference_version_id: "",
});

const resumes = computed(() => documents.value.filter((item) => item.document_type === "resume" && item.latest_version));
const selectedFields = computed(() => selected.value?.job_content?.job_fields || selected.value?.job_content || {});

function message(value: unknown) {
  error.value = value instanceof Error ? value.message : "操作失败";
}

function analysisReady(item: JsonMap): boolean {
  return ["available", "succeeded"].includes(String(item.latest_analysis?.status || ""));
}

function analysisInProgress(item: JsonMap): boolean {
  return ["queued", "running", "retry_wait"].includes(String(item.analysis_status || item.latest_analysis?.status || ""));
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
  if (!resumes.value.some((item) => item.latest_version?.id === matchForm.resume_version_id)) {
    matchForm.resume_version_id = resumes.value[0]?.latest_version?.id || "";
  }
  if (!preferences.value.some((item) => item.version?.id === matchForm.preference_version_id)) {
    matchForm.preference_version_id = preferences.value.find((item) => item.is_default)?.version?.id || preferences.value[0]?.version?.id || "";
  }
}

async function load() {
  loading.value = true;
  error.value = "";
  try {
    await refreshData();
    const poolId = String(route.query.pool_item_id || "");
    if (poolId) await openItem({ id: poolId });
    const legacyDraftId = String(route.query.browser_draft_id || "");
    if (legacyDraftId) {
      const draft = await api<JsonMap>(`/api/v1/browser/job-drafts/${encodeURIComponent(legacyDraftId)}/web`);
      let pool = draft.job_pool_item;
      if (!pool) {
        const result = await api<JsonMap>("/api/v1/job-pool/items", {
          method: "POST",
          idempotencyKey: idempotencyKey(`legacy-browser-pool-${draft.id}`),
          body: { source: { type: "browser_draft", browser_draft_id: draft.id } },
        });
        pool = result.job_pool_item || result;
      }
      await router.replace({ query: { ...route.query, browser_draft_id: undefined, pool_item_id: pool.id } });
      await openItem(pool);
      success.value = "岗位已直接写入匹配池；需要时再选择简历和岗位期望开始匹配。";
      await refreshData();
    }
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
    const jobVersion = await createJobVersion();
    const result = await api<JsonMap>("/api/v1/job-pool/items", {
      method: "POST",
      idempotencyKey: idempotencyKey("pool"),
      body: {
        source: { type: "document_version", job_document_version_id: jobVersion.id },
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
    if (!matchForm.resume_version_id || !matchForm.preference_version_id) throw new Error("请先选择已确认简历和岗位期望");
    const result = await api<JsonMap>(`/api/v1/job-pool/items/${encodeURIComponent(selected.value.id)}/analyze`, {
      method: "POST",
      idempotencyKey: idempotencyKey("pool-analysis"),
      body: {
        resume_document_version_id: matchForm.resume_version_id,
        preference_version_id: matchForm.preference_version_id,
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
  if (!target) return;
  // 点击事件中先创建标签页，避免等待服务端记账后被浏览器当作弹窗拦截。
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
    if (response.status !== 303 && response.type !== "opaqueredirect") {
      throw new Error("去投递记录未能保存，请稍后重试");
    }
    destination.location.replace(target);
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
    <PageHeader eyebrow="MATCH POOL" title="匹配池" description="岗位先快速入库；需要分析时再选择简历和岗位期望，匹配与采集互不阻塞。"><button class="button outline small" type="button" @click="load">刷新</button></PageHeader>
    <AsyncState :loading="loading" :error="error" :success="success" />
    <div v-if="activeTask" class="callout opportunity task-inline-state"><span class="spinner" />{{ taskLabel(activeTask.task_type) }}：{{ statusLabel(activeTask.status) }} · {{ activeTask.current_step || "等待执行" }}</div>

    <div v-if="!loading" class="grid-2">
      <section class="card"><div class="card-head"><div><h3>保存手动岗位</h3><p>先保存岗位本身，不需要选择简历或岗位期望；匹配时再决定使用哪一版。</p></div></div><form id="pool-form" class="card-body form-card" @submit.prevent="savePool"><div class="form-row"><div class="field-group"><label>岗位名称</label><input v-model="form.job_title" class="field" name="job_title" placeholder="例如：高级前端工程师" required /></div><div class="field-group"><label>公司</label><input v-model="form.company_name" class="field" name="company_name" placeholder="未披露可留空" /></div></div><div class="form-row"><div class="field-group"><label>工作地点</label><input v-model="form.location_text" class="field" name="location_text" placeholder="例如：杭州、上海" /></div><div class="field-group"><label>办公方式</label><select v-model="form.work_mode" class="select" name="work_mode"><option value="">未披露</option><option value="onsite">现场</option><option value="hybrid">混合</option><option value="remote">远程</option></select></div></div><div class="field-group"><label>薪资原文</label><input v-model="form.salary_text" class="field" name="salary_text" placeholder="例如：20–30K/月·14薪；未知可留空" /></div><div class="field-group"><label>岗位 JD</label><textarea v-model="form.job_text" class="textarea" name="job_text" placeholder="粘贴完整职责和任职要求" required /></div><button class="button primary" :disabled="busy" type="submit">保存岗位</button></form></section>
      <section class="card"><div class="card-head"><div><h3>我的岗位</h3><p>抓取或保存后立即入库；点击“匹配”时才选择简历和期望。</p></div><span class="tag neutral">{{ items.length }} 条</span></div><div class="card-body data-list"><div v-if="!items.length" class="empty"><div><strong>匹配池还是空的</strong><p>先抓取或保存一份岗位。</p></div></div><article v-for="item in items" :key="item.id" class="data-row pool-item"><div><strong>{{ item.job_title }}</strong><small>{{ item.company_name || "未标注公司" }} · {{ statusLabel(item.analysis_status) }} · {{ item.latest_analysis?.ability_score ?? "—" }} 分</small></div><div class="item-actions"><span class="tag" :class="statusClass(item.analysis_status)">{{ statusLabel(item.analysis_status) }}</span><button class="button soft small" type="button" @click="openItem(item)">{{ analysisReady(item) ? "查看岗位" : analysisInProgress(item) ? "查看进度" : "匹配" }}</button><RouterLink v-if="analysisReady(item)" class="button outline small" :to="{ path: '/app/seeker/report', query: { analysis_id: item.latest_analysis.id } }">完整报告</RouterLink><button class="button link-button small" :disabled="busy" type="button" @click="deletePoolItem(item)">删除</button></div></article></div></section>
    </div>

    <section v-if="selected" class="card" style="margin-top:18px">
      <div class="card-head"><div><h3>{{ selected.job_title }}</h3><p>{{ selected.company_name || "未标注公司" }} · {{ statusLabel(selected.analysis_status) }} · 版本 {{ selected.revision }}</p></div><button class="button link-button" type="button" @click="selected = null">关闭</button></div>
      <div class="card-body"><div v-if="selected.blocking_reasons?.length" class="callout opportunity">{{ selected.blocking_reasons.join("；") }}</div><div class="job-condition-summary"><div><strong>地点</strong><span>{{ selectedFields.location_text || (selectedFields.locations || []).join("、") || "未披露" }}</span></div><div><strong>办公方式</strong><span>{{ statusLabel(selectedFields.work_mode || "unknown") }}</span></div><div><strong>薪资</strong><span>{{ selectedFields.salary_text || "未披露" }}</span></div></div><div class="version-strip"><span class="version-chip">岗位版本 · {{ selected.job_document_version?.id }}</span><span class="version-chip">简历版本 · {{ selected.resume_version_id || "待选择" }}</span></div><details class="raw-report"><summary>查看岗位结构化高级信息</summary><pre>{{ JSON.stringify(selected.job_content || {}, null, 2) }}</pre></details><section v-if="analysisInProgress(selected)" class="callout opportunity" style="margin-top:18px">分析任务正在执行，请等待任务完成后再进行下一次匹配。</section><section v-else-if="!analysisReady(selected)" class="match-panel" style="margin-top:18px"><div class="card-head"><div><h3>开始匹配</h3><p>岗位已经入库；现在才选择本次匹配使用的简历和岗位期望，并确认消耗 1 次分析。</p></div><span class="tag opportunity">待匹配</span></div><div class="form-row"><div class="field-group"><label>简历版本</label><select v-model="matchForm.resume_version_id" class="select" required><option value="" disabled>请选择已确认简历</option><option v-for="item in resumes" :key="item.id" :value="item.latest_version.id">{{ item.title }} · v{{ item.latest_version.version_no }}</option></select></div><div class="field-group"><label>岗位期望</label><select v-model="matchForm.preference_version_id" class="select" required><option value="" disabled>请选择完整的一条期望</option><option v-for="item in preferences" :key="item.id" :value="item.version.id">{{ item.display_name }} · v{{ item.version.version_no }}</option></select></div></div></section><div class="item-actions" style="margin-top:14px"><RouterLink v-if="analysisReady(selected)" class="button primary" :to="{ path: '/app/seeker/report', query: { analysis_id: selected.latest_analysis.id } }">打开完整报告</RouterLink><button v-if="selected.apply_action?.available" class="button soft" type="button" @click="goApply">去投递</button><span v-else-if="selected.source_url" class="tag neutral">原岗位链接当前不可用</span><button v-if="!analysisInProgress(selected)" class="button primary" type="button" :disabled="busy" @click="analyzeSelected">{{ analysisReady(selected) ? "使用新输入重新匹配" : "开始匹配（消耗 1 次）" }}</button><button class="button link-button" type="button" :disabled="busy" @click="deletePoolItem(selected)">删除岗位</button></div></div>
    </section>
  </AppShell>
</template>
