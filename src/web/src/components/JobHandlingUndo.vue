<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from "vue";

import { api, idempotencyKey } from "@/services/api";
import type { JsonMap } from "@/types";
import { errorMessage } from "@/utils/format";

const props = defineProps<{ change: JsonMap | null }>();
const emit = defineEmits<{ undone: [change: JsonMap] }>();
const busy = ref(false); const error = ref("");
let generation = 0; let key = "";
watch(() => props.change, () => { generation++; busy.value = false; error.value = ""; key = idempotencyKey("job-handling-undo"); }, { immediate: true });
onBeforeUnmount(() => { generation++; });
async function undo() {
  if (!props.change?.previous_status || busy.value) return;
  const change = props.change; const current = generation;
  busy.value = true; error.value = "";
  try {
    const result = await api<JsonMap>(`/api/v1/job-pool/items/${encodeURIComponent(change.item_id)}/handling-status`, { method: "PATCH", idempotencyKey: key,
      body: { status: change.previous_status, base_revision: change.handling.revision, confirm_applied: change.previous_status === "applied" } });
    if (current === generation) emit("undone", { ...change, handling: result.handling, previous_status: null });
  } catch (value) { if (current === generation) error.value = errorMessage(value, "撤销失败，请刷新状态后重试"); }
  finally { if (current === generation) busy.value = false; }
}
</script>
<template>
  <div v-if="change?.previous_status" class="handling-undo" role="status"><span>{{ change.job_title || '岗位' }} · 已设为{{ change.handling?.label }}</span><button class="button link-button small" type="button" :disabled="busy" @click="undo">{{ busy ? '撤销中…' : '撤销最近操作' }}</button><p v-if="error" class="error-text" role="alert">{{ error }}</p></div>
</template>
