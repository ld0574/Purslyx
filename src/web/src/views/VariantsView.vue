<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useRoute } from "vue-router";

import AppShell from "@/components/AppShell.vue";
import AsyncState from "@/components/AsyncState.vue";
import PageHeader from "@/components/PageHeader.vue";
import { api, deleteWithImpact, idempotencyKey, waitForTask } from "@/services/api";
import type { JsonMap } from "@/types";
import { statusLabel, taskLabel } from "@/utils/format";

const route = useRoute();
const loading = ref(true);
const busy = ref(false);
const error = ref("");
const success = ref("");
const variants = ref<JsonMap[]>([]);
const pool = ref<JsonMap[]>([]);
const selected = ref<JsonMap | null>(null);
const lastExport = ref<JsonMap | null>(null);
const activeTask = ref<JsonMap | null>(null);
const savedSnapshot = ref("");
const draggedIndex = ref<number | null>(null);
const previewFlow = ref<HTMLElement | null>(null);
const previewBody = ref<HTMLElement | null>(null);
const previewPageCount = ref(1);
const previewPageHeight = ref(720);
let previewObserver: ResizeObserver | null = null;

const currentVersion = computed<JsonMap | null>(() => selected.value?.versions?.[0] || null);
const isDirty = computed(() => currentVersion.value ? versionSnapshot(currentVersion.value) !== savedSnapshot.value : false);
const previewStyle = computed(() => ({
  "--resume-font-size": `${Number(currentVersion.value?.layout?.font_size_pt || 10.5)}pt`,
  "--resume-line-height": String(Number(currentVersion.value?.layout?.line_height || 1.4)),
  "--resume-section-spacing": `${Number(currentVersion.value?.layout?.section_spacing_pt || 8)}pt`,
  "--preview-page-height": `${previewPageHeight.value}px`,
  fontFamily: currentVersion.value?.layout?.font_family === "source_han_serif"
    ? '"Songti SC", "STSong", serif'
    : '"PingFang SC", "Microsoft YaHei", sans-serif',
  minHeight: `${previewPageHeight.value * previewPageCount.value}px`,
}));
const previewRisk = computed(() => {
  const segments = (currentVersion.value?.content?.sections || []).flatMap((section: JsonMap) => section.segments || []);
  if (segments.some((segment: JsonMap) => !String(segment.text || "").trim())) {
    return { tone: "attention", text: "存在空段落，保存前需要补充或删除。" };
  }
  if (segments.some((segment: JsonMap) => String(segment.text || "").length > 5000)) {
    return { tone: "opportunity", text: "存在超长段落，PDF 会自动跨页；建议导出后重点检查段落断点。" };
  }
  return { tone: "success", text: `内容预计流入 ${previewPageCount.value} 页，当前预览未发现空白或截字风险。` };
});

function message(value: unknown) {
  error.value = value instanceof Error ? value.message : "操作失败";
}

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value));
}

function versionSnapshot(version: JsonMap): string {
  return JSON.stringify({ content: version.content, layout: version.layout });
}

function prepareVersion(version: JsonMap) {
  version.layout ||= {};
  version.layout.bold_segment_keys = Array.isArray(version.layout.bold_segment_keys) ? version.layout.bold_segment_keys : [];
  version.layout.section_order = Array.isArray(version.layout.section_order)
    ? version.layout.section_order
    : (version.content?.sections || []).map((section: JsonMap) => section.section_key);
}

function selectVariant(value: JsonMap) {
  selected.value = value;
  lastExport.value = value.exports?.[0] || null;
  if (currentVersion.value) {
    prepareVersion(currentVersion.value);
    savedSnapshot.value = versionSnapshot(currentVersion.value);
  }
  nextTick(measurePreview);
}

async function refreshLists() {
  const [variantResult, poolResult] = await Promise.all([
    api<JsonMap>("/api/v1/resumes"),
    api<JsonMap>("/api/v1/job-pool/items"),
  ]);
  variants.value = variantResult.items || [];
  pool.value = poolResult.items || [];
}

async function load() {
  loading.value = true;
  error.value = "";
  try {
    await refreshLists();
    const id = String(route.query.variant_id || selected.value?.id || "");
    if (id) selectVariant(await api<JsonMap>(`/api/v1/resumes/${encodeURIComponent(id)}`));
  } catch (value) {
    message(value);
  } finally {
    loading.value = false;
  }
}

