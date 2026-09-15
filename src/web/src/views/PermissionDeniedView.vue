<script setup lang="ts">
import AppShell from "@/components/AppShell.vue";
import PageHeader from "@/components/PageHeader.vue";
import { useAuthStore } from "@/stores/auth";

const auth = useAuthStore();
</script>

<template><AppShell><PageHeader eyebrow="PERMISSION REQUIRED" title="没有访问权限" description="当前账号没有这个管理模块所需的独立后台权限。服务端也会对每个管理接口重新鉴权。" /><section class="card"><div class="empty"><div><strong>没有访问权限</strong><p>请联系超级管理员授予对应角色；注册身份不会因为后台角色而改变。</p><RouterLink class="button primary" :to="`/app/${auth.role}/dashboard`">返回工作台</RouterLink></div></div></section></AppShell></template>
