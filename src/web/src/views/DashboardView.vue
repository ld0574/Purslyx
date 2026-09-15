<script setup lang="ts">
import { computed, onMounted, ref } from "vue";

import AppShell from "@/components/AppShell.vue";
import AsyncState from "@/components/AsyncState.vue";
import PageHeader from "@/components/PageHeader.vue";
import { api } from "@/services/api";
import { useAuthStore } from "@/stores/auth";
import type { JsonMap } from "@/types";

const auth = useAuthStore();
const loading = ref(true);
const error = ref("");
const documents = ref<JsonMap[]>([]);
const analyses = ref<JsonMap[]>([]);
const tasks = ref<JsonMap[]>([]);
const usage = ref<JsonMap>({ balances: [] });
const role = computed(() => auth.role || "seeker");

async function load() {
  loading.value = true; error.value = "";
  try {
    const [documentResult, analysisResult, taskResult, usageResult] = await Promise.all([
      api<JsonMap>("/api/v1/documents"), api<JsonMap>("/api/v1/analyses"), api<JsonMap>("/api/v1/tasks"), api<JsonMap>("/api/v1/usage"),
    ]);
    documents.value = documentResult.items || [];
    analyses.value = analysisResult.items || [];
    tasks.value = taskResult.items || [];
    usage.value = usageResult;
  } catch (value) { error.value = value instanceof Error ? value.message : "工作台读取失败"; }
  finally { loading.value = false; }
}
onMounted(load);
</script>

<template><AppShell><PageHeader eyebrow="WORKBENCH" title="工作台" :description="role === 'seeker' ? '继续下一步：从资料、岗位、报告、改写、岗位版到面试都能恢复。' : '最近分析、候选人资料、任务和用量集中在这里。'"><button class="button outline small" @click="load">刷新</button></PageHeader><AsyncState :loading="loading" :error="error" />
  <template v-if="!loading"><section class="metric-grid"><article class="metric-card"><strong>{{ documents.length }}</strong><span>已保存资料</span></article><article class="metric-card"><strong>{{ analyses.length }}</strong><span>最近分析</span></article><article class="metric-card"><strong>{{ tasks.filter(item => ['queued','running','retry_wait'].includes(item.status)).length }}</strong><span>未完成任务</span></article><article v-for="balance in usage.balances || []" :key="balance.feature" class="metric-card"><strong>{{ balance.available }}</strong><span>{{ balance.feature }} 剩余</span></article></section>
  <section class="grid-2" style="margin-top:18px"><article class="card"><div class="card-head"><div><h3>继续下一步</h3><p>从本身份的核心入口进入。</p></div></div><div class="card-body item-actions"><RouterLink class="button primary" :to="role === 'seeker' ? '/app/seeker/resume' : '/app/recruiter/materials'">管理资料</RouterLink><RouterLink v-if="role === 'seeker'" class="button soft" to="/app/seeker/pool">打开匹配池</RouterLink><RouterLink class="button soft" :to="`/app/${role}/tasks`">查看任务</RouterLink></div></article><article class="card"><div class="card-head"><div><h3>最近分析</h3><p>报告绑定输入版本和规则版本。</p></div></div><div class="card-body data-list"><div v-if="!analyses.length" class="empty"><div><strong>还没有报告</strong><p>确认资料后开始第一份分析。</p></div></div><div v-for="item in analyses.slice(0,5)" :key="item.id" class="data-row"><div><strong>{{ item.job_category || '岗位分析' }}</strong><small>{{ item.status }} · {{ item.ability_score ?? '—' }} 分</small></div><RouterLink class="button soft small" :to="{ path: `/app/${role}/report`, query: { analysis_id: item.id } }">打开</RouterLink></div></div></article></section></template>
</AppShell></template>
