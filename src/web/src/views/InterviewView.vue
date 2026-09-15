<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref } from "vue";
import { useRoute } from "vue-router";

import AppShell from "@/components/AppShell.vue";
import AsyncState from "@/components/AsyncState.vue";
import PageHeader from "@/components/PageHeader.vue";
import { api, idempotencyKey, waitForTask } from "@/services/api";
import type { JsonMap } from "@/types";
import { errorMessage, formatDate, statusClass, statusLabel, summaryText, taskLabel, textList } from "@/utils/format";

const route = useRoute();
const loading = ref(true);
const busy = ref(false);
const error = ref("");
const success = ref("");
const interviews = ref<JsonMap[]>([]);
const poolItems = ref<JsonMap[]>([]);
const selected = ref<JsonMap | null>(null);
const activeTask = ref<JsonMap | null>(null);
const startForm = reactive({ pool_id: "", title: "岗位面试练习" });
const answerText = ref("");
let pollingTimer: number | undefined;

const availablePools = computed(() => poolItems.value.filter((item) => item.latest_analysis?.id && item.resume_version_id));
const currentQuestion = computed(() => selected.value?.questions?.find((item: JsonMap) => item.id === selected.value?.current_question_id)
  || selected.value?.questions?.find((item: JsonMap) => item.status === "awaiting_answer") || null);
const shouldPoll = computed(() => ["opening", "processing"].includes(String(selected.value?.status || "")));

async function settleInterview(response: JsonMap, interviewId?: string): Promise<JsonMap> {
  const view = response.interview || response;
  selected.value = view;
  const task = response.task || view.task;
  const targetId = interviewId || view.id;
  if (task && ["queued", "running", "retry_wait"].includes(String(task.status))) {
    activeTask.value = task;
    await waitForTask(task, { onUpdate: (value) => { activeTask.value = value; } });
    selected.value = await api<JsonMap>(`/api/v1/interviews/${encodeURIComponent(targetId)}`);
  }
  activeTask.value = null;
  return selected.value || view;
}

async function load(selectId?: string) {
  loading.value = true; error.value = "";
  try {
    const [interviewResult, poolResult] = await Promise.all([
      api<JsonMap>("/api/v1/interviews"), api<JsonMap>("/api/v1/job-pool/items"),
    ]);
    interviews.value = interviewResult.items || [];
    poolItems.value = poolResult.items || [];
    const analysisId = String(route.query.analysis_id || "");
    const preferredPool = availablePools.value.find((item) => item.latest_analysis?.id === analysisId) || availablePools.value[0];
    startForm.pool_id ||= preferredPool?.id || "";
    const targetId = selectId || String(route.query.interview_id || "") || selected.value?.id || interviews.value[0]?.id;
    selected.value = targetId ? await api<JsonMap>(`/api/v1/interviews/${encodeURIComponent(targetId)}`) : null;
  } catch (value) { error.value = errorMessage(value, "面试记录读取失败"); }
  finally { loading.value = false; }
}

async function openInterview(item: JsonMap) {
  busy.value = true; error.value = "";
  try { selected.value = await api<JsonMap>(`/api/v1/interviews/${encodeURIComponent(item.id)}`); answerText.value = ""; }
  catch (value) { error.value = errorMessage(value); }
  finally { busy.value = false; }
}

async function startInterview() {
  const pool = availablePools.value.find((item) => item.id === startForm.pool_id);
  if (!pool) { error.value = "请先选择一份已经完成分析的岗位"; return; }
  busy.value = true; error.value = ""; success.value = "";
  try {
    const result = await api<JsonMap>("/api/v1/interviews", {
      method: "POST", idempotencyKey: idempotencyKey("interview-start"), body: {
        job_pool_item_id: pool.id,
        analysis_id: pool.latest_analysis.id,
        resume_document_version_id: pool.resume_version_id,
        title: startForm.title,
        confirm_usage: true,
      },
    });
    success.value = "面试开场任务已受理，正在准备 3 道主问题";
    const view = await settleInterview(result, result.interview?.id);
    success.value = view.usage_settled ? "3 道主问题已准备好，本次结算 1 场" : "面试题目已准备好";
    await load(view.id);
  } catch (value) { error.value = errorMessage(value); }
  finally { busy.value = false; }
}

