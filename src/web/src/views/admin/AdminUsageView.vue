<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";

import AdminDialog from "@/components/AdminDialog.vue";
import AppShell from "@/components/AppShell.vue";
import AsyncState from "@/components/AsyncState.vue";
import PageHeader from "@/components/PageHeader.vue";
import { api, idempotencyKey } from "@/services/api";
import type { JsonMap } from "@/types";
import { errorMessage, formatDate } from "@/utils/format";

const loading = ref(true);
const busy = ref(false);
const error = ref("");
const success = ref("");
const grants = ref<JsonMap[]>([]);
const page = ref<JsonMap>({});
const grantOpen = ref(false);
const pickerOpen = ref(false);
const pickerBusy = ref(false);
const pickerError = ref("");
const pickerItems = ref<JsonMap[]>([]);
const pickerPage = ref<JsonMap>({});
let pickerRequest = 0;
const pickerMode = ref<"grant" | "filter">("grant");
const pickerFilters = reactive({ search: "", registration_role: "" });
const selectedTarget = ref<JsonMap | null>(null);
const filterTarget = ref<JsonMap | null>(null);
const filters = reactive({ feature: "", created_from: "", created_to: "" });
const form = reactive({ user_id: "", feature: "analysis", count: 1, reason: "内测运营人工追加使用次数" });

const availableFeatures = computed(() => selectedTarget.value?.registration_role === "recruiter" ? ["analysis"] : ["analysis", "rewrite", "interview"]);
const featureLabels: Record<string, string> = { analysis: "岗位分析", rewrite: "简历改写", interview: "面试练习" };

function iso(value: string) { return value ? new Date(value).toISOString() : ""; }

async function load(cursor = "", append = false) {
  loading.value = !append; error.value = "";
  try {
    const query = new URLSearchParams({ limit: "20" });
    if (filters.feature) query.set("feature", filters.feature);
    if (filterTarget.value) query.set("account_id", filterTarget.value.id);
    if (filters.created_from) query.set("created_from", iso(filters.created_from));
    if (filters.created_to) query.set("created_to", iso(filters.created_to));
    if (cursor) query.set("cursor", cursor);
    const result = await api<JsonMap>(`/api/v1/admin/usage-grants?${query}`);
    grants.value = append ? [...grants.value, ...(result.items || [])] : result.items || [];
    page.value = result.page || {};
  } catch (value) { error.value = errorMessage(value, "次数记录读取失败"); }
  finally { loading.value = false; }
}

function openGrant() {
  error.value = ""; success.value = "";
  form.user_id = ""; form.feature = "analysis"; form.count = 1; form.reason = "内测运营人工追加使用次数";
  selectedTarget.value = null;
  grantOpen.value = true;
}

function openPicker(mode: "grant" | "filter") {
  pickerMode.value = mode;
  pickerFilters.search = ""; pickerFilters.registration_role = "";
  pickerItems.value = []; pickerPage.value = {}; pickerError.value = "";
  pickerOpen.value = true;
  void loadTargets();
}

async function loadTargets(cursor = "", append = false) {
  const request = ++pickerRequest;
  pickerBusy.value = true; pickerError.value = "";
  try {
    const query = new URLSearchParams({ limit: "20" });
    if (pickerFilters.search.trim()) query.set("search", pickerFilters.search.trim());
    if (pickerFilters.registration_role) query.set("registration_role", pickerFilters.registration_role);
    if (cursor) query.set("cursor", cursor);
    const result = await api<JsonMap>(`/api/v1/admin/usage-targets?${query}`);
    if (request !== pickerRequest) return;
    pickerItems.value = append ? [...pickerItems.value, ...(result.items || [])] : result.items || [];
    pickerPage.value = result.page || {};
  } catch (value) { if (request === pickerRequest) pickerError.value = errorMessage(value, "账号搜索失败"); }
  finally { if (request === pickerRequest) pickerBusy.value = false; }
}

function chooseTarget(item: JsonMap) {
  if (pickerMode.value === "grant") {
    selectedTarget.value = item;
    form.user_id = item.id;
    if (!availableFeatures.value.includes(form.feature)) form.feature = "analysis";
  } else {
    filterTarget.value = item;
    void load();
  }
  pickerOpen.value = false;
}

function clearFilters() {
  filters.feature = ""; filters.created_from = ""; filters.created_to = ""; filterTarget.value = null;
  void load();
}

async function grantUsage() {
  if (!form.user_id) { error.value = "请选择目标账号"; return; }
  busy.value = true; error.value = ""; success.value = "";
  try {
    await api(`/api/v1/admin/users/${encodeURIComponent(form.user_id)}/usage-grants`, {
      method: "POST", idempotencyKey: idempotencyKey("admin-usage-grant"), body: { feature: form.feature, count: form.count, reason: form.reason },
    });
    success.value = `已发放 ${form.count} ${form.feature === "interview" ? "场" : "次"}${featureLabels[form.feature]}`;
    grantOpen.value = false;
    await load();
  } catch (value) { error.value = errorMessage(value); }
  finally { busy.value = false; }
}

onMounted(load);
</script>

