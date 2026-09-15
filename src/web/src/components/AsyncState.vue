<script setup lang="ts">
defineProps<{ loading?: boolean; error?: string; success?: string }>();
</script>

<template>
  <div v-if="loading" class="callout"><span class="spinner" />正在读取 201 环境数据…</div>
  <div v-else-if="error" class="callout attention">{{ error }}</div>
  <div v-else-if="success" class="callout success">{{ success }}</div>
</template>
