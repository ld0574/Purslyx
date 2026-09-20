import type { JsonMap } from "@/types";

const STATUS_LABELS: Record<string, string> = {
  active: "正常",
  suspended: "已暂停",
  pending_verification: "待验证",
  queued: "排队中",
  running: "执行中",
  retry_wait: "等待重试",
  needs_input: "需要补充",
  succeeded: "已完成",
  available: "可使用",
  failed: "失败",
  cancelled: "已取消",
  opening: "准备题目",
  opening_failed: "开场失败",
  awaiting_answer: "等待回答",
  processing: "生成反馈",
  feedback_failed: "反馈失败",
  summary_failed: "总结失败",
  completed: "完整完成",
  ended_early: "提前结束",
  answered: "已回答",
  skipped: "已跳过",
  reviewed: "已复核",
  closed: "已关闭",
  new: "待处理",
  archived: "已归档",
  downloadable: "可下载",
  expired: "已过期",
  deleted: "已下架",
  exporting: "正在导出",
  awaiting_requirements: "待补齐条件",
  onsite: "现场",
  hybrid: "混合",
  remote: "远程",
  unknown: "未披露",
  analysis: "分析",
  rewrite: "改写",
  interview: "面试",
};

const TASK_LABELS: Record<string, string> = {
  document_parse: "资料解析",
  analysis: "岗位分析",
  rewrite: "简历改写",
  resume_export: "PDF 导出",
  interview_opening: "面试开场",
  interview_feedback: "面试反馈",
  interview_summary: "面试总结",
  log_export: "日志导出",
};

export function statusLabel(value?: string): string {
  return value ? STATUS_LABELS[value] || value : "未知";
}

export function statusClass(value?: string): string {
  if (["succeeded", "available", "completed", "downloadable", "active", "reviewed"].includes(value || "")) return "success";
  if (["failed", "feedback_failed", "summary_failed", "opening_failed", "suspended", "cancelled", "deleted"].includes(value || "")) return "attention";
  return "opportunity";
}

export function taskLabel(value?: string): string {
  return value ? TASK_LABELS[value] || value : "任务";
}

export function formatDate(value?: string): string {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false,
  }).format(date);
}

export function formatDateTime(value?: string): string {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : new Intl.DateTimeFormat("zh-CN", {
    year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false,
  }).format(date);
}

export function errorMessage(value: unknown, fallback = "操作失败"): string {
  return value instanceof Error ? value.message : fallback;
}

export function textList(value: unknown): string[] {
  return Array.isArray(value) ? value.map(String) : [];
}

export function summaryText(content: JsonMap | null | undefined): string {
  if (!content) return "总结已生成。";
  const nested = content.content as JsonMap | undefined;
  return String(nested?.summary || content.summary || "总结已生成。");
}
