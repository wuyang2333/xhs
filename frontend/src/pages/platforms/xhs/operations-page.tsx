import { CalendarOutlined, CheckOutlined, DeleteOutlined, EditOutlined, ExperimentOutlined, MoreOutlined, PlusOutlined, SyncOutlined } from "@ant-design/icons";
import { Alert, Button, Card, Col, Collapse, DatePicker, Descriptions, Divider, Dropdown, Form, Input, InputNumber, List, message, Modal, Progress, Row, Select, Space, Switch, Table, Tag, Typography } from "antd";
import dayjs from "dayjs";
import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { analyzeAccountStrategy, approveStrategyProposal, clearAccountContent, fetchAccountOperationSummary, fetchAccountOperationTask, fetchAccounts, fetchOperationPlans, fetchOperationProfiles, fetchStrategyProposals, generateOperationPlanDraft, generateOperationPlans, rejectStrategyProposal, saveOperationProfile, syncAccountContent, updateOperationPlan } from "../../../lib/api";
import type { AccountOperationSummary, OperationPlan, OperationProfile, PlatformAccount, StrategyProposal, TaskRecord } from "../../../types";

const { Title, Text } = Typography;
const split = (value = "") => value.split(/[,，\n]/).map((item) => item.trim()).filter(Boolean);
const planStatusLabels: Record<string, string> = {
  pending: "待生成", drafted: "已生成草稿", scheduled: "已排入发布", published: "已发布", skipped: "已跳过",
};
const planStatusColors: Record<string, string> = {
  pending: "default", drafted: "blue", scheduled: "gold", published: "green", skipped: "default",
};
const taskPhaseLabels: Record<string, string> = {
  queued: "排队等待", starting: "准备同步", syncing: "正在同步内容",
  syncing_local: "正在整理工作台内容", syncing_remote: "正在读取账号最新内容", organizing: "正在整理同步结果",
  analyzing: "正在分析策略", saving: "正在保存方案", completed: "已完成", failed: "执行失败",
};
const weekdayLabels = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"];
const syncModeLabels: Record<string, string> = { quick: "快速同步", full: "完整同步", rebuild: "强制重建" };
const dismissedStatusAlertsStorageKey = "xhs.operations.dismissed-status-alerts.v1";

function isTimeoutError(value: string): boolean {
  return /timed\s*out|timeout|超时/i.test(value);
}

function userFacingTaskMessage(task: TaskRecord): string {
  const raw = String(task.payload?.message || "");
  if (task.status === "failed") {
    return task.task_type === "account_content_sync" ? "内容同步未完成，请检查手机连接后重试" : "策略分析未完成，请稍后重试";
  }
  return raw || (task.status === "completed" ? "任务已完成" : "任务执行中");
}

function readDismissedStatusAlerts(): Set<string> {
  if (typeof window === "undefined") return new Set();
  try {
    const value = JSON.parse(window.localStorage.getItem(dismissedStatusAlertsStorageKey) || "[]");
    return new Set(Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : []);
  } catch {
    return new Set();
  }
}

