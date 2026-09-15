<script setup lang="ts">
defineProps<{ loading?: boolean; error?: string; success?: string }>();
</script>

<template>
  <div v-if="loading" class="callout"><span class="spinner" />正在加载…</div>
  <div v-else-if="error" class="callout attention">{{ error }}</div>
  <div v-else-if="success" class="callout success">{{ success }}</div>
</template>