<template>
  <AppShell>
    <PageHeader eyebrow="ADMIN USAGE" title="次数管理" description="按账号、功能和时间查看发放记录；追加次数需要填写理由。"><button class="button primary small" type="button" data-action="open-admin-grant" @click="openGrant">追加次数</button><button class="button outline small" type="button" @click="load()">刷新</button></PageHeader>
    <AsyncState :loading="loading" :error="error" :success="success" />
    <form class="card filter-grid admin-filter-card" @submit.prevent="load()"><div class="field-group"><label for="grant-feature-filter">功能</label><select id="grant-feature-filter" v-model="filters.feature" class="select"><option value="">全部功能</option><option value="analysis">岗位分析</option><option value="rewrite">简历改写</option><option value="interview">面试练习</option></select></div><div class="field-group"><label>目标账号</label><button class="button soft admin-account-button" type="button" @click="openPicker('filter')">{{ filterTarget ? filterTarget.email : '筛选目标账号' }}</button></div><div class="field-group"><label for="grant-from">发放时间起</label><input id="grant-from" v-model="filters.created_from" class="field" type="datetime-local" /></div><div class="field-group"><label for="grant-to">发放时间止</label><input id="grant-to" v-model="filters.created_to" class="field" type="datetime-local" /></div><div class="item-actions filter-actions"><button class="button primary small" type="submit">应用筛选</button><button class="button link-button small" type="button" @click="clearFilters">清空</button></div></form>
    <section class="card admin-list-card"><div class="card-head"><div><h3>发放记录</h3><p>历史记录不可覆盖，显示变更前后余额。</p></div><span class="tag neutral">已加载 {{ grants.length }} 条</span></div><div class="card-body table-scroll"><table v-if="grants.length" class="mini-table"><thead><tr><th>时间</th><th>目标账号</th><th>功能</th><th>理由</th><th>变更</th></tr></thead><tbody><tr v-for="item in grants" :key="item.id"><td>{{ formatDate(item.created_at) }}</td><td>{{ item.account_id }}</td><td>{{ featureLabels[item.feature] || item.feature }}</td><td>{{ item.reason }}</td><td>{{ item.before_available }} → {{ item.after_available }}（+{{ item.count }}）</td></tr></tbody></table><div v-else-if="!loading" class="empty"><div><strong>暂无发放记录</strong><p>调整筛选条件或新增一次发放。</p></div></div><button v-if="page.has_more" class="button soft small" style="margin-top:12px" type="button" @click="load(page.next_cursor, true)">加载更多</button></div></section>

    <AdminDialog :open="grantOpen" title="追加使用次数" description="发放会记录操作者、目标、数量和变更理由。" @close="grantOpen = false"><form id="admin-grant-form" class="form-card admin-modal-form" @submit.prevent="grantUsage"><div v-if="error" class="callout attention">{{ error }}</div><div class="field-group"><label>目标账号</label><button class="button soft admin-account-button" type="button" data-action="choose-grant-target" @click="openPicker('grant')">{{ selectedTarget ? `${selectedTarget.email} · ${selectedTarget.registration_role === 'seeker' ? '求职' : '招聘'}` : '点击筛选并选择账号' }}</button></div><div class="field-group"><label for="grant-feature">功能</label><select id="grant-feature" v-model="form.feature" class="select" name="feature"><option v-for="item in availableFeatures" :key="item" :value="item">{{ featureLabels[item] }}</option></select></div><div class="field-group"><label for="grant-count">发放数量</label><input id="grant-count" v-model.number="form.count" class="field" name="count" type="number" min="1" max="10000" required /></div><div class="field-group"><label for="grant-reason">发放理由</label><textarea id="grant-reason" v-model="form.reason" class="textarea" name="reason" minlength="5" required /></div><div class="item-actions admin-dialog-actions"><button class="button soft" type="button" @click="grantOpen = false">取消</button><button class="button primary" :disabled="busy || !form.user_id" type="submit">确认追加次数</button></div></form></AdminDialog>

    <AdminDialog :open="pickerOpen" title="选择目标账号" description="输入邮箱关键词筛选当前可用账号。" @close="pickerOpen = false"><form class="admin-picker-filters" @submit.prevent="loadTargets()"><div class="field-group"><label for="target-search">邮箱关键词</label><input id="target-search" v-model="pickerFilters.search" class="field" placeholder="输入完整或部分邮箱" /></div><div class="field-group"><label for="target-role">注册身份</label><select id="target-role" v-model="pickerFilters.registration_role" class="select"><option value="">全部身份</option><option value="seeker">求职</option><option value="recruiter">招聘</option></select></div><button class="button primary small" type="submit">搜索</button></form><div v-if="pickerError" class="callout attention">{{ pickerError }}</div><div v-if="pickerBusy && !pickerItems.length" class="callout">正在搜索账号…</div><div v-else-if="!pickerItems.length" class="empty"><div><strong>没有匹配的账号</strong><p>请修改邮箱或身份筛选条件。</p></div></div><div v-else class="admin-picker-list"><button v-for="item in pickerItems" :key="item.id" class="admin-picker-row" type="button" @click="chooseTarget(item)"><span><strong>{{ item.email }}</strong><small>{{ item.registration_role === 'seeker' ? '求职账号' : '招聘账号' }}</small></span><b>选择 →</b></button></div><button v-if="pickerPage.has_more" class="button soft small" type="button" :disabled="pickerBusy" @click="loadTargets(pickerPage.next_cursor, true)">加载更多</button></AdminDialog>
  </AppShell>
</template>
