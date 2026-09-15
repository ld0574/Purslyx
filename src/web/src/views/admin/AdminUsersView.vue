<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";

import AppShell from "@/components/AppShell.vue";
import AsyncState from "@/components/AsyncState.vue";
import PageHeader from "@/components/PageHeader.vue";
import { api, idempotencyKey } from "@/services/api";
import { useAuthStore } from "@/stores/auth";
import type { JsonMap } from "@/types";
import { errorMessage, formatDate, statusClass, statusLabel } from "@/utils/format";

const auth = useAuthStore();
const loading = ref(true);
const busy = ref(false);
const error = ref("");
const success = ref("");
const users = ref<JsonMap[]>([]);
const page = ref<JsonMap>({});
const selected = ref<JsonMap | null>(null);
const roles = ref<JsonMap[]>([]);
const assignedRoleIds = ref<string[]>([]);
const filters = reactive({ search: "", registration_role: "", status: "" });
const statusForm = reactive({ status: "active", reason: "管理端账号状态复核" });
const roleReason = ref("管理端替换后台角色");

const canManageStatus = computed(() => auth.permissions.has("admin.users.manage_status"));
const canManageRoles = computed(() => auth.permissions.has("admin.roles.manage"));

async function load(cursor = "", append = false) {
  loading.value = !append; error.value = "";
  try {
    const query = new URLSearchParams({ limit: "20" });
    if (filters.search.trim()) query.set("search", filters.search.trim());
    if (filters.registration_role) query.set("registration_role", filters.registration_role);
    if (filters.status) query.set("status", filters.status);
    if (cursor) query.set("cursor", cursor);
    const result = await api<JsonMap>(`/api/v1/admin/users?${query}`);
    users.value = append ? [...users.value, ...(result.items || [])] : result.items || [];
    page.value = result.page || {};
    if (canManageRoles.value && !roles.value.length) roles.value = (await api<JsonMap>("/api/v1/admin/roles")).items || [];
  } catch (value) { error.value = errorMessage(value, "用户列表读取失败"); }
  finally { loading.value = false; }
}

async function openUser(item: JsonMap) {
  busy.value = true; error.value = ""; success.value = "";
  try {
    selected.value = await api<JsonMap>(`/api/v1/admin/users/${encodeURIComponent(item.id)}`);
    statusForm.status = selected.value.account.status;
    assignedRoleIds.value = (selected.value.admin_roles || []).map((role: JsonMap) => role.id);
  } catch (value) { error.value = errorMessage(value); }
  finally { busy.value = false; }
}

async function updateStatus() {
  if (!selected.value) return;
  busy.value = true; error.value = "";
  try {
    const result = await api<JsonMap>(`/api/v1/admin/users/${encodeURIComponent(selected.value.account.id)}/status`, {
      method: "PUT", idempotencyKey: idempotencyKey("admin-user-status"), body: {
        status: statusForm.status,
        reason: statusForm.reason,
        base_revision: selected.value.account.revision,
      },
    });
    selected.value.account = result.account; success.value = "账号状态已更新"; await load();
  } catch (value) { error.value = errorMessage(value); }
  finally { busy.value = false; }
}

async function assignRoles() {
  if (!selected.value) return;
  busy.value = true; error.value = "";
  try {
    const result = await api<JsonMap>(`/api/v1/admin/users/${encodeURIComponent(selected.value.account.id)}/roles`, {
      method: "PUT", idempotencyKey: idempotencyKey("admin-user-roles"), body: {
        role_ids: assignedRoleIds.value,
        base_revision: selected.value.account.revision,
        reason: roleReason.value,
      },
    });
    selected.value.account = result.account; selected.value.admin_roles = result.admin_roles; success.value = "后台角色分配已更新";
  } catch (value) { error.value = errorMessage(value); }
  finally { busy.value = false; }
}

onMounted(load);
</script>

