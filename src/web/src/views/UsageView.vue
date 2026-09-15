<script setup lang="ts">
import { onMounted, ref } from "vue";

import AppShell from "@/components/AppShell.vue";
import AsyncState from "@/components/AsyncState.vue";
import PageHeader from "@/components/PageHeader.vue";
import { api } from "@/services/api";
import type { JsonMap } from "@/types";
import { errorMessage, formatDate } from "@/utils/format";

const loading = ref(true);
const error = ref("");
const usage = ref<JsonMap>({ balances: [], entries: [], page: {} });
const feature = ref("");

const featureLabels: Record<string, string> = { analysis: "岗位分析", rewrite: "简历改写", interview: "面试练习" };
const entryLabels: Record<string, string> = { grant: "发放", reserve: "预留", settle: "结算", release: "释放" };

async function load(cursor = "", append = false) {
  loading.value = !append; error.value = "";
  try {
    const query = new URLSearchParams({ entries_limit: "20" });
    if (feature.value) query.set("feature", feature.value);
    if (cursor) query.set("entries_cursor", cursor);
    const result = await api<JsonMap>(`/api/v1/usage?${query}`);
    usage.value = append ? { ...result, entries: [...(usage.value.entries || []), ...(result.entries || [])] } : result;
  } catch (value) { error.value = errorMessage(value, "用量读取失败"); }
  finally { loading.value = false; }
}

onMounted(() => load());
</script>

<template>
  <AppShell>
    <PageHeader eyebrow="USAGE" title="用量" description="每次收费操作都先确认；失败释放预留，幂等重放不会重复扣减。"><select v-model="feature" class="select compact-select" @change="load()"><option value="">全部功能</option><option value="analysis">岗位分析</option><option value="rewrite">简历改写</option><option value="interview">面试练习</option></select><button class="button outline small" type="button" @click="load()">刷新</button></PageHeader>
    <AsyncState :loading="loading" :error="error" />
    <section class="card"><div class="balance-grid"><article v-for="item in usage.balances || []" :key="item.feature" class="balance"><div class="eyebrow">{{ featureLabels[item.feature] || item.feature }}</div><strong>{{ item.available }}</strong><small>可用{{ item.unit === 'sessions' ? '场' : '次' }} · 已发放 {{ item.granted_total }} · 已结算 {{ item.settled_total }} · 预留 {{ item.reserved_total }}</small></article><div v-if="!usage.balances?.length" class="empty"><div><strong>暂无可用功能</strong><p>注册身份决定可使用的功能类型。</p></div></div></div></section>
    <section class="card" style="margin-top:18px"><div class="card-head"><div><h3>最近流水</h3><p>流水用于审计计次，不包含简历、JD、面试回答或令牌正文。</p></div></div><div class="card-body"><div v-if="!usage.entries?.length" class="empty"><div><strong>还没有流水</strong><p>试用发放和后续操作会记录在这里。</p></div></div><article v-for="item in usage.entries || []" :key="item.id" class="usage-entry"><span>{{ formatDate(item.created_at) }}</span><div>{{ featureLabels[item.feature] || item.feature }} · {{ entryLabels[item.entry_type] || item.entry_type }}<br /><span>{{ item.reason || item.source_type || '系统记录' }}</span></div><strong :class="item.count > 0 ? 'positive' : ''">{{ item.count > 0 ? `+${item.count}` : item.count }}</strong></article><button v-if="usage.page?.has_more" class="button soft small" style="margin-top:14px" type="button" @click="load(usage.page.next_cursor, true)">加载更多</button></div></section>
  </AppShell>
</template>
