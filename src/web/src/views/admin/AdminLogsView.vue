<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";

import AppShell from "@/components/AppShell.vue";
import AsyncState from "@/components/AsyncState.vue";
import PageHeader from "@/components/PageHeader.vue";
import { api, idempotencyKey } from "@/services/api";
import { useAuthStore } from "@/stores/auth";
import type { JsonMap } from "@/types";
import { errorMessage, formatDate, statusClass, statusLabel, taskLabel } from "@/utils/format";

type LogType = "operations" | "security" | "tasks";

const auth = useAuthStore();
const loading = ref(true);
const busy = ref(false);
const error = ref("");
const success = ref("");
const logType = ref<LogType>("operations");
const items = ref<JsonMap[]>([]);
const page = ref<JsonMap>({});
const selected = ref<JsonMap | null>(null);
const lastExport = ref<JsonMap | null>(null);
const filters = reactive({ created_from: "", created_to: "", request_id: "", result: "", keyword: "" });

const typeOptions = computed(() => [
  { value: "operations" as LogType, label: "操作日志", permission: "admin.logs.operations.read" },
  { value: "security" as LogType, label: "安全日志", permission: "admin.logs.security.read" },
  { value: "tasks" as LogType, label: "任务日志", permission: "admin.logs.tasks.read" },
].filter((item) => auth.permissions.has(item.permission)));
const currentLabel = computed(() => typeOptions.value.find((item) => item.value === logType.value)?.label || "日志");
const canExport = computed(() => auth.permissions.has("admin.logs.export"));

function iso(value: string): string {
  return value ? new Date(value).toISOString() : "";
}

function queryParams(cursor = "") {
  const query = new URLSearchParams({ limit: "20" });
  if (filters.created_from) query.set("created_from", iso(filters.created_from));
  if (filters.created_to) query.set("created_to", iso(filters.created_to));
  if (filters.request_id) query.set("request_id", filters.request_id);
  if (filters.result) query.set(logType.value === "tasks" ? "task_status" : "result", filters.result);
  if (filters.keyword) query.set(logType.value === "operations" ? "action" : logType.value === "security" ? "event_type" : "task_type", filters.keyword);
  if (cursor) query.set("cursor", cursor);
  return query;
}

function exportFilters(): JsonMap {
  const result: JsonMap = {};
  if (filters.created_from) result.created_from = iso(filters.created_from);
  if (filters.created_to) result.created_to = iso(filters.created_to);
  if (filters.request_id) result.request_id = filters.request_id;
  if (filters.result) result[logType.value === "tasks" ? "task_status" : "result"] = filters.result;
  if (filters.keyword) result[logType.value === "operations" ? "action" : logType.value === "security" ? "event_type" : "task_type"] = filters.keyword;
  return result;
}

async function load(cursor = "", append = false) {
  loading.value = !append; error.value = "";
  try {
    const result = await api<JsonMap>(`/api/v1/admin/logs/${logType.value}?${queryParams(cursor)}`);
    items.value = append ? [...items.value, ...(result.items || [])] : result.items || [];
    page.value = result.page || {};
  } catch (value) { error.value = errorMessage(value, "日志读取失败"); }
  finally { loading.value = false; }
}

async function switchType(value: LogType) {
  logType.value = value; selected.value = null; await load();
}

async function openDetail(item: JsonMap) {
  busy.value = true; error.value = "";
  try { selected.value = await api<JsonMap>(`/api/v1/admin/logs/${logType.value}/${encodeURIComponent(item.id)}`); }
  catch (value) { error.value = errorMessage(value); }
  finally { busy.value = false; }
}

async function createExport() {
  busy.value = true; error.value = ""; success.value = "";
  try {
    lastExport.value = await api<JsonMap>("/api/v1/admin/log-exports", {
      method: "POST", idempotencyKey: idempotencyKey("admin-log-export"), body: { log_type: logType.value, export_format: "csv", filters: exportFilters() },
    });
    success.value = lastExport.value.status === "downloadable" ? "日志导出已生成，可下载" : "日志导出任务已创建";
  } catch (value) { error.value = errorMessage(value); }
  finally { busy.value = false; }
}

