<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from "vue";
import { useRoute } from "vue-router";
import AppShell from "@/components/AppShell.vue";
import AppDialog from "@/components/AdminDialog.vue";
import AsyncState from "@/components/AsyncState.vue";
import InterviewFeedback from "@/components/InterviewFeedback.vue";
import PageHeader from "@/components/PageHeader.vue";
import { api, idempotencyKey, waitForTask } from "@/services/api";
import type { JsonMap } from "@/types";
import { errorMessage, formatDate, statusLabel, summaryText, textList } from "@/utils/format";

const route = useRoute(); const rubric = "interview-rubric-v2";
const loading = ref(true); const busy = ref(false); const error = ref(""); const success = ref("");
const interviews = ref<JsonMap[]>([]); const poolItems = ref<JsonMap[]>([]); const sourceReportPool = ref<JsonMap | null>(null);
const selected = ref<JsonMap | null>(null); const activeTask = ref<JsonMap | null>(null);
const startForm = reactive({ pool_id: "", title: "岗位面试练习" }); const answerText = ref("");
const historyOpen = ref(false); const startOpen = ref(false);
const practiceQuestion = ref<JsonMap | null>(null); const practiceText = ref(""); const practiceBusy = ref(false);
const practiceError = ref(""); const practiceTask = ref<JsonMap | null>(null);
let readGeneration = 0; let practiceGeneration = 0; let pollingInFlight = false; let pollingTimer: number | undefined;
let taskController = new AbortController(); let practiceController = new AbortController();
const practiceDrafts = new Map<string, string>();
const practiceKeys = new Map<string, { text: string; key: string }>();
const availablePools = computed(() => {
  const rows = sourceReportPool.value ? [sourceReportPool.value, ...poolItems.value.filter((row) => row.id !== sourceReportPool.value?.id)] : poolItems.value;
  return rows.filter((row) => row.latest_analysis?.id && ["available", "succeeded"].includes(row.latest_analysis.status) && (row.latest_analysis.resume_version_id || row.resume_version_id));
});
const legacy = computed(() => selected.value && selected.value.rubric_version !== rubric);
const currentQuestion = computed(() => selected.value?.questions?.find((row: JsonMap) => row.id === selected.value?.current_question_id) || null);
const answeredQuestions = computed<JsonMap[]>(() => legacy.value ? [] : (selected.value?.questions || []).filter((row: JsonMap) => row.answer));
const latestAnswer = computed(() => [...answeredQuestions.value].sort((a, b) => String(b.answer.created_at || "").localeCompare(String(a.answer.created_at || "")))[0] || null);
const summaryRecord = computed<JsonMap>(() => selected.value?.summary?.content || {});
const summaryNarrative = computed<JsonMap>(() => summaryRecord.value.content || summaryRecord.value);
const completed = computed(() => ["completed", "ended_early"].includes(selected.value?.status));
const mainAnswered = computed(() => answeredQuestions.value.filter((row) => row.question_type === "main").length);
const practiceRecord = computed<JsonMap | null>(() => selected.value?.questions?.find((row: JsonMap) => row.id === practiceQuestion.value?.id)?.practice_state?.practice || null);
const shouldPoll = computed(() => !legacy.value && (["opening", "processing"].includes(selected.value?.status) || selected.value?.questions?.some((row: JsonMap) => ["queued", "processing"].includes(row.practice_state?.practice?.status))));
function kindLabel(value: string) { return ({ experience: "经历题", reasoning: "判断题", scenario: "情景题" } as JsonMap)[value] || "经历题"; }
function level(value: string) { return ({ strong: "表现较好", partial: "仍可加强", missing: "信息不足" } as JsonMap)[value] || "评价暂未完成"; }
function changeLabel(value: string) { return ({ improved: "反馈提升", unchanged: "等级未变", weaker: "本次需加强", unavailable: "无法比较" } as JsonMap)[value] || "无法比较"; }

