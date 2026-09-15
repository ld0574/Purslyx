<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useRoute } from "vue-router";

import AppShell from "@/components/AppShell.vue";
import AsyncState from "@/components/AsyncState.vue";
import PageHeader from "@/components/PageHeader.vue";
import { api, deleteWithImpact, idempotencyKey } from "@/services/api";
import type { JsonMap } from "@/types";

const route = useRoute(); const loading = ref(true); const busy = ref(false); const error = ref(""); const success = ref("");
const variants = ref<JsonMap[]>([]); const pool = ref<JsonMap[]>([]); const selected = ref<JsonMap | null>(null); const lastExport = ref<JsonMap | null>(null);
const currentVersion = computed(() => selected.value?.versions?.[0] || null);
function message(value: unknown) { error.value = value instanceof Error ? value.message : "操作失败"; }
async function load() {
  loading.value = true; error.value = "";
  try {
    const [variantResult, poolResult] = await Promise.all([api<JsonMap>("/api/v1/resumes"), api<JsonMap>("/api/v1/job-pool/items")]);
    variants.value = variantResult.items || []; pool.value = poolResult.items || [];
    const id = String(route.query.variant_id || "");
    if (id) selected.value = await api<JsonMap>(`/api/v1/resumes/${encodeURIComponent(id)}`);
  } catch (value) { message(value); }
  finally { loading.value = false; }
}
async function createVariant() {
  busy.value = true; error.value = "";
  try {
    const analysisId = String(route.query.analysis_id || "");
    let target = pool.value.find((item) => item.latest_analysis?.id === analysisId) || pool.value.find((item) => item.latest_analysis);
    if (!target) throw new Error("请先在匹配池生成报告");
    const result = await api<JsonMap>("/api/v1/resumes", { method: "POST", idempotencyKey: idempotencyKey("variant"), body: {
      job_pool_item_id: target.id, source_resume_version_id: target.resume_version_id,
      title: `${target.job_title} · 岗位版简历`, ...(route.query.rewrite_id ? { rewrite_id: String(route.query.rewrite_id) } : {}),
    } });
    selected.value = result; success.value = "岗位版简历已创建"; await load(); selected.value = result;
  } catch (value) { message(value); }
  finally { busy.value = false; }
}
async function openVariant(id: string) { try { selected.value = await api<JsonMap>(`/api/v1/resumes/${encodeURIComponent(id)}`); } catch (value) { message(value); } }
function move(rawIndex: string | number, direction: number) { const sections = currentVersion.value?.content?.sections; if (!sections) return; const index = Number(rawIndex); const target = index + direction; if (target < 0 || target >= sections.length) return; [sections[index], sections[target]] = [sections[target], sections[index]]; }
async function saveVersion() {
  if (!selected.value || !currentVersion.value) return;
  busy.value = true; error.value = "";
  try {
    const content = currentVersion.value.content;
    content.sections.forEach((section: JsonMap, index: number) => { section.position = index + 1; });
    const result = await api<JsonMap>(`/api/v1/resumes/${encodeURIComponent(selected.value.id)}/versions`, { method: "POST", idempotencyKey: idempotencyKey("variant-version"), body: {
      base_revision: selected.value.revision, content, layout: currentVersion.value.layout, template_version: currentVersion.value.template_version,
    } });
    selected.value = result; success.value = "岗位版新版本已保存"; await load(); selected.value = result;
  } catch (value) { message(value); }
  finally { busy.value = false; }
}
async function exportPdf() {
  if (!currentVersion.value) return;
  busy.value = true; error.value = "";
  try { const result = await api<JsonMap>("/api/v1/exports", { method: "POST", idempotencyKey: idempotencyKey("export"), body: { resume_variant_version_id: currentVersion.value.id } }); lastExport.value = result.export; success.value = "PDF 已生成，可下载"; }
  catch (value) { message(value); } finally { busy.value = false; }
}
async function deleteVariant(item: JsonMap) {
  busy.value = true; error.value = ""; success.value = "";
  try {
    const deleted = await deleteWithImpact(
      `/api/v1/resumes/${encodeURIComponent(item.id)}`,
      (impact) => `删除岗位版简历“${item.title}”将移除 ${impact.affected?.versions || 0} 个版本，并使 ${impact.affected?.exports || 0} 个 PDF 导出失效。确认删除？`,
    );
    if (!deleted) return;
    if (selected.value?.id === item.id) selected.value = null;
    lastExport.value = null;
    success.value = "岗位版简历及其 PDF 已删除";
    await load();
  } catch (value) { message(value); }
  finally { busy.value = false; }
}
onMounted(load);
</script>