<template><AppShell><PageHeader eyebrow="ADMIN USERS" title="用户管理" description="检索现有账号、查看注册身份与用量；状态和后台角色变更分别鉴权并记录理由。"><button class="button outline small" @click="load()">刷新</button></PageHeader><AsyncState :loading="loading" :error="error" :success="success" />
  <form id="admin-user-search" class="card filter-grid" @submit.prevent="load()"><div class="field-group"><label>邮箱检索</label><input v-model="filters.search" class="field" name="search" placeholder="输入完整或部分邮箱" /></div><div class="field-group"><label>注册身份</label><select v-model="filters.registration_role" class="select"><option value="">全部身份</option><option value="seeker">求职</option><option value="recruiter">招聘</option></select></div><div class="field-group"><label>账号状态</label><select v-model="filters.status" class="select"><option value="">全部状态</option><option value="active">正常</option><option value="suspended">已暂停</option><option value="pending_verification">待验证</option></select></div><button class="button primary small" type="submit">搜索用户</button></form>
  <section v-if="selected" class="card admin-user-detail" style="margin-top:18px"><div class="card-head"><div><div class="eyebrow">USER DETAIL</div><h3>{{ selected.account.email }}</h3><p>{{ selected.account.id }} · 注册身份只读：{{ selected.account.registration_role === 'seeker' ? '求职' : '招聘' }}</p></div><button class="button link-button small" @click="selected = null">收起</button></div><div class="card-body grid-2"><div class="stack"><div class="metric-grid"><article v-for="balance in selected.usage?.balances || []" :key="balance.feature" class="metric-card"><strong>{{ balance.available }}</strong><span>{{ balance.feature }} 可用</span></article></div><form v-if="canManageStatus" class="form-card" @submit.prevent="updateStatus"><h3>账号状态</h3><div class="field-group"><label>状态</label><select v-model="statusForm.status" class="select"><option value="active">正常</option><option value="suspended">暂停</option></select></div><div class="field-group"><label>变更理由</label><input v-model="statusForm.reason" class="field" required /></div><button class="button primary small" :disabled="busy" type="submit">保存状态</button></form></div><form v-if="canManageRoles" class="form-card" @submit.prevent="assignRoles"><h3>后台角色分配</h3><p class="micro">注册身份不会随后台角色改变；不能修改自己的角色。</p><div class="permission-grid"><label v-for="role in roles.filter(item => item.status === 'active')" :key="role.id" class="permission-option"><input v-model="assignedRoleIds" type="checkbox" :value="role.id" /><span><strong>{{ role.name }}</strong><br />{{ role.description || '无说明' }}</span></label></div><div class="field-group"><label>分配理由</label><input v-model="roleReason" class="field" required /></div><button class="button primary small" :disabled="busy || selected.account.id === auth.account?.id" type="submit">保存角色分配</button></form></div></section>
  <section class="card" style="margin-top:18px"><div class="card-head"><div><h3>账号列表</h3><p>邮箱只在已授权的用户管理页面展示。</p></div><span class="tag neutral">{{ users.length }} 条</span></div><div class="card-body admin-list"><div v-if="!users.length && !loading" class="empty"><div><strong>没有匹配账号</strong><p>调整筛选条件后重试。</p></div></div><article v-for="item in users" :key="item.id" class="admin-row"><div><strong>{{ item.email }}</strong><small>{{ item.registration_role === 'seeker' ? '求职' : '招聘' }} · {{ statusLabel(item.status) }} · {{ formatDate(item.created_at) }}</small></div><div class="item-actions"><span class="tag" :class="statusClass(item.status)">{{ statusLabel(item.status) }}</span><button class="button soft small" type="button" data-action="open-admin-user" @click="openUser(item)">查看详情</button></div></article><button v-if="page.has_more" class="button soft small" @click="load(page.next_cursor, true)">加载更多</button></div></section>
</AppShell></template>