async function readDetail(id: string, generation: number) {
  const view = await api<JsonMap>(`/api/v1/interviews/${encodeURIComponent(id)}`);
  if (generation === readGeneration) {
    if (selected.value?.id !== view.id || selected.value?.current_question_id !== view.current_question_id) answerText.value = "";
    selected.value = view;
  }
  return view;
}
async function load(selectId?: string) {
  const generation = ++readGeneration; loading.value = true; error.value = "";
  try {
    const [sessions, pools] = await Promise.all([api<JsonMap>("/api/v1/interviews?limit=100"), api<JsonMap>("/api/v1/job-pool/items?limit=100")]);
    if (generation !== readGeneration) return;
    interviews.value = (sessions.items || []).filter((row: JsonMap) => row.rubric_version === rubric); poolItems.value = pools.items || []; sourceReportPool.value = null;
    const analysisId = String(route.query.analysis_id || "");
    if (analysisId) {
      const report = await api<JsonMap>(`/api/v1/analyses/${encodeURIComponent(analysisId)}`);
      const resumeId = report.input_versions?.find((row: JsonMap) => row.type === "resume")?.id;
      if (["available", "succeeded"].includes(report.status) && report.job_pool_item_id && resumeId) {
        const pool = poolItems.value.find((row) => row.id === report.job_pool_item_id) || await api<JsonMap>(`/api/v1/job-pool/items/${encodeURIComponent(report.job_pool_item_id)}`);
        if (generation !== readGeneration) return;
        sourceReportPool.value = { ...pool, latest_analysis: { ...report, resume_version_id: resumeId } };
      }
    }
    if (generation !== readGeneration) return;
    if (analysisId || !availablePools.value.some((row) => row.id === startForm.pool_id)) startForm.pool_id = availablePools.value[0]?.id || "";
    const id = selectId !== undefined ? selectId : String(route.query.interview_id || "") || selected.value?.id || interviews.value[0]?.id;
    if (id) await readDetail(id, generation); else selected.value = null;
    if (generation === readGeneration && route.query.practice_id) {
      const question = selected.value?.questions?.find((row: JsonMap) => row.practice_state?.practice?.id === route.query.practice_id);
      if (question && !legacy.value) openPractice(question);
    }
  } catch (value) { if (generation === readGeneration) error.value = errorMessage(value, "练习读取失败"); }
  finally { if (generation === readGeneration) loading.value = false; }
}
async function openInterview(item: JsonMap) {
  if (busy.value || loading.value || practiceBusy.value) return;
  const generation = ++readGeneration; busy.value = true; error.value = "";
  try { await readDetail(item.id, generation); if (generation === readGeneration) { historyOpen.value = false; answerText.value = ""; } }
  catch (value) { if (generation === readGeneration) error.value = errorMessage(value); }
  finally { if (generation === readGeneration) busy.value = false; }
}
async function settle(response: JsonMap, generation: number, controller: AbortController) {
  const view = response.interview || response; const task = response.task || view.task;
  if (generation !== readGeneration) return null;
  selected.value = view;
  try {
    if (task) { activeTask.value = task; await waitForTask(task, { signal: controller.signal, onUpdate: (value) => { if (generation === readGeneration) activeTask.value = value; } }); }
    return await readDetail(view.id, generation);
  } catch (value) {
    if (generation === readGeneration) { try { await readDetail(view.id, generation); } catch { /* 保留现有回答 */ } }
    throw value;
  } finally { if (generation === readGeneration) activeTask.value = null; }
}
async function startInterview() {
  if (busy.value || practiceBusy.value) return;
  const pool = availablePools.value.find((row) => row.id === startForm.pool_id);
  if (!pool) { error.value = "请先完成一份岗位匹配报告"; return; }
  const generation = ++readGeneration; busy.value = true; error.value = ""; success.value = "";
  try {
    const response = await api<JsonMap>("/api/v1/interviews", { method: "POST", idempotencyKey: idempotencyKey("interview-start"), body: { job_pool_item_id: pool.id, analysis_id: pool.latest_analysis.id, resume_document_version_id: pool.latest_analysis.resume_version_id || pool.resume_version_id, title: startForm.title, confirm_usage: true } });
    const view = await settle(response, generation, taskController);
    if (generation !== readGeneration || !view) return;
    startOpen.value = false; success.value = "三道主问题已准备好，本次结算一场"; busy.value = false; await load(view.id);
  } catch (value) { if (generation === readGeneration) error.value = errorMessage(value); }
  finally { if (generation === readGeneration) busy.value = false; }
}
async function submitAnswer() {
  if (busy.value || !selected.value || !currentQuestion.value) return;
  const id = selected.value.id; const generation = ++readGeneration; busy.value = true; error.value = ""; success.value = "";
  try {
    const response = await api<JsonMap>(`/api/v1/interviews/${encodeURIComponent(id)}/answers`, { method: "POST", idempotencyKey: idempotencyKey("interview-answer"), body: { question_id: currentQuestion.value.id, answer_text: answerText.value, base_revision: selected.value.revision } });
    const view = await settle(response, generation, taskController);
    if (generation === readGeneration && view) { answerText.value = ""; success.value = view.status === "completed" ? "练习已完成，可以按建议重答" : "本轮回答和反馈已保存"; }
  } catch (value) { if (generation === readGeneration) { try { await readDetail(id, generation); } catch { /* 不覆盖原始错误 */ } error.value = errorMessage(value); } }
  finally { if (generation === readGeneration) busy.value = false; }
}
async function finishInterview() {
  if (busy.value || !selected.value || !window.confirm("提前结束只保留实际反馈，不生成练习指数。确认结束？")) return;
  const generation = ++readGeneration; busy.value = true; error.value = "";
  try { await settle(await api<JsonMap>(`/api/v1/interviews/${selected.value.id}/finish`, { method: "POST", idempotencyKey: idempotencyKey("interview-finish"), body: { base_revision: selected.value.revision } }), generation, taskController); }
  catch (value) { if (generation === readGeneration) error.value = errorMessage(value); }
  finally { if (generation === readGeneration) busy.value = false; }
}
async function retryTask() {
  if (busy.value || !selected.value?.task?.id) return;
  const id = selected.value.id; const generation = ++readGeneration; busy.value = true; error.value = "";
  try {
    const response = await api<JsonMap>(`/api/v1/tasks/${selected.value.task.id}/retry`, { method: "POST", idempotencyKey: idempotencyKey("interview-retry"), body: { reason: "resume_interview_generation" } });
    await settle({ interview: selected.value, task: response.task }, generation, taskController);
  } catch (value) { if (generation === readGeneration) { try { await readDetail(id, generation); } catch { /* 保留错误 */ } error.value = errorMessage(value); } }
  finally { if (generation === readGeneration) busy.value = false; }
}
function openPractice(question: JsonMap) {
  if (busy.value || practiceBusy.value) return;
  practiceGeneration += 1; practiceQuestion.value = question; practiceError.value = "";
  practiceText.value = question.practice_state?.practice?.answer_text ?? practiceDrafts.get(`${selected.value?.id}:${question.id}`) ?? question.answer?.answer_text ?? "";
}
function closePractice() {
  if (practiceQuestion.value && selected.value && !practiceRecord.value) practiceDrafts.set(`${selected.value.id}:${practiceQuestion.value.id}`, practiceText.value);
  practiceGeneration += 1; practiceController.abort(); practiceController = new AbortController(); practiceBusy.value = false; practiceTask.value = null; practiceQuestion.value = null;
}
async function submitPractice(retry = false) {
  if (!selected.value || !practiceQuestion.value || practiceBusy.value) return;
  const id = selected.value.id; const qid = practiceQuestion.value.id; const generation = readGeneration; const operation = ++practiceGeneration; const controller = practiceController;
  practiceBusy.value = true; practiceError.value = "";
  const draftKey = `${id}:${qid}`;
  if (practiceKeys.get(draftKey)?.text !== practiceText.value) practiceKeys.set(draftKey, { text: practiceText.value, key: idempotencyKey("interview-practice") });
  try {
    const response = retry ? await api<JsonMap>(`/api/v1/tasks/${practiceRecord.value?.task?.id}/retry`, { method: "POST", idempotencyKey: idempotencyKey("practice-retry"), body: { reason: "resume_interview_practice" } }) : await api<JsonMap>(`/api/v1/interviews/${id}/questions/${qid}/practice`, { method: "POST", idempotencyKey: practiceKeys.get(draftKey)!.key, body: { answer_text: practiceText.value } });
    if (generation !== readGeneration) return;
    if (operation !== practiceGeneration) { await readDetail(id, generation); return; }
    practiceTask.value = response.task;
    await waitForTask(response.task, { signal: controller.signal, onUpdate: (value) => { if (operation === practiceGeneration) practiceTask.value = value; } });
    await readDetail(id, generation);
  } catch (value) { if (generation === readGeneration) { try { await readDetail(id, generation); } catch { /* 不覆盖原始错误 */ } if (operation === practiceGeneration) practiceError.value = errorMessage(value, "重答评价暂未完成"); } }
  finally { if (operation === practiceGeneration) { practiceBusy.value = false; practiceTask.value = null; } }
}
async function deleteInterview() {
  if (busy.value || practiceBusy.value || !selected.value) return;
  const id = selected.value.id; busy.value = true;
  try {
    const impact = await api<JsonMap>(`/api/v1/interviews/${id}/deletion-impact`);
    if (!window.confirm(`删除这场练习及 ${impact.affected?.practices || 0} 条重答记录？`)) return;
    await api(`/api/v1/interviews/${id}`, { method: "DELETE", headers: { "If-Match": `"${impact.impact_version}"` } });
    selected.value = null; answerText.value = ""; closePractice(); await load(interviews.value.find((row) => row.id !== id)?.id || "");
  } catch (value) { error.value = errorMessage(value); }
  finally { busy.value = false; }
}
async function pollInterview() {
  if (!shouldPoll.value || !selected.value || loading.value || busy.value || practiceBusy.value || pollingInFlight) return;
  const generation = readGeneration; const id = selected.value.id; pollingInFlight = true;
  try { await readDetail(id, generation); }
  catch (value) { if (generation === readGeneration) error.value = errorMessage(value, "练习状态读取失败"); }
  finally { pollingInFlight = false; }
}
onMounted(() => { void load(); pollingTimer = window.setInterval(pollInterview, 2000); });
watch(() => [route.query.analysis_id, route.query.interview_id, route.query.practice_id], () => { taskController.abort(); taskController = new AbortController(); busy.value = false; activeTask.value = null; answerText.value = ""; closePractice(); void load(); });
onBeforeUnmount(() => { readGeneration += 1; closePractice(); taskController.abort(); if (pollingTimer) window.clearInterval(pollingTimer); });
</script>

