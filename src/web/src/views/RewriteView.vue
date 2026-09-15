<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";
import { useRoute, useRouter } from "vue-router";

import AppShell from "@/components/AppShell.vue";
import AsyncState from "@/components/AsyncState.vue";
import PageHeader from "@/components/PageHeader.vue";
import { api, deleteWithImpact, idempotencyKey, waitForTask } from "@/services/api";
import type { JsonMap } from "@/types";
import { statusLabel, taskLabel } from "@/utils/format";

const route = useRoute(); const router = useRouter();
const loading = ref(true); const busy = ref(false); const error = ref(""); const success = ref("");
const analysis = ref<JsonMap | null>(null); const source = ref<JsonMap | null>(null); const facts = ref<JsonMap[]>([]); const rewrite = ref<JsonMap | null>(null);
const activeTask = ref<JsonMap | null>(null);
const selectedSegments = ref<string[]>([]); const selectedFacts = ref<string[]>([]);
const factForm = reactive({ fact_category: "项目职责", fact_text: "", source_segment_key: "" });
const segments = computed(() => (source.value?.version?.content?.sections || []).flatMap((section: JsonMap) => section.segments || []));

function message(value: unknown) { error.value = value instanceof Error ? value.message : "操作失败"; }
function markEdited(segment: JsonMap) { segment.edited_dirty = true; }
function decisionLabel(segment: JsonMap) {
  if (segment.edited_dirty) return "修改未保存";
  return ({ adopt: "已采用", keep_original: "保留原文", revert: "已撤回" } as Record<string, string>)[String(segment.current_decision || "")] || "待确认";
}
async function resolveSource() {
  const resumeId = analysis.value?.input_versions?.find((item: JsonMap) => item.type === "resume")?.id;
  const docs = (await api<JsonMap>("/api/v1/documents")).items || [];
  for (const item of docs.filter((value: JsonMap) => value.document_type === "resume")) {
    try {
      const detail = await api<JsonMap>(`/api/v1/documents/${encodeURIComponent(item.id)}?version_id=${encodeURIComponent(resumeId)}`);
      if (detail.version?.id === resumeId) { source.value = { document: item, version: detail.version }; return; }
    } catch { /* 继续尝试本账号的下一份简历，避免泄露其他资源。 */ }
  }
  throw new Error("报告引用的简历版本已不可用");
}
async function load() {
  loading.value = true; error.value = "";
  try {
    let id = String(route.query.analysis_id || "");
    if (!id) id = ((await api<JsonMap>("/api/v1/analyses")).items || [])[0]?.id || "";
    if (!id) throw new Error("请先从匹配池生成报告");
    analysis.value = await api<JsonMap>(`/api/v1/analyses/${encodeURIComponent(id)}`);
    await resolveSource();
    facts.value = (await api<JsonMap>("/api/v1/facts")).items || [];
    selectedSegments.value = segments.value.slice(0, 1).map((item: JsonMap) => item.segment_key);
    factForm.source_segment_key = selectedSegments.value[0] || "";
    const rewriteId = String(route.query.rewrite_id || "");
    if (rewriteId) rewrite.value = await api<JsonMap>(`/api/v1/rewrites/${encodeURIComponent(rewriteId)}`);
  } catch (value) { message(value); }
  finally { loading.value = false; }
}
async function saveFact() {
  if (!source.value) return;
  busy.value = true; error.value = "";
  try {
    const result = await api<JsonMap>("/api/v1/facts", { method: "POST", idempotencyKey: idempotencyKey("fact"), body: {
      document_id: source.value.document.id, source_document_version_id: source.value.version.id,
      source_segment_key: factForm.source_segment_key || null, fact_category: factForm.fact_category,
      fact_text: factForm.fact_text, source_type: "user_added",
    } });
    facts.value = [result, ...facts.value]; factForm.fact_text = ""; success.value = "事实已保存并生成版本";
  } catch (value) { message(value); }
  finally { busy.value = false; }
}
async function createRewrite() {
  if (!analysis.value || !source.value || !selectedSegments.value.length) return;
  busy.value = true; error.value = "";
  try {
    const result = await api<JsonMap>("/api/v1/rewrites", { method: "POST", idempotencyKey: idempotencyKey("rewrite"), body: {
      analysis_id: analysis.value.id, source_resume_version_id: source.value.version.id,
      segment_keys: selectedSegments.value, fact_version_ids: selectedFacts.value, confirm_usage: true,
    } });
    rewrite.value = result.rewrite;
    activeTask.value = result.task || null;
    if (result.task) {
      success.value = "改写任务已受理，正在逐段生成建议";
      await waitForTask(result.task, { onUpdate: (task) => { activeTask.value = task; } });
      rewrite.value = await api<JsonMap>(`/api/v1/rewrites/${encodeURIComponent(result.rewrite.id)}`);
      activeTask.value = null;
    }
    success.value = "逐段改写已经生成";
    await router.replace({ query: { ...route.query, analysis_id: analysis.value.id, rewrite_id: rewrite.value?.id } });
  } catch (value) { message(value); }
  finally { busy.value = false; }
}
async function decide(segment: JsonMap, decision: string) {
  if (!rewrite.value) return;
  busy.value = true; error.value = "";
  try {
    const result = await api<JsonMap>(`/api/v1/rewrites/${encodeURIComponent(rewrite.value.id)}/segments/${encodeURIComponent(segment.id)}/decisions`, { method: "POST", idempotencyKey: idempotencyKey("rewrite-decision"), body: {
      decision, base_decision_no: segment.decision_no || 0,
      ...(decision === "edited" ? { edited_text: segment.edited_text || segment.suggested_text } : {}),
    } });
    rewrite.value = result.rewrite || result; success.value = decision === "edited" ? "编辑已保存，需要以新决定为准" : "改写决定已保存";
  } catch (value) { message(value); }
  finally { busy.value = false; }
}
async function deleteRewrite() {
  if (!rewrite.value) return;
  busy.value = true; error.value = ""; success.value = "";
  try {
    const deleted = await deleteWithImpact(
      `/api/v1/rewrites/${encodeURIComponent(rewrite.value.id)}`,
      (impact) => `删除这次改写将移除 ${impact.affected?.segments || 0} 个建议段落和全部采用决定；已生成岗位版保留内容快照，但不再关联本次改写。确认删除？`,
    );
    if (!deleted) return;
    rewrite.value = null;
    const query = { ...route.query }; delete query.rewrite_id;
    await router.replace({ query });
    success.value = "改写结果已删除";
  } catch (value) { message(value); }
  finally { busy.value = false; }
}
onMounted(load);
</script>

