<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";

import AppShell from "@/components/AppShell.vue";
import AsyncState from "@/components/AsyncState.vue";
import PageHeader from "@/components/PageHeader.vue";
import { api, idempotencyKey, waitForTask } from "@/services/api";
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
const activeTask = ref<JsonMap | null>(null);
const filters = reactive({
  created_from: "",
  created_to: "",
  account_id: "",
  operator_id: "",
  target_type: "",
  target_id: "",
  resource_type: "",
  resource_id: "",
  request_id: "",
  result: "",
  keyword: "",
  retry_count_min: null as number | null,
});

const typeOptions = computed(() => [
  { value: "operations" as LogType, label: "操作日志", permission: "admin.logs.operations.read" },
  { value: "security" as LogType, label: "安全日志", permission: "admin.logs.security.read" },
  { value: "tasks" as LogType, label: "任务日志", permission: "admin.logs.tasks.read" },
].filter((item) => auth.permissions.has(item.permission)));
const currentLabel = computed(() => typeOptions.value.find((item) => item.value === logType.value)?.label || "日志");
const canExport = computed(() => auth.permissions.has("admin.logs.export"));
const resultOptions = computed(() => logType.value === "tasks"
  ? [["", "全部状态"], ["queued", "排队中"], ["running", "执行中"], ["retry_wait", "等待重试"], ["succeeded", "已完成"], ["failed", "失败"], ["cancelled", "已取消"]]
  : [["", "全部结果"], ["succeeded", "成功"], ["denied", "拒绝"], ["failed", "失败"]]);

function iso(value: string): string {
  return value ? new Date(value).toISOString() : "";
}

function filterValues(): JsonMap {
  const result: JsonMap = {};
  if (filters.created_from) result.created_from = iso(filters.created_from);
  if (filters.created_to) result.created_to = iso(filters.created_to);
  if (filters.account_id) result.account_id = filters.account_id.trim();
  if (filters.result) result[logType.value === "tasks" ? "task_status" : "result"] = filters.result;
  if (filters.keyword) result[logType.value === "operations" ? "action" : logType.value === "security" ? "event_type" : "task_type"] = filters.keyword.trim();
  if (logType.value !== "tasks" && filters.request_id) result.request_id = filters.request_id.trim();
  if (logType.value === "operations") {
    if (filters.operator_id) result.operator_id = filters.operator_id.trim();
    if (filters.target_type) result.target_type = filters.target_type.trim();
    if (filters.target_id) result.target_id = filters.target_id.trim();
  }
  if (logType.value === "tasks") {
    if (filters.resource_type) result.resource_type = filters.resource_type.trim();
    if (filters.resource_id) result.resource_id = filters.resource_id.trim();
    if (filters.retry_count_min !== null) result.retry_count_min = filters.retry_count_min;
  }
  return result;
}

function queryParams(cursor = "") {
  const query = new URLSearchParams({ limit: "20" });
  Object.entries(filterValues()).forEach(([key, value]) => query.set(key, String(value)));
  if (cursor) query.set("cursor", cursor);
  return query;
}

async function load(cursor = "", append = false) {
  loading.value = !append;
  error.value = "";
  try {
    const result = await api<JsonMap>(`/api/v1/admin/logs/${logType.value}?${queryParams(cursor)}`);
    items.value = append ? [...items.value, ...(result.items || [])] : result.items || [];
    page.value = result.page || {};
  } catch (value) {
    error.value = errorMessage(value, "日志读取失败");
  } finally {
    loading.value = false;
  }
}

async function switchType(value: LogType) {
  logType.value = value;
  selected.value = null;
  lastExport.value = null;
  filters.result = "";
  filters.keyword = "";
  await load();
}

async function openDetail(item: JsonMap) {
  busy.value = true;
  error.value = "";
  try {
    selected.value = await api<JsonMap>(`/api/v1/admin/logs/${logType.value}/${encodeURIComponent(item.id)}`);
  } catch (value) {
    error.value = errorMessage(value);
  } finally {
    busy.value = false;
  }
}

