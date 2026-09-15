<script setup lang="ts">
defineProps<{ eyebrow: string; title: string; description: string }>();
</script>

<template>
  <div class="page-heading"><div><div class="eyebrow">{{ eyebrow }}</div><h1>{{ title }}</h1><p>{{ description }}</p></div><div v-if="$slots.default" class="page-actions"><slot /></div></div>
</template>