function primaryText(item: JsonMap): string {
  if (logType.value === "operations") return item.action || "管理操作";
  if (logType.value === "security") return item.event_type || "安全事件";
  return taskLabel(item.task_type);
}

onMounted(async () => {
  if (typeOptions.value.length) logType.value = typeOptions.value[0].value;
  await load();
});
</script>

<template><AppShell><PageHeader eyebrow="ADMIN LOGS" title="日志管理" description="管理操作、登录安全和任务运行独立鉴权；列表、详情与导出均不包含私有业务正文或凭据。"><button v-if="canExport" class="button primary small" type="button" data-action="export-logs" :disabled="busy" @click="createExport">导出当前筛选</button><button class="button outline small" @click="load()">刷新</button></PageHeader><AsyncState :loading="loading" :error="error" :success="success" />
  <nav class="admin-tabs" aria-label="日志分类"><button v-for="item in typeOptions" :key="item.value" class="button soft small admin-tab" :class="{ active: logType === item.value }" type="button" data-action="admin-log-type" :data-log-type="item.value" @click="switchType(item.value)">{{ item.label }}</button></nav>
  <form class="card filter-grid" @submit.prevent="load()"><div class="field-group"><label>开始时间</label><input v-model="filters.created_from" class="field" type="datetime-local" /></div><div class="field-group"><label>结束时间</label><input v-model="filters.created_to" class="field" type="datetime-local" /></div><div class="field-group"><label>请求 ID</label><input v-model="filters.request_id" class="field" /></div><div class="field-group"><label>结果/状态</label><input v-model="filters.result" class="field" :placeholder="logType === 'tasks' ? 'failed / succeeded' : 'succeeded / denied'" /></div><div class="field-group"><label>{{ logType === 'operations' ? '动作' : logType === 'security' ? '事件类型' : '任务类型' }}</label><input v-model="filters.keyword" class="field" /></div><button class="button primary small" type="submit">查询日志</button></form>
  <div v-if="lastExport" class="callout success" style="margin-top:18px">导出 {{ lastExport.status }} · {{ lastExport.row_count ?? '等待统计' }} 行 <a v-if="lastExport.status === 'downloadable'" class="button soft small" :href="`/api/v1/admin/log-exports/${lastExport.id}/file`" target="_blank">下载 CSV</a></div>
  <section v-if="selected" class="card pool-detail" style="margin-top:18px"><div class="card-head"><div><div class="eyebrow">LOG DETAIL</div><h3>{{ primaryText(selected) }}</h3><p>{{ selected.id }} · {{ formatDate(selected.created_at) }}</p></div><button class="button link-button small" @click="selected = null">收起</button></div><details class="raw-report" open><summary>允许展示的日志元数据</summary><pre>{{ JSON.stringify(selected, null, 2) }}</pre></details></section>
  <section class="card" style="margin-top:18px"><div class="card-head"><div><h3>{{ currentLabel }}</h3><p>按时间倒序展示脱敏摘要。</p></div><span class="tag neutral">{{ items.length }} 条</span></div><div class="card-body admin-list"><div v-if="!items.length && !loading" class="empty"><div><strong>当前筛选没有日志</strong></div></div><article v-for="item in items" :key="item.id" class="admin-row"><div><strong>{{ primaryText(item) }}</strong><small>{{ item.account || item.account_id || item.operator || '系统' }} · {{ item.request_id || '无请求 ID' }} · {{ formatDate(item.created_at) }}</small></div><div class="item-actions"><span class="tag" :class="statusClass(item.result || item.status)">{{ statusLabel(item.result || item.status) }}</span><button class="button soft small" @click="openDetail(item)">查看详情</button></div></article><button v-if="page.has_more" class="button soft small" @click="load(page.next_cursor, true)">加载更多</button></div></section>
</AppShell></template>