async function createVariant() {
  busy.value = true;
  error.value = "";
  success.value = "";
  try {
    const analysisId = String(route.query.analysis_id || "");
    const target = pool.value.find((item) => item.latest_analysis?.id === analysisId) || pool.value.find((item) => item.latest_analysis);
    if (!target) throw new Error("请先在匹配池生成报告");
    const result = await api<JsonMap>("/api/v1/resumes", {
      method: "POST",
      idempotencyKey: idempotencyKey("variant"),
      body: {
        job_pool_item_id: target.id,
        source_resume_version_id: target.resume_version_id,
        title: `${target.job_title} · 岗位版简历`,
        ...(route.query.rewrite_id ? { rewrite_id: String(route.query.rewrite_id) } : {}),
      },
    });
    selectVariant(result);
    await refreshLists();
    success.value = "岗位版简历已创建，可以继续调整内容和排版";
  } catch (value) {
    message(value);
  } finally {
    busy.value = false;
  }
}

async function openVariant(id: string) {
  error.value = "";
  try {
    selectVariant(await api<JsonMap>(`/api/v1/resumes/${encodeURIComponent(id)}`));
  } catch (value) {
    message(value);
  }
}

function syncSectionOrder() {
  if (!currentVersion.value) return;
  const sections = currentVersion.value.content.sections || [];
  sections.forEach((section: JsonMap, index: number) => { section.position = index + 1; });
  currentVersion.value.layout.section_order = sections.map((section: JsonMap) => section.section_key);
}

function move(rawIndex: string | number, direction: number) {
  const sections = currentVersion.value?.content?.sections;
  if (!sections) return;
  const index = Number(rawIndex);
  const target = index + direction;
  if (target < 0 || target >= sections.length) return;
  [sections[index], sections[target]] = [sections[target], sections[index]];
  syncSectionOrder();
}

function onDragStart(index: string | number, event: DragEvent) {
  const numericIndex = Number(index);
  draggedIndex.value = numericIndex;
  if (event.dataTransfer) {
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("text/plain", String(numericIndex));
  }
}

function onDrop(index: string | number) {
  const from = draggedIndex.value;
  const sections = currentVersion.value?.content?.sections;
  const numericIndex = Number(index);
  if (from === null || !sections || from === numericIndex) return;
  const [section] = sections.splice(from, 1);
  sections.splice(numericIndex, 0, section);
  draggedIndex.value = null;
  syncSectionOrder();
}

function isBold(segmentKey: string): boolean {
  return Boolean(currentVersion.value?.layout?.bold_segment_keys?.includes(segmentKey));
}

async function persistVersion(): Promise<JsonMap> {
  if (!selected.value || !currentVersion.value) throw new Error("请先选择岗位版简历");
  syncSectionOrder();
  const result = await api<JsonMap>(`/api/v1/resumes/${encodeURIComponent(selected.value.id)}/versions`, {
    method: "POST",
    idempotencyKey: idempotencyKey("variant-version"),
    body: {
      base_revision: selected.value.revision,
      content: clone(currentVersion.value.content),
      layout: clone(currentVersion.value.layout),
      template_version: currentVersion.value.template_version,
      ...(currentVersion.value.rewrite_id ? { rewrite_id: currentVersion.value.rewrite_id } : {}),
    },
  });
  selectVariant(result);
  await refreshLists();
  return result;
}

async function saveVersion() {
  busy.value = true;
  error.value = "";
  success.value = "";
  try {
    const result = await persistVersion();
    success.value = `岗位版 v${result.versions?.[0]?.version_no} 已保存`;
  } catch (value) {
    message(value);
  } finally {
    busy.value = false;
  }
}

async function exportPdf() {
  if (!currentVersion.value) return;
  busy.value = true;
  error.value = "";
  success.value = "";
  try {
    if (isDirty.value) {
      await persistVersion();
      success.value = "修改已先保存，正在生成 PDF";
    }
    const result = await api<JsonMap>("/api/v1/exports", {
      method: "POST",
      idempotencyKey: idempotencyKey("export"),
      body: { resume_variant_version_id: currentVersion.value?.id },
    });
    lastExport.value = result.export;
    activeTask.value = result.task || null;
    if (result.task) {
      await waitForTask(result.task, { onUpdate: (task) => { activeTask.value = task; } });
    }
    const exportId = activeTask.value?.result?.resource_id || result.export?.id;
    if (!exportId) throw new Error("PDF 任务完成，但没有返回导出引用");
    lastExport.value = await api<JsonMap>(`/api/v1/exports/${encodeURIComponent(exportId)}`);
    activeTask.value = null;
    success.value = "PDF 已生成，可下载";
  } catch (value) {
    message(value);
  } finally {
    busy.value = false;
  }
}

