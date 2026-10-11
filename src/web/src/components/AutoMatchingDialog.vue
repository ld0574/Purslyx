<script setup lang="ts">
import { computed, ref, watch } from "vue";

import AdminDialog from "@/components/AdminDialog.vue";
import AsyncState from "@/components/AsyncState.vue";
import { api, idempotencyKey } from "@/services/api";
import type { JsonMap } from "@/types";
import { errorMessage } from "@/utils/format";

const props = defineProps<{ open: boolean }>();
const emit = defineEmits<{ close: []; saved: [] }>();
const loading = ref(false);
const loaded = ref(false);
const busy = ref(false);
const error = ref("");
const enabled = ref(true);
const resumeId = ref("");
const preferenceIds = ref<string[]>([]);
const dailyLimit = ref(10);
const confirmed = ref(false);
const config = ref<JsonMap>({ revision: 0 });
const resumes = ref<JsonMap[]>([]);
const preferences = ref<JsonMap[]>([]);
const filter = ref("");
let generation = 0;
let lastAttempt: { body: string; key: string } | undefined;
const choices = computed(() => resumes.value.filter(item => String(item.title).includes(filter.value.trim())));
const ready = computed(() => loaded.value && !loading.value && !busy.value && (!enabled.value || Boolean(resumeId.value && preferenceIds.value.length && preferenceIds.value.length <= 10 && confirmed.value)));

async function load() {
  const current = ++generation;
  loading.value = true; loaded.value = false; error.value = ""; confirmed.value = false; filter.value = ""; lastAttempt = undefined;
  try {
    const [policy, documents, profiles] = await Promise.all([
      api<JsonMap>("/api/v1/job-pool/automation"),
      api<JsonMap>("/api/v1/documents?document_type=resume&limit=100"),
      api<JsonMap>("/api/v1/preferences?limit=100"),
    ]);
    if (current !== generation || !props.open) return;
    config.value = policy;
    resumes.value = (documents.items || []).filter((item: JsonMap) => item.latest_version && item.subject_type === "self_resume");
    preferences.value = (profiles.items || []).filter((item: JsonMap) => item.version && item.context === "self");
    resumeId.value = policy.resume_document_id || (resumes.value.length === 1 ? resumes.value[0].id : "");
    preferenceIds.value = (policy.preference_ids || []).filter((id: string) => preferences.value.some(item => item.id === id));
    if (!preferenceIds.value.length && preferences.value.length === 1) preferenceIds.value = [preferences.value[0].id];
    dailyLimit.value = policy.daily_limit || 10;
    enabled.value = policy.confirmed_at ? Boolean(policy.enabled) : true;
    loaded.value = true;
  } catch (value) {
    if (current === generation) error.value = errorMessage(value, "自动处理设置读取失败");
  } finally { if (current === generation) loading.value = false; }
}

async function save() {
  if (!ready.value) return;
  busy.value = true; error.value = "";
  const body = { enabled: enabled.value, resume_document_id: resumeId.value || null, preference_ids: preferenceIds.value, daily_limit: Number(dailyLimit.value), base_revision: config.value.revision || 0, confirm_usage: enabled.value && confirmed.value };
  const serialized = JSON.stringify(body);
  if (lastAttempt?.body !== serialized) lastAttempt = { body: serialized, key: idempotencyKey("auto-matching") };
  try {
    await api("/api/v1/job-pool/automation", { method: "PUT", body, idempotencyKey: lastAttempt!.key });
    emit("saved"); emit("close");
  } catch (value) { error.value = errorMessage(value, "自动处理设置保存失败"); }
  finally { busy.value = false; }
}
function close() { if (!busy.value) emit("close"); }
watch(() => props.open, open => { if (open) void load(); else generation++; }, { immediate: true });
</script>

<template>
  <AdminDialog :open="open" title="自动解析与匹配" description="设置一次，新抓取岗位逐条处理；不自动投递。" @close="close">
    <AsyncState :loading="loading" :error="error" />
    <form v-if="!loading" class="auto-matching-form" @submit.prevent="save">
      <label class="auto-consent"><input v-model="enabled" type="checkbox" :disabled="busy" />自动处理新抓取岗位</label>
      <p class="micro">仅处理开启后的新抓取，以及此前已授权但尚未处理的岗位。已入池的历史岗位不会被批量扣次。</p>
      <fieldset :disabled="busy"><legend>默认简历</legend>
        <input v-model="filter" class="input" aria-label="筛选简历" placeholder="筛选简历名称" />
        <div class="auto-choice-list"><label v-for="item in choices" :key="item.id" class="resume-choice" :class="{ selected: resumeId === item.id }"><input v-model="resumeId" type="radio" name="automatic-resume" :value="item.id" /><span><strong>{{ item.title }}</strong><small>当前已确认版本 v{{ item.latest_version.version_no }}</small></span></label></div>
        <p v-if="!resumes.length" class="empty-inline">还没有已确认简历。<RouterLink to="/app/seeker/resume" @click="close">添加并确认简历 →</RouterLink></p>
      </fieldset>
      <fieldset :disabled="busy"><legend>参与匹配的岗位期望</legend>
        <div class="auto-choice-list"><label v-for="item in preferences" :key="item.id" class="resume-choice" :class="{ selected: preferenceIds.includes(item.id) }"><input v-model="preferenceIds" type="checkbox" :value="item.id" /><span><strong>{{ item.display_name }}</strong><small>多套期望按整体最适合的一套判断，不拼接条件</small></span></label></div>
        <p v-if="!preferences.length" class="empty-inline">还没有有效岗位期望。<RouterLink to="/app/seeker/resume" @click="close">添加岗位期望 →</RouterLink></p>
      </fieldset>
      <label class="field-group"><span>每日自动处理上限（上海自然日）</span><input v-model.number="dailyLimit" class="input" type="number" min="1" max="50" required :disabled="busy" /></label>
      <p class="micro">新岗位使用所选资料的最新已确认版本；待处理岗位保留原版本。余额不足、队列繁忙或达到每日上限时保留，条件恢复后自动继续。失败不会自动反复调用。</p>
      <label v-if="enabled" class="auto-consent auto-consent-confirm"><input v-model="confirmed" type="checkbox" :disabled="busy" />我确认自动匹配每个新岗位预留 1 次分析额度，成功才计次；失败释放额度。</label>
      <div class="item-actions"><button class="button outline" type="button" :disabled="busy" @click="load">重新读取</button><button class="button primary" type="submit" :disabled="!ready">{{ busy ? "保存中…" : enabled ? "确认并开启自动处理" : "保存并暂停自动处理" }}</button></div>
    </form>
  </AdminDialog>
</template>
