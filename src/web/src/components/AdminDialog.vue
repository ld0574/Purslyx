<script setup lang="ts">
import { nextTick, onMounted, ref, watch } from "vue";

const props = withDefaults(defineProps<{ open: boolean; title: string; description?: string; wide?: boolean }>(), {
  description: "",
  wide: false,
});
const emit = defineEmits<{ close: [] }>();
const dialog = ref<HTMLDialogElement | null>(null);

async function syncOpen(open: boolean) {
  await nextTick();
  if (!dialog.value) return;
  if (open && !dialog.value.open) {
    if (typeof dialog.value.showModal === "function") dialog.value.showModal();
    else dialog.value.setAttribute("open", "");
  } else if (!open && dialog.value.open) {
    if (typeof dialog.value.close === "function") dialog.value.close();
    else dialog.value.removeAttribute("open");
  }
}

watch(() => props.open, syncOpen);
onMounted(() => { void syncOpen(props.open); });
</script>

<template>
  <dialog ref="dialog" class="admin-dialog" :class="{ 'admin-dialog-wide': wide }" :aria-label="title" @cancel.prevent="emit('close')" @close="emit('close')">
    <div class="admin-dialog-head"><div><h3>{{ title }}</h3><p v-if="description">{{ description }}</p></div><button class="button link-button small" type="button" aria-label="关闭弹窗" @click="emit('close')">关闭</button></div>
    <div class="admin-dialog-body"><slot /></div>
  </dialog>
</template>