async function submitAnswer() {
  if (!selected.value || !currentQuestion.value) return;
  busy.value = true; error.value = ""; success.value = "";
  try {
    const result = await api<JsonMap>(`/api/v1/interviews/${encodeURIComponent(selected.value.id)}/answers`, {
      method: "POST", idempotencyKey: idempotencyKey("interview-answer"), body: {
        question_id: currentQuestion.value.id,
        answer_text: answerText.value,
        base_revision: selected.value.revision,
      },
    });
    const view = await settleInterview(result, selected.value.id);
    answerText.value = "";
    success.value = view.status === "completed" ? "三道主问题已经完成，练习总结已生成" : "回答与本轮反馈已保存";
  } catch (value) { error.value = errorMessage(value); }
  finally { busy.value = false; }
}

async function finishInterview() {
  if (!selected.value || !window.confirm("提前结束后，未回答题目会标记为跳过并按已有回答生成总结。确认继续？")) return;
  busy.value = true; error.value = "";
  try {
    const interviewId = selected.value.id;
    const result = await api<JsonMap>(`/api/v1/interviews/${encodeURIComponent(interviewId)}/finish`, {
      method: "POST", idempotencyKey: idempotencyKey("interview-finish"), body: { base_revision: selected.value.revision },
    });
    await settleInterview(result, interviewId);
    success.value = "已提前结束并生成总结";
  } catch (value) { error.value = errorMessage(value); }
  finally { busy.value = false; }
}

async function retryTask() {
  const taskId = selected.value?.task?.id;
  if (!taskId) return;
  busy.value = true; error.value = "";
  try {
    const result = await api<JsonMap>(`/api/v1/tasks/${encodeURIComponent(taskId)}/retry`, {
      method: "POST", idempotencyKey: idempotencyKey("interview-retry"), body: { reason: "resume_interview_generation" },
    });
    activeTask.value = result.task;
    success.value = "重试已受理，正在恢复原任务";
    await waitForTask(result.task, { onUpdate: (value) => { activeTask.value = value; } });
    activeTask.value = null;
    await load(selected.value?.id);
  } catch (value) { error.value = errorMessage(value); }
  finally { busy.value = false; }
}

async function deleteInterview() {
  if (!selected.value) return;
  busy.value = true; error.value = "";
  try {
    const impact = await api<JsonMap>(`/api/v1/interviews/${encodeURIComponent(selected.value.id)}/deletion-impact`);
    if (!window.confirm(`删除后将不可访问 ${impact.affected?.questions || 0} 道题和相关回答，确认删除？`)) return;
    await api(`/api/v1/interviews/${encodeURIComponent(selected.value.id)}`, {
      method: "DELETE", headers: { "If-Match": `"${impact.impact_version}"` },
    });
    selected.value = null; success.value = "面试练习已删除"; await load();
  } catch (value) { error.value = errorMessage(value); }
  finally { busy.value = false; }
}

function feedbackItems(question: JsonMap, key: string): string[] {
  const content = question.feedback?.content as JsonMap | undefined;
  return textList(content?.[key]);
}

onMounted(() => {
  load();
  pollingTimer = window.setInterval(() => { if (shouldPoll.value && selected.value) openInterview(selected.value); }, 2000);
});
onBeforeUnmount(() => { if (pollingTimer) window.clearInterval(pollingTimer); });
</script>