async function deleteVariant(item: JsonMap) {
  busy.value = true;
  error.value = "";
  success.value = "";
  try {
    const deleted = await deleteWithImpact(
      `/api/v1/resumes/${encodeURIComponent(item.id)}`,
      (impact) => `删除岗位版简历“${item.title}”将移除 ${impact.affected?.versions || 0} 个版本，并使 ${impact.affected?.exports || 0} 个 PDF 导出失效。确认删除？`,
    );
    if (!deleted) return;
    if (selected.value?.id === item.id) selected.value = null;
    lastExport.value = null;
    savedSnapshot.value = "";
    success.value = "岗位版简历及其 PDF 已删除";
    await refreshLists();
  } catch (value) {
    message(value);
  } finally {
    busy.value = false;
  }
}

function measurePreview() {
  const flow = previewFlow.value;
  const body = previewBody.value;
  if (!flow || !body) return;
  const pageHeight = Math.max(480, flow.clientWidth * 297 / 210);
  previewPageHeight.value = pageHeight;
  previewPageCount.value = Math.max(1, Math.ceil((body.scrollHeight + 64) / pageHeight));
}

watch(currentVersion, () => { nextTick(measurePreview); }, { deep: true });

onMounted(async () => {
  await load();
  await nextTick();
  measurePreview();
  if (typeof ResizeObserver !== "undefined" && previewFlow.value) {
    previewObserver = new ResizeObserver(measurePreview);
    previewObserver.observe(previewFlow.value);
  }
});

onBeforeUnmount(() => previewObserver?.disconnect());
</script>

