<script setup lang="ts">
import { onMounted, reactive, ref } from "vue";

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
const form = reactive({ name: "", description: "", permission_keys: [] as string[], status: "active", reason: "创建受限后台角色" });

async function load() {
  loading.value = true; error.value = "";
  try { const result = await api<JsonMap>("/api/v1/admin/roles"); roles.value = result.items || []; catalog.value = result.permission_catalog || []; }
  catch (value) { error.value = errorMessage(value, "角色权限读取失败"); }
  finally { loading.value = false; }
}

function resetForm() {
  editing.value = null; form.name = ""; form.description = ""; form.permission_keys = []; form.status = "active"; form.reason = "创建受限后台角色";
}

function editRole(item: JsonMap) {
  if (item.is_builtin) return;
  editing.value = item; form.name = item.name; form.description = item.description || ""; form.permission_keys = [...(item.permission_keys || [])]; form.status = item.status; form.reason = "更新后台角色权限";
  window.scrollTo({ top: 0, behavior: "smooth" });
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
    resetForm(); await load();
  } catch (value) { error.value = errorMessage(value); }
  finally { busy.value = false; }
}

onMounted(load);
</script>

<template><AppShell><PageHeader eyebrow="ADMIN ROLES" title="角色权限" description="角色只是后台权限组合；只能授予当前操作者自己拥有的权限，内置超级管理员只读。"><button class="button outline small" @click="load">刷新</button></PageHeader><AsyncState :loading="loading" :error="error" :success="success" />
  <div class="grid-2"><section class="card"><div class="card-head"><div><h3>{{ editing ? '编辑后台角色' : '创建后台角色' }}</h3><p>每次保存都会写入操作者、理由和前后值。</p></div></div><form id="admin-role-form" class="card-body form-card" @submit.prevent="saveRole"><div class="field-group"><label>角色名称</label><input v-model="form.name" class="field" name="name" required /></div><div class="field-group"><label>说明</label><textarea v-model="form.description" class="textarea" name="description" style="min-height:90px" /></div><div class="field-group"><label>权限组合</label><div class="permission-grid"><label v-for="item in catalog" :key="item.key" class="permission-option"><input v-model="form.permission_keys" type="checkbox" name="permission_keys" :value="item.key" /><span><strong>{{ item.display_name }}</strong><br />{{ item.key }}<br />{{ item.description }}</span></label></div></div><div class="form-row"><div class="field-group"><label>状态</label><select v-model="form.status" class="select"><option value="active">启用</option><option value="archived">归档</option></select></div><div class="field-group"><label>变更理由</label><input v-model="form.reason" class="field" required /></div></div><div class="item-actions"><button class="button primary" :disabled="busy || !form.permission_keys.length" type="submit">{{ editing ? '保存角色变更' : '创建角色' }}</button><button v-if="editing" class="button soft" type="button" @click="resetForm">取消编辑</button></div></form></section>
  <section class="card"><div class="card-head"><div><h3>现有角色</h3><p>{{ roles.length }} 个权限组合。</p></div></div><div class="card-body admin-list"><article v-for="item in roles" :key="item.id" class="admin-row"><div><strong>{{ item.name }} <span v-if="item.is_builtin" class="tag brand">内置</span></strong><small>{{ item.description || '无说明' }} · {{ item.member_count }} 名成员 · revision {{ item.revision }}</small><div class="role-chip-list"><span v-for="permission in item.permission_keys" :key="permission" class="role-chip">{{ permission }}</span></div></div><div class="item-actions"><span class="tag" :class="statusClass(item.status)">{{ statusLabel(item.status) }}</span><button v-if="!item.is_builtin" class="button soft small" @click="editRole(item)">编辑</button></div></article></div></section></div>
</AppShell></template>