<template>
  <AppShell>
    <PageHeader eyebrow="INTERVIEW PRACTICE" title="面试练习" description="围绕一份冻结的岗位报告逐轮回答；3 道主问题、每题最多一次追问，刷新后仍能恢复。">
      <button class="button outline small" type="button" @click="load()">刷新</button>
    </PageHeader>
    <AsyncState :loading="loading" :error="error" :success="success" />
    <div v-if="activeTask" class="callout opportunity task-inline-state"><span class="spinner" />{{ taskLabel(activeTask.task_type) }}：{{ statusLabel(activeTask.status) }} · {{ activeTask.current_step || "等待执行" }}</div>

    <section v-if="!loading" class="card" style="margin-bottom:18px">
      <div class="card-head"><div><h3>开始一场新练习</h3><p>开场题目成功保存后结算 1 场，后续回答与恢复不重复扣减。</p></div></div>
      <form id="interview-start-form" class="card-body form-card form-row" @submit.prevent="startInterview">
        <div class="field-group"><label>岗位报告</label><select v-model="startForm.pool_id" class="select" required><option value="" disabled>请选择</option><option v-for="item in availablePools" :key="item.id" :value="item.id">{{ item.job_title }} · {{ item.latest_analysis.ability_score ?? '—' }} 分</option></select></div>
        <div class="field-group"><label>练习名称</label><input v-model="startForm.title" class="field" required /></div>
        <div class="item-actions"><button class="button primary" :disabled="busy || !availablePools.length" type="submit">确认消耗 1 场并开始</button><RouterLink v-if="!availablePools.length" class="button soft" to="/app/seeker/pool">先生成岗位报告</RouterLink></div>
      </form>
    </section>

    <div v-if="!loading" class="interview-layout">
      <aside class="interview-nav">
        <div class="eyebrow" style="margin:4px 4px 12px">历史练习</div>
        <div v-if="!interviews.length" class="empty"><div><strong>还没有会话</strong><p>从上方已完成报告开始。</p></div></div>
        <button v-for="item in interviews" :key="item.id" class="interview-item text-button" :class="{ active: selected?.id === item.id }" type="button" @click="openInterview(item)"><strong>{{ item.title }}</strong><small>{{ statusLabel(item.status) }} · {{ formatDate(item.updated_at) }}</small></button>
      </aside>

      <section v-if="selected" class="question">
        <div class="item-title"><div><div class="eyebrow">{{ selected.title }}</div><h3>{{ currentQuestion ? `第 ${currentQuestion.main_no} 题${currentQuestion.question_type === 'followup' ? ' · 追问' : ''}：${currentQuestion.question_text}` : statusLabel(selected.status) }}</h3></div><span class="tag" :class="statusClass(selected.status)">{{ statusLabel(selected.status) }}</span></div>
        <form v-if="selected.status === 'awaiting_answer' && currentQuestion" id="answer-form" class="answer-box" @submit.prevent="submitAnswer"><label for="interview-answer">你的回答</label><textarea id="interview-answer" v-model="answerText" class="textarea" placeholder="按背景、本人行动、可核对结果写下真实回答……" required /><div class="form-foot"><span class="micro">提交后先保存回答，再生成本轮反馈。</span><button class="button primary" :disabled="busy" type="submit">提交回答</button></div></form>
        <div v-else-if="selected.status === 'processing' || selected.status === 'opening'" class="callout opportunity"><span class="spinner" />正在生成内容，页面会自动刷新。</div>
        <div v-else-if="['feedback_failed','summary_failed','opening_failed'].includes(selected.status)" class="callout attention">已保存的回答不会丢失。<button v-if="selected.task?.retryable" class="button outline small" type="button" @click="retryTask">重试生成</button></div>
        <div v-if="selected.summary" class="summary-box"><strong>练习总结 · {{ selected.summary.completion_type === 'full' ? '完整完成' : '提前结束' }}</strong><p>{{ summaryText(selected.summary.content) }}</p><p v-if="selected.summary.content?.next_steps?.length">下一步：{{ selected.summary.content.next_steps.join('；') }}</p></div>
        <button v-if="selected.status === 'awaiting_answer'" class="button outline small" style="margin-top:12px" type="button" @click="finishInterview">提前结束并总结</button>

        <section v-if="selected.questions?.length" class="interview-history" style="margin-top:22px"><div class="card-head"><div><h3>逐题记录</h3><p>问题、本人回答与反馈完整保留。</p></div></div><div class="card-list"><article v-for="question in selected.questions" :key="question.id" class="interview-question-row"><div class="item-title"><strong>第 {{ question.main_no }} 题{{ question.question_type === 'followup' ? ' · 追问' : '' }}</strong><span class="tag" :class="statusClass(question.status)">{{ statusLabel(question.status) }}</span></div><p class="micro" style="margin-top:7px">{{ question.question_text }}</p><div v-if="question.answer" class="compare-pane original" style="margin-top:9px"><h4>我的回答</h4><p>{{ question.answer.answer_text }}</p></div><div v-if="question.feedback" class="feedback-box"><strong>本题反馈</strong><ul><li v-for="item in feedbackItems(question, 'strengths')" :key="`s-${item}`">优势：{{ item }}</li><li v-for="item in feedbackItems(question, 'gaps')" :key="`g-${item}`">可补充：{{ item }}</li><li v-for="item in feedbackItems(question, 'suggestions')" :key="`n-${item}`">建议：{{ item }}</li></ul><span v-if="question.feedback.needs_followup" class="tag opportunity">需要一次追问</span></div></article></div></section>
        <div class="item-actions"><button class="button link-button small" :disabled="busy" type="button" @click="deleteInterview">删除这场练习</button></div>
      </section>
      <section v-else class="empty"><div><strong>选择一场练习</strong><p>系统会从报告生成 3 道主问题，并保留所有轮次。</p></div></section>
    </div>
  </AppShell>
</template>