async function createExport() {
  busy.value = true;
  error.value = "";
  success.value = "";
  try {
    lastExport.value = await api<JsonMap>("/api/v1/admin/log-exports", {
      method: "POST",
      idempotencyKey: idempotencyKey("admin-log-export"),
      body: { log_type: logType.value, export_format: "csv", filters: filterValues() },
    });
    activeTask.value = lastExport.value.task || null;
    if (lastExport.value.task) {
      success.value = "日志导出任务已创建";
      await waitForTask(lastExport.value.task, { onUpdate: (task) => { activeTask.value = task; } });
      lastExport.value = await api<JsonMap>(`/api/v1/admin/log-exports/${encodeURIComponent(lastExport.value.id)}`);
      activeTask.value = null;
    }
    success.value = "日志导出已生成，可下载";
  } catch (value) {
    error.value = errorMessage(value);
  } finally {
    busy.value = false;
  }
}

function primaryText(item: JsonMap): string {
  if (logType.value === "operations") return item.action || "管理操作";
  if (logType.value === "security") return item.event_type || "安全事件";
  return taskLabel(item.task_type);
}

function itemAccount(item: JsonMap): string {
  return item.account?.email_masked || item.account || item.operator || item.target_account || "系统";
}

function itemResult(item: JsonMap): string {
  return item.result || item.outcome || item.status;
}

const detailFields = computed(() => {
  const item = selected.value || {};
  if (logType.value === "operations") return [
    ["操作者", item.operator?.email_masked || "系统"],
    ["操作者 ID", item.operator?.id || "—"],
    ["对象", item.target ? `${item.target.type} · ${item.target.email_masked || ""}` : "—"],
    ["关联 ID", item.target?.id || "—"],
    ["结果", statusLabel(item.result)],
    ["原因", item.reason || "—"],
    ["请求 ID", item.request_id || "—"],
  ];
  if (logType.value === "security") return [
    ["账号", item.account?.email_masked || "匿名"],
    ["账号 ID", item.account?.id || "—"],
    ["客户端", item.client_type || "—"],
    ["结果", statusLabel(item.result)],
    ["原因码", item.reason_code || "—"],
    ["请求 ID", item.request_id || "—"],
  ];
  return [
    ["账号", item.account?.email_masked || "系统"],
    ["账号 ID", item.account?.id || "—"],
    ["状态", statusLabel(item.status)],
    ["结果对象", item.result ? `${item.result.resource_type || "资源"} · ${item.result.resource_id || "—"}` : "—"],
    ["重试次数", item.retry_count ?? 0],
    ["失败码", item.failure_code || "—"],
    ["排队耗时", item.queue_duration_ms === null ? "—" : `${item.queue_duration_ms} ms`],
    ["执行耗时", item.execution_duration_ms === null ? "—" : `${item.execution_duration_ms} ms`],
  ];
});

onMounted(async () => {
  if (typeOptions.value.length) logType.value = typeOptions.value[0].value;
  await load();
});
</script>

