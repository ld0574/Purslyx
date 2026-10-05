<script setup lang="ts">
import type { JsonMap } from "@/types";
defineProps<{ content: JsonMap }>();
const dimensions = [{ key: "relevance", label: "回答切题度" }, { key: "specificity", label: "事实具体度" }, { key: "ownership", label: "个人贡献清晰度" }, { key: "outcome_evidence", label: "结果证据力度" }, { key: "communication", label: "表达结构与清晰度" }];
const stars = [{ key: "situation", label: "情境" }, { key: "task", label: "任务" }, { key: "action", label: "行动" }, { key: "result", label: "结果" }];
function level(row: JsonMap = {}) { return row.evidence_status === "unverified" ? "评价暂未完成" : ({ strong: "表现较好", partial: "仍可加强", missing: "信息不足" } as JsonMap)[row.status] || "评价暂未完成"; }
function tone(row: JsonMap = {}) { return row.evidence_status === "unverified" ? "neutral" : row.status === "strong" ? "success" : row.status === "partial" ? "opportunity" : "attention"; }
function clean(value: unknown): string { return String(value || "").replace(/\*\*|__/g, "").replace(/Ownership/gi, "个人贡献").replace(/Outcome[ _]Evidence/gi, "结果证据").replace(/Relevance/gi, "切题度").replace(/Specificity/gi, "事实具体度").replace(/Communication/gi, "表达清晰度"); }
</script>

<template>
  <div class="coaching-feedback">
    <p class="coaching-conclusion">{{ clean(content.summary) }}</p>
    <div v-if="content.evaluation_dimensions" class="coaching-dimensions" aria-label="本题五维评价">
      <details v-for="part in dimensions" :key="part.key" class="coaching-dimension"><summary><strong>{{ part.label }}</strong><span class="tag" :class="tone(content.evaluation_dimensions[part.key])">{{ level(content.evaluation_dimensions[part.key]) }}</span><span aria-hidden="true">⌄</span></summary><p>{{ clean(content.evaluation_dimensions[part.key]?.feedback) }}</p><blockquote v-if="content.evaluation_dimensions[part.key]?.evidence_quote">本轮原文：“{{ content.evaluation_dimensions[part.key].evidence_quote }}”</blockquote></details>
    </div>
    <div v-if="content.followup_review" class="followup-review"><strong>本轮补充</strong><p v-if="!content.followup_review.supplemented?.length" class="micro">本轮暂未提供新的可引用依据。</p><blockquote v-for="(item, index) in content.followup_review.supplemented || []" :key="index">{{ item.evidence_source === 'parent_answer' ? '主回答已有依据' : '本轮补充依据' }}：“{{ item.quote }}”</blockquote><p class="micro">追问承接主问题，不重复五维打分，也不计入练习指数。</p></div>
    <div v-if="content.priority_actions?.length" class="coaching-actions"><strong>{{ content.followup_review ? '仍需澄清' : '优先改进' }}</strong><ol><li v-for="item in content.priority_actions.slice(0, 2)" :key="item">{{ clean(item) }}</li></ol></div>
    <section v-if="content.knowledge_checks?.length" class="coaching-checks"><h4>专业核查与方案权衡</h4><p class="micro">以下为需核实的提示，不是权威事实校验，不计入五维指数。</p><article v-for="(item, index) in content.knowledge_checks" :key="index"><span class="tag opportunity">需核实</span><blockquote>“{{ item.claim_quote }}”</blockquote><p>{{ clean(item.note) }}</p><p>{{ clean(item.verification) }}</p></article></section>
    <details v-if="content.question_kind === 'experience' && content.star_assessment" class="coaching-extra"><summary>STAR 结构诊断 · 仅用于经历题</summary><div class="coaching-star"><article v-for="part in stars" :key="part.key"><strong>{{ part.label }} · {{ level(content.star_assessment[part.key]) }}</strong><p>{{ clean(content.star_assessment[part.key]?.feedback) }}</p></article></div></details>
    <details v-if="content.answer_outline?.length" class="coaching-extra"><summary>重答提纲 · 原文与待补充事项</summary><div v-for="(item, index) in content.answer_outline" :key="index" class="outline-row"><strong>{{ item.label }}</strong><span>{{ item.kind === 'quote' ? '原文依据：' : '待补充：' }}{{ item.text }}</span></div></details>
  </div>
</template>

<style scoped>
.coaching-feedback { padding: 18px; border: 1px solid var(--line); border-radius: 14px; background: var(--surface, #fff); }.coaching-conclusion { line-height: 1.7; font-size: 14px; margin-bottom: 14px; }
.coaching-dimension { border-top: 1px solid var(--line); padding: 10px 0; }.coaching-dimension summary { display: flex; align-items: center; gap: 10px; cursor: pointer; list-style: none; }.coaching-dimension summary strong { flex: 1; }.coaching-dimension p, .coaching-star p { margin-top: 10px; line-height: 1.65; }
blockquote { margin: 10px 0; padding: 9px 12px; border-left: 3px solid var(--brand); background: var(--brand-soft); line-height: 1.65; overflow-wrap: anywhere; }.coaching-actions { padding: 14px; margin-top: 12px; border-radius: 10px; background: var(--brand-soft); }.coaching-actions ol { padding-left: 20px; margin: 8px 0 0; }.coaching-actions li { margin-top: 7px; line-height: 1.65; }
.coaching-extra { border-top: 1px solid var(--line); padding-top: 12px; margin-top: 14px; }.coaching-extra summary { cursor: pointer; font-weight: 600; }.coaching-star, .coaching-checks article { margin-top: 12px; }.coaching-star article { margin-top: 10px; }.coaching-checks { margin-top: 20px; }.coaching-checks p { line-height: 1.65; margin-top: 6px; }.outline-row { display: flex; gap: 10px; margin-top: 12px; line-height: 1.65; }.outline-row strong { flex: 0 0 44px; }
@media (max-width: 560px) { .coaching-feedback { padding: 13px; }.coaching-conclusion { font-size: 13px; }.coaching-dimension summary { gap: 6px; font-size: 12px; } }
</style>
