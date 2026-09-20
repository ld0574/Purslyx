<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useRoute } from "vue-router";

import { api } from "@/services/api";
import { useAuthStore } from "@/stores/auth";
import type { JsonMap } from "@/types";

const route = useRoute();
const auth = useAuthStore();
const syncRequested = computed(() => String(route.query.purslyx_browser_sync || "") === "1");
const syncLoading = ref(false);
const syncError = ref("");
const syncSuccess = ref("");

async function syncBrowserSession() {
  if (!syncRequested.value) return;
  syncLoading.value = true;
  syncError.value = "";
  syncSuccess.value = "";
  try {
    if (!auth.account) {
      syncError.value = "请先登录 Purslyx，再回到此窗口完成同步。";
      return;
    }
    const origin = String(route.query.origin || "");
    const nonce = String(route.query.nonce || "");
    const target = new URL(origin);
    if (!origin || !nonce || !["http:", "https:"].includes(target.protocol)) {
      throw new Error("同步参数无效，请从岗位页面重新点击“同步 Purslyx”。");
    }
    if (!window.opener || window.opener.closed) {
      throw new Error("同步窗口已失去来源，请关闭后从岗位页面重新点击同步。");
    }
    const result = await api<JsonMap>("/api/v1/auth/browser-codes", {
      method: "POST",
      body: { origin, nonce },
    });
    const authorizationCode = result.authorization_code || result.code;
    if (!authorizationCode) throw new Error("服务没有返回有效授权码，请稍后重试。");
    window.opener.postMessage(
      { type: "PURSLYX_BROWSER_CODE", authorization_code: authorizationCode, origin, nonce },
      origin,
    );
    syncSuccess.value = "登录同步完成，可以关闭此窗口并回到岗位页面。";
  } catch (value) {
    syncError.value = value instanceof Error ? value.message : "登录同步失败，请稍后重试。";
  } finally {
    syncLoading.value = false;
  }
}

onMounted(() => {
  if (syncRequested.value) void syncBrowserSession();
});
</script>

<template>
  <div id="product-home">
    <header class="topbar"><RouterLink class="brand-button" to="/"><img class="brand-mark" src="/purslyx-logo.png" alt="Purslyx 品牌标志" />Purslyx</RouterLink><RouterLink class="guide-link" to="/guide">使用说明</RouterLink><span class="topbar-spacer" /><a class="button outline" href="/purslyx-job-capture.user.js">安装脚本</a><RouterLink class="button soft" to="/app/login">登录</RouterLink><RouterLink class="button primary" to="/app/register">创建账号</RouterLink></header>
    <main class="public-main">
      <section class="hero">
        <div class="hero-copy"><div class="hero-brand"><img class="hero-logo" src="/purslyx-logo.png" alt="Purslyx 品牌标志" /><div><div class="eyebrow">EVIDENCE-FIRST CAREER SKILL</div><small>让每一次经历，都有下一步</small></div></div><h1>从真实经历，<span>走到下一步。</span></h1><p>从岗位页面获取 JD，AI 按证据完成匹配、事实约束改写和 STAR 面试练习；每个重要结论都能回到原始经历和冻结版本。</p><div class="hero-actions"><RouterLink class="button primary" to="/app/register">免费开始</RouterLink><RouterLink class="button outline" to="/app/login">登录工作台</RouterLink></div><div class="proof-row"><div><strong>证据优先</strong>不凭空补写经历</div><div><strong>条件分离</strong>能力与求职期望分开</div><div><strong>闭环协作</strong>岗位、简历与面试连起来</div></div></div>
        <div class="hero-visual"><div class="visual-window"><div class="window-top"><strong>岗位匹配报告</strong><span class="tag success">可复核</span></div><div class="window-lines"><i class="window-line blue" /><i class="window-line" /><i class="window-line short" /><i class="window-line blue" /></div><div class="visual-score"><strong>86</strong><small>能力分</small></div></div></div>
      </section>
      <section v-if="syncRequested" class="product-section sync-section"><div class="sync-panel"><div class="eyebrow">BROWSER SYNC</div><h2>同步浏览器脚本登录</h2><p v-if="syncLoading">正在为当前已登录账号签发一次性授权码……</p><p v-else-if="syncSuccess">{{ syncSuccess }}</p><p v-else-if="syncError">{{ syncError }}</p><div v-if="syncError && !auth.account" class="hero-actions"><RouterLink class="button primary" :to="{ path: '/app/login', query: { redirect: route.fullPath } }">先登录 Purslyx</RouterLink></div><div v-else-if="syncError" class="hero-actions"><button class="button primary" type="button" @click="syncBrowserSession">重新同步</button></div></div></section>
      <section class="product-section"><div class="section-heading"><div><div class="eyebrow">FOR BOTH SIDES</div><h2>不同角色，各有清晰路径</h2></div><p>求职、招聘与授权管理围绕同一套证据和条件规则协作，同时保持数据与身份边界。</p></div><div class="capability-grid"><article class="capability-card"><span class="capability-index">01</span><h3>求职工作台</h3><p>资料与多条期望、匹配池、证据报告、事实改写、岗位版 PDF 和完整面试。</p><RouterLink to="/app/register">以求职者身份开始 →</RouterLink></article><article class="capability-card"><span class="capability-index">02</span><h3>招聘工作台</h3><p>JD、候选人资料、候选人明确期望、单人分析、待核实问题与成本统计。</p><RouterLink to="/app/register">以招聘方身份开始 →</RouterLink></article><article class="capability-card"><span class="capability-index">03</span><h3>授权管理</h3><p>用户、角色权限、次数、站点概况、反馈和三类脱敏日志，所有写操作留审计。</p><RouterLink to="/app/login">管理员登录 →</RouterLink></article></div></section>
      <section id="browser-script" class="product-section"><div class="section-heading"><div><div class="eyebrow">BROWSER ASSISTANT</div><h2>在岗位页面一键同步</h2></div><p>用篡改猴在 BOSS 直聘或猎聘详情页识别当前岗位，直接写入匹配池；需要分析时再选择简历和岗位期望。</p></div><div class="install-layout"><article class="install-card install-card-primary"><span class="capability-index">01</span><h3>安装岗位采集脚本</h3><p>先安装 Tampermonkey，再点击下面的按钮。浏览器会打开安装确认页，确认后即可使用。</p><div class="hero-actions"><a class="button primary" href="/purslyx-job-capture.user.js">一键安装浏览器脚本</a><a class="button outline" href="https://www.tampermonkey.net/" target="_blank" rel="noreferrer">安装 Tampermonkey</a></div><p class="micro">脚本只读取当前打开的岗位详情并写入待匹配岗位，不会自动投递、代聊或提交简历。</p></article><ol class="install-steps"><li><strong>登录 Purslyx</strong><span>保持官网登录状态，脚本通过一次性授权同步。</span></li><li><strong>打开支持的岗位页</strong><span>支持 BOSS 直聘和猎聘的岗位详情页。</span></li><li><strong>点击匹配</strong><span>岗位进入匹配池后，选择简历和岗位期望，再确认一次分析消耗。</span></li></ol></div></section>
    </main>
  </div>
</template>
