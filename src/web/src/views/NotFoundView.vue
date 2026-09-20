<script setup lang="ts">
import SiteFooter from "@/components/SiteFooter.vue";
</script>

<template>
  <main class="public-main"><div class="empty"><div><strong>页面不存在</strong><p>这个地址没有对应的 Purslyx 页面。</p><RouterLink class="button primary" to="/">返回首页</RouterLink></div></div></main>
  <SiteFooter />
</template>