<template>
  <AppShell>
    <PageHeader eyebrow="ADMIN LOGS" title="日志管理" description="按账号、对象、结果和关联 ID 查询脱敏日志；详情和导出均不包含业务正文或凭据。"><button v-if="canExport" class="button primary small" type="button" data-action="export-logs" :disabled="busy" @click="createExport">导出当前筛选</button><button class="button outline small" type="button" @click="load()">刷新</button></PageHeader>
    <AsyncState :loading="loading" :error="error" :success="success" />
    <div v-if="activeTask" class="callout opportunity task-inline-state"><span class="spinner" />{{ taskLabel(activeTask.task_type) }}：{{ statusLabel(activeTask.status) }}</div>
    <nav class="admin-tabs" aria-label="日志分类"><button v-for="item in typeOptions" :key="item.value" class="button soft small admin-tab" :class="{ active: logType === item.value }" type="button" data-action="admin-log-type" :data-log-type="item.value" @click="switchType(item.value)">{{ item.label }}</button></nav>

    <form class="card filter-grid log-filter-grid" @submit.prevent="load()">
      <div class="field-group"><label>开始时间</label><input v-model="filters.created_from" class="field" type="datetime-local" /></div>
      <div class="field-group"><label>结束时间</label><input v-model="filters.created_to" class="field" type="datetime-local" /></div>
      <div class="field-group"><label>账号 ID</label><input v-model="filters.account_id" class="field" placeholder="账号公共 ID" /></div>
      <div v-if="logType === 'operations'" class="field-group"><label>操作者 ID</label><input v-model="filters.operator_id" class="field" placeholder="后台操作者公共 ID" /></div>
      <div v-if="logType === 'operations'" class="field-group"><label>对象类型</label><select v-model="filters.target_type" class="select"><option value="">全部对象</option><option value="account">账号</option></select></div>
      <div v-if="logType === 'operations'" class="field-group"><label>关联对象 ID</label><input v-model="filters.target_id" class="field" placeholder="目标账号公共 ID" /></div>
      <div v-if="logType === 'tasks'" class="field-group"><label>资源类型</label><input v-model="filters.resource_type" class="field" placeholder="analysis / rewrite / interview" /></div>
      <div v-if="logType === 'tasks'" class="field-group"><label>关联资源 ID</label><input v-model="filters.resource_id" class="field" placeholder="输入或结果资源公共 ID" /></div>
      <div v-if="logType !== 'tasks'" class="field-group"><label>请求 ID</label><input v-model="filters.request_id" class="field" /></div>
      <div class="field-group"><label>结果／状态</label><select v-model="filters.result" class="select"><option v-for="option in resultOptions" :key="option[0]" :value="option[0]">{{ option[1] }}</option></select></div>
      <div class="field-group"><label>{{ logType === "operations" ? "动作" : logType === "security" ? "事件类型" : "任务类型" }}</label><input v-model="filters.keyword" class="field" /></div>
      <div v-if="logType === 'tasks'" class="field-group"><label>最少重试次数</label><input v-model.number="filters.retry_count_min" class="field" type="number" min="0" placeholder="不限" /></div>
      <button class="button primary small" type="submit">查询日志</button>
    </form>

    <div v-if="lastExport" class="callout" :class="lastExport.status === 'downloadable' ? 'success' : 'opportunity'" style="margin-top:18px">导出 {{ statusLabel(lastExport.status) }} · {{ lastExport.row_count ?? "等待统计" }} 行 <a v-if="lastExport.status === 'downloadable'" class="button soft small" :href="`/api/v1/admin/log-exports/${lastExport.id}/file`" target="_blank">下载 CSV</a></div>

    <section v-if="selected" class="card pool-detail" style="margin-top:18px"><div class="card-head"><div><div class="eyebrow">LOG DETAIL</div><h3>{{ primaryText(selected) }}</h3><p>{{ selected.id }} · {{ formatDate(selected.created_at) }}</p></div><button class="button link-button small" type="button" @click="selected = null">收起</button></div><dl class="log-detail-grid"><template v-for="field in detailFields" :key="field[0]"><dt>{{ field[0] }}</dt><dd>{{ field[1] }}</dd></template></dl><section v-if="logType === 'tasks' && selected.model_calls?.length" class="model-call-list"><h4>模型调用</h4><article v-for="call in selected.model_calls" :key="call.id" class="data-row"><div><strong>{{ call.provider }} · {{ call.model }}</strong><small>{{ call.id }} · {{ call.duration_ms ?? "—" }} ms</small></div><div><span class="tag" :class="statusClass(call.status)">{{ statusLabel(call.status) }}</span><small>{{ call.input_tokens ?? "未知" }} / {{ call.output_tokens ?? "未知" }} tokens · ${{ call.cost_usd_exact ?? "未知" }}</small></div></article></section><details class="raw-report"><summary>查看允许展示的高级元数据</summary><pre>{{ JSON.stringify(selected, null, 2) }}</pre></details></section>

    <section class="card" style="margin-top:18px"><div class="card-head"><div><h3>{{ currentLabel }}</h3><p>按时间倒序展示脱敏摘要。</p></div><span class="tag neutral">{{ items.length }} 条</span></div><div class="card-body admin-list"><div v-if="!items.length && !loading" class="empty"><div><strong>当前筛选没有日志</strong></div></div><article v-for="item in items" :key="item.id" class="admin-row"><div><strong>{{ primaryText(item) }}</strong><small>{{ itemAccount(item) }} · {{ item.resource_id || item.target_account_id || item.request_id || "无关联 ID" }} · {{ formatDate(item.created_at) }}</small></div><div class="item-actions"><span class="tag" :class="statusClass(itemResult(item))">{{ statusLabel(itemResult(item)) }}</span><button class="button soft small" type="button" @click="openDetail(item)">查看详情</button></div></article><button v-if="page.has_more" class="button soft small" type="button" @click="load(page.next_cursor, true)">加载更多</button></div></section>
  </AppShell>
</template>