export function XhsOperationsPage() {
  const [searchParams] = useSearchParams();
  const [accounts, setAccounts] = useState<PlatformAccount[]>([]);
  const [profiles, setProfiles] = useState<OperationProfile[]>([]);
  const [plans, setPlans] = useState<OperationPlan[]>([]);
  const [date, setDate] = useState(dayjs());
  const [weekStart, setWeekStart] = useState(dayjs().startOf("day"));
  const [accountFilter, setAccountFilter] = useState<number | undefined>(() => Number(searchParams.get("account_id")) || undefined);
  const [editing, setEditing] = useState<PlatformAccount | null>(null);
  const [loading, setLoading] = useState(false);
  const [syncing, setSyncing] = useState<Set<number>>(new Set());
  const [clearing, setClearing] = useState<Set<number>>(new Set());
  const [proposals, setProposals] = useState<StrategyProposal[]>([]);
  const [reviewing, setReviewing] = useState<StrategyProposal | null>(null);
  const [analyzing, setAnalyzing] = useState<Set<number>>(new Set());
  const [operationTasks, setOperationTasks] = useState<Map<number, TaskRecord>>(new Map());
  const [operationSummaries, setOperationSummaries] = useState<Map<number, AccountOperationSummary>>(new Map());
  const [dismissedStatusAlerts, setDismissedStatusAlerts] = useState<Set<string>>(readDismissedStatusAlerts);
  const [deciding, setDeciding] = useState(false);
  const [createAutoTasks, setCreateAutoTasks] = useState(false);
  const [form] = Form.useForm();
  const [editingPlan, setEditingPlan] = useState<OperationPlan | null>(null);
  const [planSaving, setPlanSaving] = useState(false);
  const [draftingPlanIds, setDraftingPlanIds] = useState<Set<number>>(new Set());
  const [draftingAll, setDraftingAll] = useState(false);
  const [planForm] = Form.useForm();
  const accountMap = useMemo(() => new Map(accounts.map((item) => [item.id, item])), [accounts]);
  const activeProfileCount = useMemo(
    () => profiles.filter((item) => item.status === "active" && (!accountFilter || item.platform_account_id === accountFilter)).length,
    [profiles, accountFilter],
  );

  useEffect(() => {
    try {
      const recent = Array.from(dismissedStatusAlerts).slice(-100);
      window.localStorage.setItem(dismissedStatusAlertsStorageKey, JSON.stringify(recent));
    } catch {
      // Private browsing or storage restrictions should not block the page.
    }
  }, [dismissedStatusAlerts]);

  async function load() {
    const rangeStart = weekStart.startOf("day");
    const rangeEnd = rangeStart.add(6, "day");
    const [accountItems, profileItems, planItems, proposalItems] = await Promise.all([
      fetchAccounts("xhs"), fetchOperationProfiles(), fetchOperationPlans(rangeStart.format("YYYY-MM-DD"), rangeEnd.format("YYYY-MM-DD"), accountFilter), fetchStrategyProposals({ status: "pending" }),
    ]);
    setAccounts(accountItems); setProfiles(profileItems); setPlans(planItems); setProposals(proposalItems);
    const [taskEntries, summaryEntries] = await Promise.all([
      Promise.all(accountItems.map(async (account) => [account.id, await fetchAccountOperationTask(account.id)] as const)),
      Promise.all(accountItems.map(async (account) => { try { return [account.id, await fetchAccountOperationSummary(account.id)] as const; } catch { return [account.id, null] as const; } })),
    ]);
    setOperationTasks(new Map(taskEntries.filter((entry): entry is readonly [number, TaskRecord] => Boolean(entry[1]))));
    setOperationSummaries(new Map(summaryEntries.filter((entry): entry is readonly [number, AccountOperationSummary] => Boolean(entry[1]))));
    const requested = Number(searchParams.get("account_id"));
    if (requested && !editing) openProfile(accountItems.find((item) => item.id === requested) || null, profileItems);
  }
  useEffect(() => { void load(); }, [weekStart.format("YYYY-MM-DD"), accountFilter]);

  function openProfile(account: PlatformAccount | null, source = profiles) {
    if (!account) return;
    const profile = source.find((item) => item.platform_account_id === account.id);
    form.setFieldsValue({
      positioning: profile?.positioning, target_audience: profile?.target_audience,
      homepage_url: profile?.homepage_url,
      content_directions: profile?.content_directions.join("\n"), daily_post_count: profile?.daily_post_count ?? 1,
      daily_generate_count: profile?.daily_generate_count ?? profile?.daily_post_count ?? 1,
      daily_publish_count: profile?.daily_publish_count ?? profile?.daily_post_count ?? 1,
      preferred_times: profile?.preferred_times.join(", "), goals: profile?.goals.join("\n"),
      tone: profile?.tone, forbidden_topics: profile?.forbidden_topics.join("\n"),
      review_required: profile?.review_required ?? true, status: profile?.status ?? "active",
    });
    setEditing(account);
  }
  async function save(values: Record<string, unknown>) {
    if (!editing) return;
    await saveOperationProfile(editing.id, {
      positioning: String(values.positioning || ""), target_audience: String(values.target_audience || ""),
      homepage_url: String(values.homepage_url || ""),
      content_directions: split(String(values.content_directions || "")), daily_post_count: Number(values.daily_publish_count || 0),
      daily_generate_count: Number(values.daily_generate_count || 0), daily_publish_count: Number(values.daily_publish_count || 0),
      preferred_times: split(String(values.preferred_times || "")), goals: split(String(values.goals || "")),
      tone: String(values.tone || ""), forbidden_topics: split(String(values.forbidden_topics || "")),
      review_required: Boolean(values.review_required), status: values.status as "active" | "paused",
    });
    message.success("运营档案已保存"); setEditing(null); await load();
  }
  async function syncContent(account: PlatformAccount, mode: "quick" | "full" | "rebuild" = "full", analyzeAfterSync = false) {
    setSyncing((current) => new Set(current).add(account.id));
    try {
      const task = await syncAccountContent(account.id, { mode, analyze_after_sync: analyzeAfterSync });
      setOperationTasks((current) => new Map(current).set(account.id, task));
      message.info(analyzeAfterSync ? "内容同步与分析已开始，可在账号卡片查看进度" : "内容同步已开始，同步完成后可单独开始分析");
    } catch (caught) {
      const detail = (caught as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      message.error(detail || "无法启动同步与分析任务");
    }
    finally { setSyncing((current) => { const next = new Set(current); next.delete(account.id); return next; }); }
  }
  async function clearContent(account: PlatformAccount) {
    setClearing((current) => new Set(current).add(account.id));
    try {
      const result = await clearAccountContent(account.id);
      message.success(`已清空内容库（${result.deleted} 篇），可重新「同步最新内容并分析」`);
      await load();
    } catch { message.error("清空内容库失败"); }
    finally { setClearing((current) => { const next = new Set(current); next.delete(account.id); return next; }); }
  }
  async function analyze(account: PlatformAccount) {
    setAnalyzing((current) => new Set(current).add(account.id));
    try {
      const task = await analyzeAccountStrategy(account.id);
      setOperationTasks((current) => new Map(current).set(account.id, task));
      message.info("已开始基于现有内容重新分析，可在账号卡片查看进度");
    } catch (caught) {
      const detail = (caught as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      message.error(detail || "无法启动重新分析任务");
    }
    finally { setAnalyzing((current) => { const next = new Set(current); next.delete(account.id); return next; }); }
  }
  async function approve() {
    if (!reviewing) return;
    setDeciding(true);
    try {
      const result = await approveStrategyProposal(reviewing.id, reviewing.strategy, { create_auto_tasks: createAutoTasks });
      message.success(`方案已应用：${result.insights_created}条学习结论，${result.plans_created}条计划${createAutoTasks ? `，已创建${result.auto_tasks_created}个暂停的自动任务` : ""}`);
      setReviewing(null); await load();
    } finally { setDeciding(false); }
  }
  async function reject() {
    if (!reviewing) return;
    setDeciding(true);
    try { await rejectStrategyProposal(reviewing.id); message.info("方案已拒绝，未修改运营档案"); setReviewing(null); await load(); }
    finally { setDeciding(false); }
  }
  async function generate(startDate = date, endDate?: dayjs.Dayjs) {
    setLoading(true);
    try {
      const result = await generateOperationPlans(startDate.format("YYYY-MM-DD"), endDate?.format("YYYY-MM-DD"), accountFilter ? [accountFilter] : []);
      const rangeLabel = endDate ? "从 " + startDate.format("MM-DD") + " 起 7 天" : startDate.format("MM-DD") + " 当天";
      if (result.created) message.success(rangeLabel + "新增 " + result.created + " 条计划" + (result.existing ? "，已有 " + result.existing + " 条" : "") + (endDate ? "，覆盖 " + result.days_covered + "/" + result.days_requested + " 天" : ""));
      else if (activeProfileCount === 0) message.warning("暂无启用中的运营档案，请先建立并启用档案");
      else message.info(rangeLabel + "已有 " + result.existing + " 条排期，无需重复生成");
      await load();
    } finally { setLoading(false); }
  }

  function generateWeek() {
    const start = weekStart.startOf("day");
    const end = start.add(6, "day");
    Modal.confirm({
      title: "从所选日期生成 7 天排期？",
      content: "将为有运营档案的账号生成 " + start.format("MM月DD日") + " 至 " + end.format("MM月DD日") + " 的缺失计划，已有计划不会重复创建。",
      okText: "生成 7 天排期",
      cancelText: "取消",
      onOk: () => generate(start, end),
    });
  }

  function openPlanEditor(plan: OperationPlan) {
    planForm.setFieldsValue({
      content_direction: plan.content_direction,
      topic: plan.topic,
      planned_at: plan.planned_at ? dayjs(plan.planned_at) : null,
      status: plan.status,
    });
    setEditingPlan(plan);
  }

  async function savePlan(values: Record<string, unknown>) {
    if (!editingPlan) return;
    setPlanSaving(true);
    try {
      const plannedAt = values.planned_at && dayjs.isDayjs(values.planned_at) ? (values.planned_at as dayjs.Dayjs).toISOString() : null;
      const updated = await updateOperationPlan(editingPlan.id, {
        content_direction: String(values.content_direction || "").trim(),
        topic: String(values.topic || "").trim(),
        planned_at: plannedAt,
        status: String(values.status || "pending"),
      });
      setPlans((items) => items.map((item) => item.id === updated.id ? updated : item));
      message.success("计划已更新");
      setEditingPlan(null);
    } catch (caught) {
      const detail = (caught as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      message.error(detail || "更新计划失败");
    } finally { setPlanSaving(false); }
  }

  async function generateDraft(plan: OperationPlan) {
    if (plan.draft_id) return;
    setDraftingPlanIds((current) => new Set(current).add(plan.id));
    try {
      const result = await generateOperationPlanDraft(plan.id);
      setPlans((items) => items.map((item) => item.id === result.plan.id ? result.plan : item));
      message.success(result.created ? "草稿已生成，未创建发布任务" : "该计划已关联草稿");
    } catch (caught) {
      const detail = (caught as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      message.error(detail || "生成草稿失败");
    } finally {
      setDraftingPlanIds((current) => { const next = new Set(current); next.delete(plan.id); return next; });
    }
  }

  async function generateAllDrafts() {
    if (pendingWeekPlans.length === 0) {
      message.info("当前 7 天没有待生成草稿的排期");
      return;
    }
    setDraftingAll(true);
    let success = 0;
    let failed = 0;
    for (const plan of pendingWeekPlans) {
      setDraftingPlanIds((current) => new Set(current).add(plan.id));
      try {
        const result = await generateOperationPlanDraft(plan.id);
        setPlans((items) => items.map((item) => item.id === result.plan.id ? result.plan : item));
        if (result.created) success += 1;
      } catch {
        failed += 1;
      } finally {
        setDraftingPlanIds((current) => { const next = new Set(current); next.delete(plan.id); return next; });
      }
    }
    setDraftingAll(false);
    if (failed) message.warning(`已生成 ${success} 条草稿，${failed} 条失败（不会自动发布）`);
    else if (success) message.success(`已生成 ${success} 条草稿（不会自动发布）`);
  }

  const activeOperationAccountIds = useMemo(
    () => Array.from(operationTasks.entries())
      .filter(([, task]) => task.status === "pending" || task.status === "running")
      .map(([accountId]) => accountId),
    [operationTasks],
  );

  const visiblePlans = useMemo(
    () => plans.filter((item) => (!accountFilter || item.platform_account_id === accountFilter) && item.plan_date === date.format("YYYY-MM-DD")),
    [plans, accountFilter, date],
  );
  const weekDays = useMemo(() => Array.from({ length: 7 }, (_, index) => weekStart.add(index, "day")), [weekStart]);
  const weekPlans = useMemo(() => plans.filter((item) => !accountFilter || item.platform_account_id === accountFilter), [plans, accountFilter]);
  const pendingWeekPlans = useMemo(() => weekPlans.filter((item) => !item.draft_id && item.status === "pending"), [weekPlans]);
  const isToday = date.isSame(dayjs(), "day");

  useEffect(() => {
    if (activeOperationAccountIds.length === 0) return;
    const timer = window.setInterval(() => {
      void (async () => {
        const updates = await Promise.all(activeOperationAccountIds.map(async (accountId) => {
          try { return [accountId, await fetchAccountOperationTask(accountId)] as const; }
          catch { return [accountId, null] as const; }
        }));
        let finished = false;
        setOperationTasks((current) => {
          const nextMap = new Map(current);
          for (const [accountId, next] of updates) {
            if (!next) continue;
            const previous = current.get(accountId);
            if (previous && ["pending", "running"].includes(previous.status) && !["pending", "running"].includes(next.status)) {
              finished = true;
              const text = userFacingTaskMessage(next);
              if (next.status === "completed") message.success(text);
              else message.error(text);
            }
            nextMap.set(accountId, next);
          }
          return nextMap;
        });
        if (finished) await load();
      })();
    }, 1500);
    return () => window.clearInterval(timer);
  }, [activeOperationAccountIds.join(",")]);

  return <div>
    <Text type="secondary" style={{ letterSpacing: 1.5 }}>XHS OPERATIONS</Text>
    <Title level={3} style={{ margin: "4px 0" }}>运营计划</Title>
    <Text type="secondary">先同步账号内容，再生成策略方案；确认后才会建立运营档案与排期。自动运营需要单独在自动运营模块启用。</Text>
    <div style={{ marginTop: 16 }}><Select allowClear value={accountFilter} onChange={setAccountFilter} placeholder="选择账号查看运营方案" style={{ width: 280 }} options={accounts.map((item) => ({ value: item.id, label: item.nickname || `#${item.id}` }))} /></div>
    <Row gutter={[16, 16]} style={{ marginTop: 24 }}>
      {accounts.filter((account) => !accountFilter || account.id === accountFilter).map((account) => {
        const profile = profiles.find((item) => item.platform_account_id === account.id);
        const pending = proposals.find((item) => item.platform_account_id === account.id);
        const summary = operationSummaries.get(account.id);
        const operationTask = operationTasks.get(account.id);
        const taskActive = operationTask?.status === "pending" || operationTask?.status === "running";
        const taskMessage = String(operationTask?.payload?.message || "正在准备任务");
        const taskRemoteError = String(operationTask?.payload?.remote_error || "");
        const taskPhase = String(operationTask?.payload?.phase || "");
        const taskTypeLabel = operationTask?.task_type === "account_content_sync" ? "内容同步" : "策略分析";
        const isContentSyncTask = operationTask?.task_type === "account_content_sync";
        const taskFailed = operationTask?.status === "failed";
        const partialSync = summary?.sync.status === "partial";
        const statusType = taskActive ? "info" : taskFailed ? "error" : pending ? "warning" : partialSync ? "warning" : profile ? "success" : "info";
        const statusTitle = taskActive ? `${taskTypeLabel}进行中` : taskFailed ? `${taskTypeLabel}失败` : pending ? "策略方案待确认" : partialSync ? "同步完成（部分结果已保存）" : profile ? "运营档案已建立" : "尚未建立运营档案";
        const scanCount = Number(operationTask?.payload?.scanned_count || summary?.sync.total || 0);
        const scanTarget = Number(operationTask?.payload?.target_count || summary?.sync.target_count || 0);
        const scanScrolls = Number(operationTask?.payload?.scroll_count || summary?.sync.scroll_count || 0);
        const rawSyncError = taskRemoteError || String(summary?.sync.error || "") || (taskFailed ? String(operationTask?.payload?.message || "") : "");
        const retainedCount = Math.max(Number(summary?.sync.note_count || 0), Math.min(scanCount, scanTarget || scanCount));
        const hasSyncIssue = Boolean(rawSyncError) && (taskFailed || partialSync || summary?.sync.status === "failed");
        const lastSyncMode: "quick" | "full" | "rebuild" = summary?.sync.sync_mode === "quick" || summary?.sync.sync_mode === "rebuild" ? summary.sync.sync_mode : "full";
        const statusDescription = taskActive
          ? `${taskPhaseLabels[taskPhase] || taskMessage}${taskPhase === "syncing_remote" && scanTarget ? ` · 已读取 ${Math.min(scanCount, scanTarget)} / ${scanTarget} 篇 · 翻页 ${scanScrolls} 次` : ""}`
          : taskFailed
            ? isContentSyncTask
              ? (retainedCount > 0 ? `${taskTypeLabel}未完成，已保留 ${retainedCount} 篇内容。请检查手机连接后重试。` : "内容同步未完成，请检查手机连接后重试。")
              : "策略分析未完成，请稍后重试。"
            : pending
              ? `基于 ${pending.source_note_count} 篇样本生成，确认后写入档案和排期`
              : partialSync
                ? `${isTimeoutError(rawSyncError) ? "读取账号内容超时" : "账号内容未完整读取"}，已保留 ${retainedCount} 篇内容。可继续同步或直接开始分析。`
                : profile ? "可以继续生成当日内容排期" : "先建立运营档案，再生成内容计划";
        const canContinueSync = isContentSyncTask && (partialSync || taskFailed) && Boolean(summary?.sync.note_count);
        const statusAction = pending
          ? <Button size="small" type="link" onClick={() => { setCreateAutoTasks(false); setReviewing(pending); }}>查看方案</Button>
          : summary?.sync.note_count
            ? <Space size={4}><Button size="small" type="link" disabled={taskActive} onClick={() => void analyze(account)}>开始分析</Button>{canContinueSync ? <Button size="small" type="link" icon={<SyncOutlined />} disabled={taskActive} onClick={() => void syncContent(account, lastSyncMode, false)}>继续同步</Button> : null}</Space>
            : !profile ? <Button size="small" type="primary" ghost icon={<PlusOutlined />} onClick={() => openProfile(account)}>建立档案</Button>
            : null;
        const statusAlertKey = `${account.id}:${operationTask?.id || "summary"}:${summary?.sync.finished_at || ""}:${summary?.sync.status || ""}:${summary?.sync.error || ""}`;
        const statusAlertDismissed = dismissedStatusAlerts.has(statusAlertKey);
        return <Col xs={24} md={12} lg={8} key={account.id}><Card
          title={<Space size={8}><span>{account.nickname || `账号 #${account.id}`}</span>{profile ? <Tag color={profile.status === "active" ? "green" : "default"}>{profile.status === "active" ? "规划中" : "已暂停"}</Tag> : <Tag>待设置</Tag>}</Space>}
          extra={<Space size={4}><Button size="small" type="primary" loading={syncing.has(account.id) || (taskActive && operationTask?.task_type === "account_content_sync")} disabled={taskActive} icon={<SyncOutlined />} onClick={() => void syncContent(account, "full", false)}>同步内容</Button><Dropdown trigger={["click"]} menu={{ items: [
            { key: "quick", label: "快速同步（最近 100 篇）", icon: <SyncOutlined />, disabled: taskActive, onClick: () => void syncContent(account, "quick", false) },
            { key: "full", label: "完整同步（最近 500 篇）", icon: <SyncOutlined />, disabled: taskActive, onClick: () => void syncContent(account, "full", false) },
            { key: "rebuild", label: "强制重建（清空后同步 500 篇）", danger: true, icon: <DeleteOutlined />, disabled: taskActive, onClick: () => { Modal.confirm({ title: "清空后重新同步该账号？", content: "会删除该账号已保存的内容，再读取最近 500 篇。", okText: "确认重建", okButtonProps: { danger: true }, cancelText: "取消", onOk: () => syncContent(account, "rebuild", false) }); } },
            { key: "analyze", label: "基于现有内容开始分析", icon: <ExperimentOutlined />, disabled: taskActive, onClick: () => void analyze(account) },
            { key: "profile", label: "编辑运营档案", icon: <EditOutlined />, onClick: () => openProfile(account) },
            { type: "divider" },
            { key: "clear", label: "清空内容库", danger: true, icon: <DeleteOutlined />, disabled: taskActive, onClick: () => { Modal.confirm({ title: "清空该账号全部内容库笔记？", content: "将删除该账号全部内容库笔记及评论/图片，不可恢复；清空后请重新同步最新内容。", okText: "确认清空", okButtonProps: { danger: true }, cancelText: "取消", onOk: () => clearContent(account) }); } },
          ] }}><Button size="small" icon={<MoreOutlined />} aria-label="更多操作" /></Dropdown></Space>}
        >
          {!statusAlertDismissed ? <Alert
            type={statusType}
            showIcon
            closable={!taskActive}
            onClose={() => {
              if (taskActive) return;
              setDismissedStatusAlerts((current) => new Set(current).add(statusAlertKey));
            }}
            message={<Space size={8}><Text strong>{statusTitle}</Text>{operationTask?.status === "completed" ? <Tag color="green">完成</Tag> : null}</Space>}
            description={<Space direction="vertical" size={6} style={{ width: "100%" }}>
              <Text type={taskFailed || partialSync ? "warning" : "secondary"}>{statusDescription}</Text>
              {taskActive ? <Progress percent={operationTask?.progress ?? 0} status="active" size="small" /> : null}
              {!taskActive && hasSyncIssue ? <Collapse
                size="small"
                ghost
                items={[{ key: "technical-details", label: "查看技术详情", children: <Text code style={{ display: "block", whiteSpace: "pre-wrap", overflowWrap: "anywhere", maxHeight: 180, overflow: "auto" }}>{rawSyncError}</Text> }]}
              /> : null}
            </Space>}
            action={statusAction}
            style={{ marginBottom: 14 }}
          /> : null}
          <Row gutter={[8, 8]}>
            <Col span={8}><div><Text type="secondary" style={{ fontSize: 12 }}>内容库</Text><div><Text strong>{summary?.sync.note_count ?? "—"}</Text><Text type="secondary"> 篇</Text></div></div></Col>
            <Col span={8}><div><Text type="secondary" style={{ fontSize: 12 }}>今日计划</Text><div><Text strong>{summary?.execution.today_plan_count ?? 0}</Text><Text type="secondary"> 条</Text></div></div></Col>
            <Col span={8}><div><Text type="secondary" style={{ fontSize: 12 }}>自动任务</Text><div><Text strong>{summary?.execution.auto_task_active_count ?? 0}</Text><Text type="secondary"> 启用</Text></div></div></Col>
          </Row>
          <Divider style={{ margin: "14px 0 12px" }} />
          <Space direction="vertical" size={4} style={{ width: "100%" }}>
            <Text type="secondary">{summary?.sync.finished_at ? `最近同步 ${dayjs(summary.sync.finished_at).format("MM-DD HH:mm")}` : "尚未同步内容"}{summary?.sync.status === "failed" ? " · 同步失败" : summary?.sync.status === "completed" ? " · 同步完成" : ""}</Text>
            <Text type="secondary">分析模型：{summary?.analysis.model?.name || "尚未确定"}</Text>
            {summary?.sync.total !== undefined ? <Text type="secondary">最近一次：{syncModeLabels[summary.sync.sync_mode || ""] || "同步"}，读取 {summary.sync.scanned_count ?? summary.sync.total} 篇，新增 {summary.sync.remote_saved ?? 0} 篇{summary.sync.target_count ? ` / 目标 ${summary.sync.target_count} 篇` : ""}{summary.sync.scroll_count ? `，翻页 ${summary.sync.scroll_count} 次` : ""}</Text> : null}
            {summary?.sync.error ? <Text type="warning">同步提示：{isTimeoutError(summary.sync.error) ? "读取账号内容超时，已保留已读取内容" : "同步未完整完成，已保留已读取内容"}</Text> : null}
            {profile ? <><Text>{profile.positioning || "未填写账号定位"}</Text><Text type="secondary">每日生成 {profile.daily_generate_count} 篇 · 计划发布 {profile.daily_publish_count} 篇 · {profile.review_required ? "发布前需审核" : "可自动执行"}</Text><Text type="secondary" ellipsis={{ tooltip: profile.content_directions.join(" / ") || "未设内容方向" }}>内容方向：{profile.content_directions.join(" / ") || "未设内容方向"}</Text></> : null}
          </Space>
        </Card></Col>;
      })}
    </Row>
    <Card style={{ marginTop: 24 }} title={<Space><CalendarOutlined />7 天内容排期</Space>} extra={<Space wrap><DatePicker value={date} onChange={(value) => { if (!value) return; setDate(value); setWeekStart(value.startOf("day")); }} /><Button size="small" disabled={isToday} onClick={() => { setDate(dayjs()); setWeekStart(dayjs().startOf("day")); }}>回到今天</Button><Button loading={loading} onClick={() => void generate(date)}>只生成当天</Button><Button type="primary" loading={loading} onClick={generateWeek}>从所选日期生成 7 天</Button><Button loading={draftingAll} disabled={pendingWeekPlans.length === 0 || loading} onClick={() => void generateAllDrafts}>生成 7 天草稿（不发布）</Button></Space>}>
      <Row gutter={[10, 10]} style={{ marginBottom: 20 }}>
        {weekDays.map((day) => {
          const dayKey = day.format("YYYY-MM-DD");
          const dayPlans = weekPlans.filter((item) => item.plan_date === dayKey);
          const drafted = dayPlans.filter((item) => Boolean(item.draft_id)).length;
          const published = dayPlans.filter((item) => item.status === "published").length;
          const selected = day.isSame(date, "day");
          return <Col xs={12} sm={8} md={Math.floor(24 / 7)} key={dayKey}>
            <Card size="small" hoverable onClick={() => setDate(day)} style={{
              cursor: "pointer", height: "100%", borderColor: selected ? "#1677ff" : undefined,
              background: selected ? "#e6f4ff" : undefined,
              boxShadow: selected ? "0 0 0 1px #1677ff" : undefined,
            }}>
              <Space direction="vertical" size={2} style={{ width: "100%" }}>
                <Text strong={selected} style={{ color: selected ? "#0f3d68" : undefined }}>{weekdayLabels[day.day()]}</Text>
                <Text strong style={{ fontSize: 18, color: selected ? "#0f3d68" : undefined }}>{day.format("MM/DD")}</Text>
                <Text style={{ color: selected ? "#173f5f" : undefined }}>{dayPlans.length} 条排期</Text>
                <Text style={{ fontSize: 12, color: selected ? "#52677d" : undefined }}>草稿 {drafted} · 已发布 {published}</Text>
              </Space>
            </Card>
          </Col>;
        })}
      </Row>
      <Divider plain style={{ margin: "8px 0 16px", textAlign: "left" }}>{date.format("YYYY年MM月DD日 dddd")} · 当日详情</Divider>
      <Form form={planForm} component={false} onFinish={(values) => void savePlan(values)}><Table rowKey="id" dataSource={visiblePlans} pagination={false} scroll={{ x: accountFilter ? 680 : 820 }} locale={{ emptyText: "所选日期暂无排期，可点击上方按钮生成计划" }} columns={[
        ...(!accountFilter ? [{ title: "账号", dataIndex: "platform_account_id", width: 130, render: (id: number) => accountMap.get(id)?.nickname || `#${id}` }] : []),
        { title: "时间", dataIndex: "planned_at", width: 120, render: (value, record: OperationPlan) => editingPlan?.id === record.id ? <Form.Item name="planned_at" noStyle><DatePicker showTime format="MM-DD HH:mm" style={{ width: 115 }} /></Form.Item> : value ? dayjs(value).format("HH:mm") : "-" },
        { title: "内容方向", dataIndex: "content_direction", width: 150, render: (value, record: OperationPlan) => editingPlan?.id === record.id ? <Form.Item name="content_direction" noStyle rules={[{ required: true, message: "请输入内容方向" }]}><Input /></Form.Item> : value },
        { title: "选题", dataIndex: "topic", width: 260, render: (value, record: OperationPlan) => editingPlan?.id === record.id ? <Form.Item name="topic" noStyle rules={[{ required: true, message: "请输入选题" }]}><Input /></Form.Item> : value },
        { title: "状态", dataIndex: "status", width: 135, render: (value, record: OperationPlan) => editingPlan?.id === record.id ? <Form.Item name="status" noStyle><Select style={{ width: 120 }} options={Object.entries(planStatusLabels).map(([itemValue, label]) => ({ value: itemValue, label }))} /></Form.Item> : <Tag color={planStatusColors[value] || "blue"}>{planStatusLabels[value] || value}</Tag> },
        { title: "操作", key: "actions", fixed: "right", width: 210, render: (_: unknown, record: OperationPlan) => editingPlan?.id === record.id ? <Space><Button size="small" type="primary" htmlType="submit" loading={planSaving}>保存</Button><Button size="small" onClick={() => setEditingPlan(null)}>取消</Button></Space> : <Space size={4}><Button size="small" icon={<EditOutlined />} disabled={Boolean(editingPlan) || draftingAll} onClick={() => openPlanEditor(record)}>编辑</Button>{record.draft_id ? <Tag color="blue">已关联草稿</Tag> : record.status === "pending" ? <Button size="small" type="link" loading={draftingPlanIds.has(record.id)} disabled={draftingAll} onClick={() => void generateDraft(record)}>生成草稿</Button> : <Text type="secondary">暂无草稿</Text>}</Space> },
      ]} /></Form>
    </Card>
    <Modal title={`运营档案 · ${editing?.nickname || ""}`} open={Boolean(editing)} onCancel={() => setEditing(null)} onOk={() => form.submit()} width={720}>
      <Form form={form} layout="vertical" onFinish={(values) => void save(values)}>
        <Form.Item label="小红书主页链接" name="homepage_url" tooltip="安卓账号会通过绑定手机读取主页内容，请确认手机在线且当前分身账号正确；PC 账号可填写公开主页链接"><Input placeholder="https://www.xiaohongshu.com/user/profile/..." /></Form.Item>
        <Row gutter={16}><Col span={12}><Form.Item label="账号定位" name="positioning"><Input.TextArea rows={2} placeholder="例：某类产品测评与使用分享" /></Form.Item></Col><Col span={12}><Form.Item label="目标人群" name="target_audience"><Input.TextArea rows={2} placeholder="例：对该主题感兴趣的用户" /></Form.Item></Col></Row>
        <Form.Item label="内容方向（每行一个）" name="content_directions"><Input.TextArea rows={3} placeholder={"产品特点分享\n使用方法\n真实体验记录"} /></Form.Item>
        <Row gutter={16}><Col span={12}><Form.Item label="每日生成草稿" name="daily_generate_count"><InputNumber min={1} max={10} style={{ width: "100%" }} /></Form.Item></Col><Col span={12}><Form.Item label="每日计划发布" name="daily_publish_count"><InputNumber min={1} max={10} style={{ width: "100%" }} /></Form.Item></Col></Row>
        <Form.Item label="发布时间（逗号分隔）" name="preferred_times"><Input placeholder="10:00, 18:00, 20:30" /></Form.Item>
        <Form.Item label="阶段目标（每行一个）" name="goals"><Input.TextArea rows={2} /></Form.Item>
        <Form.Item label="内容语气" name="tone"><Input placeholder="自然、专业、不过度承诺" /></Form.Item>
        <Form.Item label="禁用话题/表达（每行一个）" name="forbidden_topics"><Input.TextArea rows={2} /></Form.Item>
        <Row gutter={16}><Col span={12}><Form.Item label="人工审核" name="review_required" valuePropName="checked"><Switch checkedChildren="需要" unCheckedChildren="不需要" /></Form.Item></Col><Col span={12}><Form.Item label="状态" name="status"><Select options={[{ value: "active", label: "启用规划" }, { value: "paused", label: "暂停规划" }]} /></Form.Item></Col></Row>
      </Form>
    </Modal>
    <Modal
      title={<Space><ExperimentOutlined />AI账号运营方案（待确认）</Space>}
      open={Boolean(reviewing)} width={900} onCancel={() => setReviewing(null)}
      footer={<Space><Button danger loading={deciding} onClick={() => void reject()}>拒绝，不应用</Button><Button type="primary" icon={<CheckOutlined />} loading={deciding} onClick={() => void approve()}>确认并保存</Button></Space>}
    >
      {reviewing ? <>
        <Alert
          type={reviewing.analysis_error || reviewing.strategy.analysis_source === "rules" ? "warning" : "info"}
          showIcon
          message={reviewing.analysis_error || reviewing.strategy.analysis_source === "rules" ? "通用建议，应用前请确认账号定位" : `仅基于当前账号 ${reviewing.source_note_count} 篇同步内容分析`}
          description={reviewing.analysis_error || reviewing.strategy.summary}
        />
        <Alert
          type="info"
          showIcon
          message="确认范围"
          description={<Space direction="vertical" size={4}>
            <Text>将保存运营档案、学习结论和未来 7 天内容排期。</Text>
            <Space><Switch checked={createAutoTasks} onChange={setCreateAutoTasks} /> <Text>同时创建自动运营任务（默认暂停，不会自动发布）</Text></Space>
          </Space>}
          style={{ marginTop: 12 }}
        />
        <Descriptions bordered column={2} size="small" style={{ marginTop: 16 }}>
          <Descriptions.Item label="账号定位">{reviewing.strategy.positioning}</Descriptions.Item>
          <Descriptions.Item label="目标人群">{reviewing.strategy.target_audience}</Descriptions.Item>
          <Descriptions.Item label="每日生成/发布">
            <Space>
              <InputNumber min={1} max={3} value={reviewing.strategy.daily_generate_count ?? reviewing.strategy.daily_post_count ?? 1} onChange={(value) => setReviewing({ ...reviewing, strategy: { ...reviewing.strategy, daily_generate_count: Number(value || 1) } })} />
              <Text>/</Text>
              <InputNumber min={1} max={reviewing.strategy.daily_generate_count ?? reviewing.strategy.daily_post_count ?? 1} value={reviewing.strategy.daily_publish_count ?? reviewing.strategy.daily_post_count ?? 1} onChange={(value) => setReviewing({ ...reviewing, strategy: { ...reviewing.strategy, daily_publish_count: Number(value || 1), daily_post_count: Number(value || 1) } })} />
              <Text>篇</Text>
            </Space>
          </Descriptions.Item>
          <Descriptions.Item label="发布时间">{reviewing.strategy.preferred_times?.join("、")}</Descriptions.Item>
          <Descriptions.Item label="内容方向" span={2}>{reviewing.strategy.content_directions?.map((item) => <Tag key={item}>{item}</Tag>)}</Descriptions.Item>
          <Descriptions.Item label="内容语气" span={2}>{reviewing.strategy.tone}</Descriptions.Item>
        </Descriptions>
        <Divider>账号诊断</Divider>
        <List size="small" dataSource={reviewing.strategy.diagnosis || []} renderItem={(item) => <List.Item>{item}</List.Item>} />
        <Divider>自动学习结论</Divider>
        <List size="small" dataSource={reviewing.strategy.learning_insights || []} renderItem={(item) => <List.Item>
          <List.Item.Meta
            title={<Space wrap><Text strong>{item.title}</Text><Tag>{Math.round((item.confidence || 0) * 100)}% 置信度</Tag></Space>}
            description={<Space direction="vertical" size={3}>
              <Text>{item.conclusion}</Text>
              {item.evidence_summary ? <Text type="secondary">证据：{item.evidence_summary}</Text> : null}
              {item.evidence_note_ids?.length ? <Text type="secondary">关联笔记：{item.evidence_note_ids.join("、")}</Text> : null}
              {item.recommended_action ? <Text type="success">建议动作：{item.recommended_action}</Text> : null}
            </Space>}
          />
        </List.Item>} />
        <Divider>未来7天选题</Divider>
        <Table size="small" pagination={false} scroll={{ x: 900 }} rowKey={(_, index) => String(index)} dataSource={reviewing.strategy.seven_day_topics || []} columns={[
          { title: "日期", dataIndex: "date", width: 105, render: (value) => value || "按计划日期" },
          { title: "内容方向", dataIndex: "direction", width: 130 },
          { title: "具体选题", dataIndex: "topic", width: 180 },
          { title: "切入角度", dataIndex: "angle", width: 160, render: (value) => value || "—" },
          { title: "标题方向", dataIndex: "title_idea", width: 180, render: (value) => value || "—" },
          { title: "带货相关", dataIndex: "product_relevance", width: 110, render: (value) => value === undefined ? "—" : typeof value === "boolean" ? <Tag color={value ? "gold" : "default"}>{value ? "适合" : "不适合"}</Tag> : value },
          { title: "人工审核", dataIndex: "review_required", width: 100, render: (value) => value === undefined ? "—" : <Tag color={value ? "orange" : "green"}>{value ? "需要" : "无需"}</Tag> },
        ]} />
      </> : null}
    </Modal>
  </div>;
}