<template><AppShell><PageHeader eyebrow="FACTS & REWRITE" title="事实与改写" description="先补充本人确认的事实，再按段生成建议；生成与采用分开。"><button class="button outline small" @click="load">刷新</button></PageHeader><AsyncState :loading="loading" :error="error" :success="success" />
  <div v-if="activeTask" class="callout opportunity task-inline-state"><span class="spinner" />{{ taskLabel(activeTask.task_type) }}：{{ statusLabel(activeTask.status) }} · {{ activeTask.current_step || '等待执行' }}</div>
  <template v-if="analysis && source"><div class="grid-2"><section class="card"><div class="card-head"><div><h3>补充真实事实</h3><p>不知道或没有做过可以不填，系统不会补造数字。</p></div></div><form class="card-body form-card" @submit.prevent="saveFact"><div class="field-group"><label>对应原文段落</label><select v-model="factForm.source_segment_key" class="select"><option v-for="item in segments" :key="item.segment_key" :value="item.segment_key">{{ item.text }}</option></select></div><div class="field-group"><label>事实分类</label><input v-model="factForm.fact_category" class="field" /></div><div class="field-group"><label>本人补充</label><textarea v-model="factForm.fact_text" class="textarea" required /></div><button class="button primary" :disabled="busy" type="submit">保存事实</button></form></section>
    <section class="card"><div class="card-head"><div><h3>选择改写依据</h3><p>一次可选择多个原文段落和事实版本。</p></div></div><form class="card-body form-card" @submit.prevent="createRewrite"><div class="data-list"><label v-for="item in segments" :key="item.segment_key" class="data-row"><span><strong>{{ item.text }}</strong><small>{{ item.segment_key }}</small></span><input v-model="selectedSegments" type="checkbox" :value="item.segment_key" /></label></div><div class="data-list"><label v-for="item in facts" :key="item.id" class="data-row"><span><strong>{{ item.fact_category }}</strong><small>{{ item.fact_text }}</small></span><input v-model="selectedFacts" type="checkbox" :value="item.version?.id" /></label></div><button class="button primary" :disabled="busy || !selectedSegments.length" type="submit">确认消耗 1 次并生成建议</button></form></section></div>
  <section v-if="rewrite" class="card" style="margin-top:18px"><div class="card-head"><div><h3>逐段改写对照</h3><p>原文、建议、理由和依据始终一起展示。</p></div><div class="item-actions"><span class="tag" :class="rewrite.status === 'available' ? 'success' : 'opportunity'">{{ statusLabel(rewrite.status) }}</span><button class="button link-button small" :disabled="busy" type="button" @click="deleteRewrite">删除改写</button></div></div><div class="card-body stack"><div v-if="rewrite.status !== 'available'" class="callout opportunity">建议仍在生成，完成后会在本页恢复。</div><article v-for="segment in rewrite.segments || []" :key="segment.id" class="report-card"><div class="grid-2"><div><div class="eyebrow">原始表达</div><p style="margin-top:10px;line-height:1.7">{{ segment.original_text }}</p></div><div><div class="eyebrow">建议表达 · 可编辑</div><textarea v-model="segment.edited_text" class="textarea" style="min-height:120px" :placeholder="segment.suggested_text" @input="markEdited(segment)" /></div></div><div class="callout">{{ segment.rationale }}</div><div v-for="evidence in segment.evidence || []" :key="evidence.id" class="evidence">{{ evidence.quote }}</div><div class="item-actions" style="margin-top:14px"><button class="button soft small" @click="decide(segment, 'keep_original')">保留原文</button><button class="button outline small" @click="decide(segment, 'edited')">保存编辑</button><button class="button primary small" @click="decide(segment, 'adopt')">采用建议</button><button v-if="segment.current_decision === 'adopt' && !segment.edited_dirty" class="button link-button small" @click="decide(segment, 'revert')">撤回采用</button><span class="tag" :class="segment.edited_dirty ? 'opportunity' : 'neutral'">{{ decisionLabel(segment) }}</span></div></article><div v-if="rewrite.status === 'available'" class="item-actions"><RouterLink class="button primary" :to="{ path: '/app/seeker/variants', query: { analysis_id: analysis.id, rewrite_id: rewrite.id } }">进入岗位版简历</RouterLink></div></div></section></template>
</AppShell></template>