<template>
  <AppShell>
    <PageHeader eyebrow="岗位面试训练" title="面试练习" description="回答、看清关键问题、按建议重答。练习表现指数仅用于个人练习，不代表录用概率。"><button class="button outline small" :disabled="busy || loading || practiceBusy" @click="historyOpen = true">练习记录</button><button class="button primary small" :disabled="busy || loading || practiceBusy" @click="startOpen = true">开始新练习</button></PageHeader>
    <AsyncState :loading="loading" :error="error" :success="success" />
    <div v-if="busy && !activeTask" class="callout opportunity" role="status"><span class="spinner" />正在处理，请稍候，无需重复提交。</div>
    <div v-if="activeTask" class="callout opportunity"><span class="spinner" />{{ statusLabel(activeTask.status) }} · {{ activeTask.task_type === 'interview_opening' ? '正在准备题目' : activeTask.task_type === 'interview_summary' ? '正在生成复盘' : '正在生成评价' }}</div>
    <section v-if="legacy" class="empty"><div><strong>这是一场旧版面试练习</strong><p>旧记录已保留，但新版不再展示，也不纳入新练习趋势。</p><button class="button primary" @click="startOpen = true">开始新版练习</button></div></section>
    <div v-else-if="selected && !loading" class="coaching-page">
      <section class="card coaching-session"><div class="card-body"><div class="item-title"><strong>{{ selected.title }}</strong><span class="tag neutral">{{ statusLabel(selected.status) }}</span></div><p class="micro">主问题已回答 {{ mainAnswered }}/3 · 每题最多一次追问 · 每道已回答主问题一次免费重答</p></div></section>
      <section v-if="currentQuestion && selected.status === 'awaiting_answer'" class="card question"><div class="card-body"><div class="item-actions"><span class="tag brand">第 {{ currentQuestion.main_no }}/3 题{{ currentQuestion.question_type === 'followup' ? ' · 追问' : '' }}</span><span class="tag neutral">{{ kindLabel(currentQuestion.question_kind) }}</span></div><h3 class="coaching-question">{{ currentQuestion.question_text }}</h3><form id="answer-form" @submit.prevent="submitAnswer"><label for="interview-answer">你的回答</label><textarea id="interview-answer" v-model="answerText" class="textarea" :disabled="busy" maxlength="8000" required placeholder="直接回答问题，说明真实事实或方案依据，不需要每题套用 STAR。" /><div class="form-foot"><span class="micro">先保存回答，再生成评价。</span><button class="button primary" :disabled="busy">{{ busy ? "正在保存并评价…" : "提交回答" }}</button></div></form><button class="button link-button small" :disabled="busy" @click="finishInterview">提前结束并复盘</button></div></section>
      <div v-if="['processing','opening'].includes(selected.status)" class="callout opportunity"><span class="spinner" />正在生成内容，页面会自动恢复。</div>
      <div v-if="['feedback_failed','opening_failed','summary_failed'].includes(selected.status)" class="callout neutral"><div><strong>评价暂未完成</strong><p>这是生成或校验异常，不代表你的回答不合格。已提交回答已保存。</p><p v-if="selected.task?.request_id" class="micro">请求 ID：{{ selected.task.request_id }}</p><button v-if="selected.task?.retryable" class="button outline small" :disabled="busy" @click="retryTask">重试生成</button></div></div>
      <section v-if="selected.summary" class="card"><div class="card-body"><div class="item-title"><h3>练习总结 · {{ selected.summary.completion_type === 'full' ? '完整完成' : '提前结束' }}</h3><span v-if="summaryRecord.practice_index != null" class="tag brand">练习表现指数 {{ summaryRecord.practice_index }}</span></div><p>{{ summaryText(summaryRecord) }}</p><p class="micro">已回答 {{ summaryRecord.answered_main_count ?? mainAnswered }}/3 道主问题，{{ summaryRecord.answered_followup_count || 0 }} 道追问。指数只使用三道主问题的五维评价。</p><p v-if="summaryRecord.practice_index == null" class="micro">{{ selected.summary.completion_type === 'early' ? '提前结束仅保留实际反馈，不生成练习指数。' : '缺少完整有效的五维评价，本次不生成指数。' }}</p><div v-if="summaryRecord.evaluation_dimensions" class="summary-dimensions"><div v-for="(part, key) in summaryRecord.evaluation_dimensions" :key="key"><span>{{ part.label }}</span><strong>{{ part.score }}</strong></div></div><div v-if="summaryRecord.next_practice_focus" class="callout opportunity"><div><strong>下次重点：{{ summaryRecord.next_practice_focus.label }}</strong><p>{{ summaryRecord.next_practice_focus.suggestion }}</p></div></div><details v-if="textList(summaryNarrative.next_steps).length || textList(summaryNarrative.strengths).length" class="coaching-archive"><summary>展开整场复盘建议</summary><p v-for="item in [...textList(summaryNarrative.strengths), ...textList(summaryNarrative.gaps), ...textList(summaryNarrative.next_steps)]" :key="item">{{ item }}</p></details></div></section>
      <section v-if="latestAnswer && !completed" class="card"><div class="card-body"><h3>刚才的回答 · 第 {{ latestAnswer.main_no }} 题{{ latestAnswer.question_type === 'followup' ? '追问' : '' }}</h3><details class="coaching-archive"><summary>查看已保存回答</summary><p class="answer-copy">{{ latestAnswer.answer.answer_text }}</p></details><InterviewFeedback v-if="latestAnswer.feedback" :content="latestAnswer.feedback.content" /><p v-else class="micro">回答已保存，正在等待有效反馈。</p></div></section>
      <section v-if="answeredQuestions.length" class="card"><div class="card-head"><h3>逐题复盘{{ completed ? '与重答' : '' }}</h3></div><div class="card-body coaching-history"><article v-for="question in answeredQuestions.filter(row => completed || row.id !== latestAnswer?.id)" :key="question.id" class="coaching-history-row"><details><summary>第 {{ question.main_no }} 题{{ question.question_type === 'followup' ? ' · 追问' : '' }} · {{ kindLabel(question.question_kind) }}<span>{{ question.question_text }}</span></summary><p class="answer-copy">{{ question.answer.answer_text }}</p><InterviewFeedback v-if="question.feedback" :content="question.feedback.content" /><p v-else class="micro">原回答评价未完成，没有有效对照基线。</p></details><button v-if="completed && question.question_type === 'main' && (question.practice_state?.eligible || question.practice_state?.practice)" class="button soft small" :data-practice-question="question.id" @click="openPractice(question)">{{ question.practice_state?.practice?.status === 'available' ? '查看重答对照' : question.practice_state?.practice ? '恢复重答训练' : '按建议重答' }}</button></article></div></section>
      <button class="button link-button small" :disabled="busy || practiceBusy" @click="deleteInterview">删除这场练习</button>
    </div>
    <section v-else-if="!loading" class="empty"><div><strong>开始一场新版面试练习</strong><p>先完成岗位匹配，再围绕岗位练习经历、判断与情景问题。</p><button v-if="availablePools.length" class="button primary" @click="startOpen = true">开始新练习</button><RouterLink v-else class="button primary" to="/app/seeker/pool">先完成岗位匹配</RouterLink></div></section>
    <AppDialog v-if="startOpen" :open="true" title="开始新练习" description="三道主问题，开场成功后结算一场面试额度。" @close="startOpen = false"><form id="interview-start-form" @submit.prevent="startInterview"><div class="field-group"><label>已完成分析的岗位</label><select v-model="startForm.pool_id" class="select" required><option value="">请选择岗位</option><option v-for="item in availablePools" :key="item.id" :value="item.id">{{ item.job_title || '目标岗位' }}</option></select></div><div class="field-group"><label>练习标题</label><input v-model="startForm.title" class="input" required maxlength="200" /></div><p class="micro">正式回答与指数不会被免费重答覆盖。</p><button class="button primary" :disabled="busy || !availablePools.length">{{ busy ? "正在生成问题…" : "确认使用一场并开始" }}</button></form></AppDialog>
    <AppDialog v-if="historyOpen" :open="true" title="新版练习记录" description="仅展示新版规则的练习，旧数据保留但不再展示。" @close="historyOpen = false"><p v-if="!interviews.length">还没有新版练习。</p><button v-for="item in interviews" :key="item.id" class="interview-item text-button" :disabled="busy || practiceBusy" @click="openInterview(item)"><strong>{{ item.title }}</strong><small>{{ statusLabel(item.status) }} · {{ formatDate(item.updated_at) }}</small></button></AppDialog>
    <AppDialog v-if="practiceQuestion" :open="true" :wide="true" title="按建议重答" description="每道主问题一次免费重答；失败可重试，不改原场次指数。" @close="closePractice">
      <h4>{{ practiceQuestion.question_text }}</h4><details class="coaching-archive"><summary>原回答与重答提纲</summary><p class="answer-copy">{{ practiceQuestion.answer?.answer_text }}</p><div v-for="(item, index) in practiceQuestion.feedback?.content?.answer_outline || []" :key="index" class="outline-item"><strong>{{ item.label }}</strong> · {{ item.kind === 'quote' ? '原文依据' : '待补充' }}：{{ item.text }}</div></details>
      <p v-if="practiceError" role="alert" class="callout neutral">{{ practiceError }}</p><p v-if="practiceBusy && !practiceTask" class="micro" role="status">正在保存重答并生成评价，请稍候，无需重复提交。</p><p v-if="practiceTask" class="micro">{{ statusLabel(practiceTask.status) }} · 训练评价生成中</p>
      <template v-if="practiceRecord?.status === 'available'"><div class="practice-compare"><article><h4>原回答</h4><p class="answer-copy">{{ practiceQuestion.answer?.answer_text }}</p></article><article><h4>本次重答</h4><p class="answer-copy">{{ practiceRecord.answer_text }}</p></article></div><p v-if="!practiceRecord.comparison?.baseline_available" class="micro">无有效基线，无法比较；不虚构提升。</p><div class="comparison-dimensions"><details v-for="(part, key) in practiceRecord.comparison?.dimensions || {}" :key="key"><summary>{{ part.label }} · {{ changeLabel(part.change) }}<span>{{ part.before ? level(part.before.status) : '无有效基线' }} → {{ level(part.after?.status) }}</span></summary><p v-if="part.before?.evidence_quote">原回答依据：“{{ part.before.evidence_quote }}”</p><p v-if="part.after?.evidence_quote">重答依据：“{{ part.after.evidence_quote }}”</p></details></div><InterviewFeedback :content="practiceRecord.feedback" /><p class="micro">本题免费重答已完成。继续练习可新开场次，原场次指数保持不变。</p></template>
      <div v-else-if="practiceRecord" class="callout neutral"><div><strong>{{ practiceRecord.status === 'failed' ? '重答评价暂未完成' : '重答训练已提交' }}</strong><p class="answer-copy">{{ practiceRecord.answer_text }}</p><p v-if="practiceRecord.task?.request_id" class="micro">请求 ID：{{ practiceRecord.task.request_id }}</p><button v-if="practiceRecord.task?.retryable" class="button outline small" :disabled="practiceBusy" @click="submitPractice(true)">{{ practiceBusy ? "正在重试…" : "重试原训练任务" }}</button><p v-else class="micro">正在处理，可关闭弹窗，稍后从逐题复盘恢复。</p></div></div>
      <form v-else id="practice-form" @submit.prevent="submitPractice(false)"><label for="practice-answer">根据建议完善回答</label><textarea id="practice-answer" v-model="practiceText" class="textarea" :disabled="practiceBusy" maxlength="8000" required /><div class="form-foot"><span class="micro">只补充真实事实，或明确标注拟议方案。</span><button class="button primary" :disabled="practiceBusy">{{ practiceBusy ? "正在保存并评价…" : "提交免费重答" }}</button></div></form>
    </AppDialog>
  </AppShell>
