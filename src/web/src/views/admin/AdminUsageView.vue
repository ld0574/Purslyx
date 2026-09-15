<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";

import AppShell from "@/components/AppShell.vue";
import AsyncState from "@/components/AsyncState.vue";
import PageHeader from "@/components/PageHeader.vue";
import { api, idempotencyKey } from "@/services/api";
import { useAuthStore } from "@/stores/auth";
import type { JsonMap } from "@/types";
import { errorMessage, formatDate } from "@/utils/format";

const auth = useAuthStore();
const loading = ref(true);
const busy = ref(false);
const error = ref("");
const success = ref("");
const users = ref<JsonMap[]>([]);
const grants = ref<JsonMap[]>([]);
const page = ref<JsonMap>({});
const form = reactive({ user_id: "", feature: "analysis", count: 1, reason: "内测运营人工追加使用次数" });
const grantFilter = ref("");

const target = computed(() => users.value.find((item) => item.id === form.user_id));
const availableFeatures = computed(() => target.value?.registration_role === "recruiter" ? ["analysis"] : ["analysis", "rewrite", "interview"]);
const featureLabels: Record<string, string> = { analysis: "岗位分析", rewrite: "简历改写", interview: "面试练习" };

async function load(cursor = "", append = false) {
  loading.value = !append; error.value = "";
  try {
    const query = new URLSearchParams({ limit: "20" });
    if (grantFilter.value) query.set("feature", grantFilter.value);
    if (cursor) query.set("cursor", cursor);
    const grantResult = await api<JsonMap>(`/api/v1/admin/usage-grants?${query}`);
    grants.value = append ? [...grants.value, ...(grantResult.items || [])] : grantResult.items || [];
    page.value = grantResult.page || {};
    if (auth.permissions.has("admin.users.read") && !users.value.length) {
      users.value = (await api<JsonMap>("/api/v1/admin/users?limit=100")).items || [];
      form.user_id ||= users.value[0]?.id || "";
    }
  } catch (value) { error.value = errorMessage(value, "次数记录读取失败"); }
  finally { loading.value = false; }
}

function syncFeature() {
  if (!availableFeatures.value.includes(form.feature)) form.feature = availableFeatures.value[0] || "analysis";
}

async function grantUsage() {
  if (!form.user_id) { error.value = "请选择目标账号"; return; }
  busy.value = true; error.value = ""; success.value = "";
  try {
    await api(`/api/v1/admin/users/${encodeURIComponent(form.user_id)}/usage-grants`, {
      method: "POST", idempotencyKey: idempotencyKey("admin-usage-grant"), body: { feature: form.feature, count: form.count, reason: form.reason },
    });
    success.value = `已发放 ${form.count} 次${form.feature}`; await load();
  } catch (value) { error.value = errorMessage(value); }
  finally { busy.value = false; }
}

onMounted(load);
</script>

<template><AppShell><PageHeader eyebrow="ADMIN USAGE" title="次数管理" description="只追加目标注册身份适用的功能次数；不直接覆盖余额，发放、余额和操作日志同事务保存。"><button class="button outline small" @click="load()">刷新</button></PageHeader><AsyncState :loading="loading" :error="error" :success="success" />
  <div class="grid-2"><section class="card"><div class="card-head"><div><h3>追加使用次数</h3><p>数量必须为正且填写原因；重复幂等请求不会重复发放。</p></div></div><form id="admin-grant-form" class="card-body form-card" @submit.prevent="grantUsage"><div class="field-group"><label>目标账号</label><select v-if="users.length" v-model="form.user_id" class="select" name="user_id" required @change="syncFeature"><option v-for="item in users" :key="item.id" :value="item.id">{{ item.email }} · {{ item.registration_role === 'seeker' ? '求职' : '招聘' }}</option></select><input v-else v-model="form.user_id" class="field" name="user_id" placeholder="目标账号 ID" required /></div><div class="form-row"><div class="field-group"><label>功能</label><select v-model="form.feature" class="select" name="feature"><option v-for="item in availableFeatures" :key="item" :value="item">{{ featureLabels[item] }}</option></select></div><div class="field-group"><label>发放数量</label><input v-model.number="form.count" class="field" name="count" type="number" min="1" max="10000" required /></div></div><div class="field-group"><label>发放理由</label><textarea v-model="form.reason" class="textarea" name="reason" minlength="5" required /></div><button class="button primary" :disabled="busy" type="submit">确认追加次数</button></form></section>
  <section class="card"><div class="card-head"><div><h3>发放规则</h3><p>角色与次数是两套独立授权。</p></div></div><div class="card-body stack"><div class="callout"><strong>求职账号</strong><br />可追加岗位分析、简历改写和面试练习。</div><div class="callout"><strong>招聘账号</strong><br />只能追加岗位分析。</div><div class="callout opportunity">所有发放保留操作者、目标、功能、数量、理由和变更前后余额。</div></div></section></div>
  <section class="card" style="margin-top:18px"><div class="card-head"><div><h3>最近发放记录</h3><p>历史记录不可覆盖。</p></div><select v-model="grantFilter" class="select compact-select" @change="load()"><option value="">全部功能</option><option value="analysis">分析</option><option value="rewrite">改写</option><option value="interview">面试</option></select></div><div class="card-body table-scroll"><table class="mini-table"><thead><tr><th>时间</th><th>目标账号</th><th>功能</th><th>理由</th><th>变更</th></tr></thead><tbody><tr v-for="item in grants" :key="item.id"><td>{{ formatDate(item.created_at) }}</td><td>{{ item.account_id }}</td><td>{{ featureLabels[item.feature] || item.feature }}</td><td>{{ item.reason }}</td><td>{{ item.before_available }} → {{ item.after_available }}（+{{ item.count }}）</td></tr></tbody></table><div v-if="!grants.length" class="empty"><div><strong>暂无发放记录</strong></div></div><button v-if="page.has_more" class="button soft small" style="margin-top:12px" @click="load(page.next_cursor, true)">加载更多</button></div></section>
</AppShell></template>
