<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";

import AdminDialog from "@/components/AdminDialog.vue";
import AppShell from "@/components/AppShell.vue";
import AsyncState from "@/components/AsyncState.vue";
import PageHeader from "@/components/PageHeader.vue";
import { api, idempotencyKey } from "@/services/api";
import type { JsonMap } from "@/types";
import { errorMessage, statusClass, statusLabel } from "@/utils/format";

const loading = ref(true);
const busy = ref(false);
const error = ref("");
const success = ref("");
const roles = ref<JsonMap[]>([]);
const catalog = ref<JsonMap[]>([]);
const editing = ref<JsonMap | null>(null);
const editorOpen = ref(false);
const filters = reactive({ search: "", status: "", permission: "" });
const form = reactive({ name: "", description: "", permission_keys: [] as string[], status: "active", reason: "创建受限后台角色" });
const permissionNames = computed<Record<string, string>>(() => Object.fromEntries(catalog.value.map((item) => [item.key, item.display_name || item.key])));
const visibleRoles = computed(() => roles.value.filter((item) => {
  const search = filters.search.trim().toLocaleLowerCase();
  return (!search || `${item.name} ${item.description || ""}`.toLocaleLowerCase().includes(search))
    && (!filters.status || item.status === filters.status)
    && (!filters.permission || (item.permission_keys || []).includes(filters.permission));
}));

async function load() {
  loading.value = true; error.value = "";
  try { const result = await api<JsonMap>("/api/v1/admin/roles"); roles.value = result.items || []; catalog.value = result.permission_catalog || []; }
  catch (value) { error.value = errorMessage(value, "角色权限读取失败"); }
  finally { loading.value = false; }
}

function resetForm() {
  editing.value = null; form.name = ""; form.description = ""; form.permission_keys = []; form.status = "active"; form.reason = "创建受限后台角色";
}

function openCreate() {
  resetForm(); error.value = ""; success.value = ""; editorOpen.value = true;
}

function editRole(item: JsonMap) {
  if (item.is_builtin) return;
  editing.value = item; form.name = item.name; form.description = item.description || ""; form.permission_keys = [...(item.permission_keys || [])]; form.status = item.status; form.reason = "更新后台角色权限";
  error.value = ""; success.value = ""; editorOpen.value = true;
}

function closeEditor() {
  editorOpen.value = false;
  resetForm();
}

async function saveRole() {
  busy.value = true; error.value = ""; success.value = "";
  try {
    const body = { name: form.name, description: form.description, permission_keys: form.permission_keys, status: form.status, reason: form.reason, ...(editing.value ? { base_revision: editing.value.revision } : {}) };
    if (editing.value) {
      await api(`/api/v1/admin/roles/${encodeURIComponent(editing.value.id)}`, { method: "PUT", idempotencyKey: idempotencyKey("admin-role-update"), body });
      success.value = "后台角色已更新";
    } else {
      await api("/api/v1/admin/roles", { method: "POST", idempotencyKey: idempotencyKey("admin-role-create"), body });
      success.value = "后台角色已创建";
    }
    closeEditor(); await load();
  } catch (value) { error.value = errorMessage(value); }
  finally { busy.value = false; }
}

onMounted(load);
</script>

<template><AppShell><PageHeader eyebrow="ADMIN ROLES" title="角色权限" description="按权限查看后台角色；内置超级管理员只读。"><button class="button primary small" type="button" data-action="open-admin-role" @click="openCreate">新增角色</button><button class="button outline small" type="button" @click="load">刷新</button></PageHeader><AsyncState :loading="loading" :error="error" :success="success" />
  <form class="card filter-grid admin-filter-card" @submit.prevent><div class="field-group"><label for="role-search">角色名称或说明</label><input id="role-search" v-model="filters.search" class="field" placeholder="搜索角色" /></div><div class="field-group"><label for="role-status">角色状态</label><select id="role-status" v-model="filters.status" class="select"><option value="">全部状态</option><option value="active">启用</option><option value="archived">归档</option></select></div><div class="field-group"><label for="role-permission">包含权限</label><select id="role-permission" v-model="filters.permission" class="select"><option value="">全部权限</option><option v-for="item in catalog" :key="item.key" :value="item.key">{{ item.display_name }}</option></select></div></form>
  <section class="card admin-list-card"><div class="card-head"><div><h3>角色与权限列表</h3><p>共 {{ visibleRoles.length }} 个角色，权限名称在列表中直接展示。</p></div></div><div class="card-body admin-list"><div v-if="!visibleRoles.length && !loading" class="empty"><div><strong>没有匹配角色</strong><p>调整筛选条件后重试。</p></div></div><article v-for="item in visibleRoles" :key="item.id" class="admin-row"><div><strong>{{ item.name }} <span v-if="item.is_builtin" class="tag brand">内置</span></strong><small>{{ item.description || '无说明' }} · {{ item.member_count }} 名成员</small><div class="role-chip-list"><span v-for="permission in item.permission_keys" :key="permission" class="role-chip" :title="permission">{{ permissionNames[permission] || permission }}</span></div></div><div class="item-actions"><span class="tag" :class="statusClass(item.status)">{{ statusLabel(item.status) }}</span><button v-if="!item.is_builtin" class="button soft small" type="button" @click="editRole(item)">编辑</button></div></article></div></section>
  <AdminDialog :open="editorOpen" :title="editing ? '编辑后台角色' : '新增后台角色'" description="每次保存都会记录操作人、变更理由和前后值。" wide @close="closeEditor"><form id="admin-role-form" class="form-card admin-modal-form" @submit.prevent="saveRole"><div v-if="error" class="callout attention">{{ error }}</div><div class="field-group"><label for="role-name">角色名称</label><input id="role-name" v-model="form.name" class="field" name="name" required /></div><div class="field-group"><label for="role-description">说明</label><textarea id="role-description" v-model="form.description" class="textarea" name="description" style="min-height:90px" /></div><div class="field-group"><label>权限组合</label><div class="admin-permission-list"><label v-for="item in catalog" :key="item.key" class="permission-option"><input v-model="form.permission_keys" type="checkbox" name="permission_keys" :value="item.key" /><span><strong>{{ item.display_name }}</strong><br />{{ item.description }}</span></label></div></div><div class="field-group"><label for="role-edit-status">状态</label><select id="role-edit-status" v-model="form.status" class="select"><option value="active">启用</option><option value="archived">归档</option></select></div><div class="field-group"><label for="role-reason">变更理由</label><input id="role-reason" v-model="form.reason" class="field" required /></div><div class="item-actions admin-dialog-actions"><button class="button soft" type="button" @click="closeEditor">取消</button><button class="button primary" :disabled="busy || !form.permission_keys.length" type="submit">{{ editing ? '保存角色变更' : '创建角色' }}</button></div></form></AdminDialog>
</AppShell></template>
