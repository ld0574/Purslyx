<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from "vue";

import AdminDialog from "@/components/AdminDialog.vue";
import { api, idempotencyKey } from "@/services/api";
import type { JsonMap } from "@/types";
import { errorMessage } from "@/utils/format";

const props = defineProps<{ open: boolean; item: JsonMap | null }>();
const emit = defineEmits<{ close: []; saved: [change: JsonMap] }>();
const status = ref("pending");
const baseRevision = ref(0);
const confirmed = ref(false);
const busy = ref(false);
const error = ref("");
let generation = 0;
let attempt: { fingerprint: string; key: string } | null = null;
watch([() => props.open, () => props.item?.id], () => {
  generation++;
  status.value = props.item?.handling?.status || "pending";
  baseRevision.value = props.item?.handling?.revision || 0;
  confirmed.value = false; busy.value = false; error.value = ""; attempt = null;
}, { immediate: true });
onBeforeUnmount(() => { generation++; });

async function save() {
  if (!props.open || !props.item || busy.value || (status.value === "applied" && !confirmed.value)) return;
  const current = generation;
  const item = props.item;
  const body = { status: status.value, base_revision: baseRevision.value, confirm_applied: status.value === "applied" && confirmed.value };
  const fingerprint = JSON.stringify([item.id, body]);
  if (attempt?.fingerprint !== fingerprint) attempt = { fingerprint, key: idempotencyKey("job-handling") };
  busy.value = true; error.value = "";
  try {
    const result = await api<JsonMap>(`/api/v1/job-pool/items/${encodeURIComponent(item.id)}/handling-status`, { method: "PATCH", body, idempotencyKey: attempt!.key });
    if (current !== generation) return;
    emit("saved", { item_id: item.id, job_title: item.job_title, handling: result.handling, previous_status: result.previous_status });
    emit("close");
  } catch (value) { if (current === generation) error.value = errorMessage(value, "处理状态保存失败"); }
  finally { if (current === generation) busy.value = false; }
}
</script>

<template>
  <AdminDialog :open="open" title="修改处理状态" :description="item?.job_title || '岗位处理状态与投递建议分类独立'" @close="emit('close')">
    <form class="handling-form" @submit.prevent="save">
      <fieldset><legend>选择处理状态（不消耗次数）</legend>
        <label v-for="option in [{ key: 'pending', label: '待投' }, { key: 'verifying', label: '待核实' }, { key: 'applied', label: '本人确认已投' }, { key: 'dismissed', label: '不感兴趣' }]" :key="option.key" class="handling-choice"><input v-model="status" type="radio" name="handling-status" :value="option.key" :disabled="busy" />{{ option.label }}</label>
      </fieldset>
      <label v-if="status === 'applied'" class="auto-consent auto-consent-confirm handling-confirm"><input v-model="confirmed" type="checkbox" :disabled="busy" />我已在原平台提交</label>
      <p class="micro">去投递点击不等于外部平台提交成功；本操作仅记录你确认的处理状态。</p>
      <p v-if="error" class="error-text" role="alert">{{ error }}</p>
      <div class="item-actions"><button class="button soft" type="button" @click="emit('close')">取消</button><button class="button primary" type="submit" :disabled="busy || (status === 'applied' && !confirmed)">{{ busy ? '保存中…' : '保存状态' }}</button></div>
    </form>
  </AdminDialog>
</template>