<template>
  <AppShell>
    <PageHeader eyebrow="RESUME VARIANTS" title="岗位版简历" description="拖动模块、编辑内容和排版后实时预览；保存版本与导出 PDF 使用同一份数据。">
      <button class="button primary small" :disabled="busy" type="button" @click="createVariant">从报告创建</button>
      <button class="button outline small" type="button" @click="load">刷新</button>
    </PageHeader>
    <AsyncState :loading="loading" :error="error" :success="success" />
    <div v-if="activeTask" class="callout opportunity task-inline-state"><span class="spinner" />{{ taskLabel(activeTask.task_type) }}：{{ statusLabel(activeTask.status) }} · {{ activeTask.current_step || "等待执行" }}</div>

    <div v-if="!loading" class="grid-2">
      <section class="card"><div class="card-head"><div><h3>岗位版记录</h3><p>每次保存都会追加不可变版本，原始简历不被覆盖。</p></div><span class="tag neutral">{{ variants.length }} 份</span></div><div class="card-body data-list"><div v-if="!variants.length" class="empty"><div><strong>还没有岗位版简历</strong><p>从一份完成的岗位报告创建。</p></div></div><article v-for="item in variants" :key="item.id" class="data-row"><div><strong>{{ item.title }}</strong><small>最新 v{{ item.versions?.[0]?.version_no || 1 }} · 版本 {{ item.revision }}</small></div><div class="item-actions"><button class="button soft small" type="button" @click="openVariant(item.id)">编辑与预览</button><button class="button link-button small" :disabled="busy" type="button" @click="deleteVariant(item)">删除</button></div></article></div></section>
      <section class="card"><div class="card-head"><div><h3>PDF 成品</h3><p>由服务端按 A4 自动分页，刷新页面后仍可继续下载。</p></div></div><div class="card-body"><div v-if="lastExport" class="callout" :class="lastExport.file_available ? 'success' : 'opportunity'">PDF {{ lastExport.id }} · {{ lastExport.file_available ? `${lastExport.page_count || "?"} 页，可下载` : statusLabel(lastExport.status) }}</div><a v-if="lastExport?.file_available" class="button primary" :href="`/api/v1/exports/${lastExport.id}/file`" target="_blank">下载 PDF</a><div v-else class="empty"><div><strong>尚无可下载成品</strong><p>选择岗位版，保存修改后生成 PDF。</p></div></div></div></section>
    </div>

    <section v-if="selected && currentVersion" class="card variant-workbench" style="margin-top:18px">
      <div class="card-head"><div><h3>{{ selected.title }}</h3><p>当前 v{{ currentVersion.version_no }} · {{ isDirty ? "有未保存修改" : "已与服务端版本一致" }}</p></div><div class="item-actions"><span class="tag" :class="isDirty ? 'opportunity' : 'success'">{{ isDirty ? "未保存" : "已保存" }}</span><button class="button link-button" type="button" @click="selected = null">收起</button></div></div>
      <div class="card-body variant-editor-grid">
        <form class="form-card variant-controls" @submit.prevent="saveVersion">
          <div class="layout-toolbar">
            <div class="field-group"><label>字号</label><input v-model.number="currentVersion.layout.font_size_pt" class="field" type="number" min="9" max="12" step="0.5" /></div>
            <div class="field-group"><label>行距</label><input v-model.number="currentVersion.layout.line_height" class="field" type="number" min="1.2" max="1.8" step="0.1" /></div>
            <div class="field-group"><label>模块间距</label><input v-model.number="currentVersion.layout.section_spacing_pt" class="field" type="number" min="4" max="16" step="1" /></div>
            <div class="field-group"><label>字体</label><select v-model="currentVersion.layout.font_family" class="select"><option value="noto_sans_sc">中文无衬线</option><option value="source_han_serif">中文宋体</option></select></div>
          </div>
          <p class="micro">拖动模块左上角的手柄排序；加粗按完整段落控制，导出的 PDF 使用相同设置。</p>
          <div class="variant-sections">
            <section
              v-for="(section, index) in currentVersion.content.sections"
              :key="section.section_key"
              class="variant-section draggable-section"
              :class="{ dragging: draggedIndex === index }"
              draggable="true"
              @dragstart="onDragStart(index, $event)"
              @dragover.prevent
              @drop.prevent="onDrop(index)"
              @dragend="draggedIndex = null"
            >
              <div class="data-row section-drag-head"><div class="drag-title"><span class="drag-handle" aria-hidden="true">⠿</span><strong>{{ section.title }}</strong></div><div class="item-actions"><button class="button outline small" type="button" :disabled="index === 0" @click="move(index, -1)">上移</button><button class="button outline small" type="button" :disabled="index === currentVersion.content.sections.length - 1" @click="move(index, 1)">下移</button></div></div>
              <div v-for="segment in section.segments" :key="segment.segment_key" class="segment-layout-editor"><div class="segment-editor-head"><label>{{ segment.segment_key }}</label><label class="bold-toggle"><input v-model="currentVersion.layout.bold_segment_keys" type="checkbox" :value="segment.segment_key" /> 加粗整段</label></div><textarea v-model="segment.text" class="textarea" /></div>
            </section>
          </div>
          <div class="item-actions"><button class="button primary" :disabled="busy || !isDirty" type="submit">保存新版本</button><button class="button soft" :disabled="busy" type="button" @click="exportPdf">{{ isDirty ? "保存并生成 PDF" : "生成 PDF" }}</button><button class="button link-button" :disabled="busy" type="button" @click="deleteVariant(selected)">删除岗位版</button></div>
        </form>

        <aside class="resume-preview-panel">
          <div class="preview-heading"><div><div class="eyebrow">A4 LIVE PREVIEW</div><h3>实时排版预览</h3></div><span class="tag brand">预计 {{ previewPageCount }} 页</span></div>
          <div class="callout preview-risk" :class="previewRisk.tone">{{ previewRisk.text }}</div>
          <div ref="previewFlow" class="resume-preview-flow" :style="previewStyle">
            <span v-for="page in Math.max(0, previewPageCount - 1)" :key="page" class="page-guide" :style="{ top: `${previewPageHeight * page}px` }"><i>第 {{ page + 1 }} 页结束</i></span>
            <div ref="previewBody" class="resume-preview-content">
              <h1>{{ selected.title }}</h1>
              <div class="resume-title-rule" />
              <section v-for="section in currentVersion.content.sections" :key="`preview-${section.section_key}`"><h2>{{ section.title }}</h2><p v-for="segment in section.segments" :key="`preview-${segment.segment_key}`" :class="{ bold: isBold(segment.segment_key) }">• {{ segment.text }}</p></section>
              <footer>Purslyx · 内容来自用户已确认资料</footer>
            </div>
          </div>
          <p class="micro preview-note">页数为浏览器实时估算；下载文件由服务端使用同一内容和排版参数重新计算分页，最终以 PDF 为准。</p>
        </aside>
      </div>
    </section>
  </AppShell>
</template>