</template>

<style scoped>
.coaching-page { max-width: 960px; margin: 0 auto; display: grid; gap: 18px; }.coaching-session .micro { margin-top: 10px; }.coaching-question { line-height: 1.7; margin: 14px 0; }.textarea { min-height: 190px; margin-top: 8px; }
.answer-copy { white-space: pre-wrap; overflow-wrap: anywhere; line-height: 1.75; margin: 12px 0; }.coaching-archive { margin: 14px 0; }.coaching-archive summary { cursor: pointer; font-weight: 600; }.coaching-archive p { line-height: 1.7; margin-top: 10px; }.coaching-history-row { border-bottom: 1px solid var(--line); padding: 14px 0; }.coaching-history-row details summary { cursor: pointer; font-weight: 600; }.coaching-history-row details summary span { display: block; font-weight: 400; margin: 6px 0; line-height: 1.6; }.coaching-history-row button { margin-top: 10px; }
.summary-dimensions { margin: 14px 0; display: grid; gap: 8px; }.summary-dimensions div { display: flex; justify-content: space-between; border-bottom: 1px solid var(--line); padding: 8px 0; }.practice-compare { display: grid; grid-template-columns: 1fr; gap: 16px; margin: 16px 0; }.practice-compare article { background: var(--brand-soft); padding: 14px; border-radius: 10px; }.comparison-dimensions { margin: 16px 0; }.comparison-dimensions details { border-bottom: 1px solid var(--line); padding: 10px 0; }.comparison-dimensions summary { cursor: pointer; }.comparison-dimensions span { display: block; color: var(--muted); font-size: 12px; margin-top: 5px; }.comparison-dimensions p { margin-top: 10px; line-height: 1.6; }.outline-item { margin: 9px 0; line-height: 1.65; }.field-group { margin-bottom: 14px; }
@media (max-width: 680px) { .practice-compare { grid-template-columns: 1fr; }.coaching-page { gap: 12px; } }
</style>