<template><AppShell><PageHeader eyebrow="RESUME VARIANTS" title="岗位版简历" description="内容、模块顺序、排版预览和 PDF 使用同一个不可变版本。"><button class="button primary small" :disabled="busy" @click="createVariant">从报告创建</button><button class="button outline small" @click="load">刷新</button></PageHeader><AsyncState :loading="loading" :error="error" :success="success" />
  <div v-if="!loading" class="grid-2"><section class="card"><div class="card-head"><div><h3>已生成成品</h3><p>每个岗位版都可以继续编辑、导出或连同 PDF 删除。</p></div></div><div class="card-body data-list"><div v-if="!variants.length" class="empty"><div><strong>还没有岗位版简历</strong><p>从报告创建第一份成品。</p></div></div><article v-for="item in variants" :key="item.id" class="data-row"><div><strong>{{ item.title }}</strong><small>revision {{ item.revision }} · {{ item.status }}</small></div><div class="item-actions"><button class="button soft small" @click="openVariant(item.id)">编辑内容</button><button class="button link-button small" :disabled="busy" type="button" @click="deleteVariant(item)">删除</button></div></article></div></section><section class="card"><div class="card-head"><div><h3>PDF 成品</h3><p>ReportLab 生成 A4 PDF，长文本自动分页。</p></div></div><div class="card-body"><div v-if="lastExport" class="callout success">PDF {{ lastExport.id }} 已生成。</div><a v-if="lastExport?.file_available" class="button primary" :href="`/api/v1/exports/${lastExport.id}/file`" target="_blank">下载 PDF</a><div v-else class="empty"><div><strong>尚未导出</strong><p>保存版本后生成 PDF。</p></div></div></div></section></div>
  <section v-if="selected && currentVersion" class="card" style="margin-top:18px"><div class="card-head"><div><h3>编辑岗位版内容与排版</h3><p>保存会追加不可变版本；原始简历不会被覆盖。</p></div><div class="item-actions"><button class="button link-button" @click="selected = null">收起</button><button class="button link-button" :disabled="busy" type="button" @click="deleteVariant(selected)">删除岗位版</button></div></div><div class="card-body"><form class="form-card" @submit.prevent="saveVersion"><section v-for="(section, index) in currentVersion.content.sections" :key="section.section_key" class="report-card"><div class="data-row"><strong>{{ section.title }}</strong><div class="item-actions"><button class="button outline small" type="button" :disabled="index === 0" @click="move(index, -1)">上移</button><button class="button outline small" type="button" :disabled="index === currentVersion.content.sections.length - 1" @click="move(index, 1)">下移</button></div></div><div v-for="segment in section.segments" :key="segment.segment_key" class="field-group" style="margin-top:10px"><label>{{ segment.segment_key }}</label><textarea v-model="segment.text" class="textarea" style="min-height:100px" /></div></section><div class="form-row"><div class="field-group"><label>字号（pt）</label><input v-model.number="currentVersion.layout.font_size_pt" class="field" type="number" min="9" max="12" step="0.5" /></div><div class="field-group"><label>行距</label><input v-model.number="currentVersion.layout.line_height" class="field" type="number" min="1.2" max="1.8" step="0.1" /></div></div><div class="form-row"><div class="field-group"><label>模块间距（pt）</label><input v-model.number="currentVersion.layout.section_spacing_pt" class="field" type="number" min="4" max="16" /></div><div class="field-group"><label>字体</label><select v-model="currentVersion.layout.font_family" class="select"><option value="noto_sans_sc">Noto Sans SC / 系统无衬线</option></select></div></div><div class="item-actions"><button class="button primary" :disabled="busy" type="submit">保存新版本</button><button class="button soft" :disabled="busy" type="button" @click="exportPdf">生成 PDF</button></div></form></div></section>
</AppShell></template>
