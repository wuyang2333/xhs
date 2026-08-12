import {
  CheckCircleOutlined,
  ClockCircleOutlined,
  DeleteOutlined,
  EditOutlined,
  PauseCircleOutlined,
  PlayCircleOutlined,
  PlusOutlined,
  ReloadOutlined,
  ThunderboltOutlined,
} from "@ant-design/icons";
import {
  Alert,
  Button,
  Card,
  Checkbox,
  Col,
  Descriptions,
  Empty,
  Form,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Row,
  Select,
  Space,
  Spin,
  Statistic,
  Tag,
  Typography,
} from "antd";
import { useEffect, useState } from "react";

import { PageHeader } from "../../../components/layout/app-shell";
import {
  createAutoTask,
  deleteAutoTask,
  fetchAccounts,
  fetchAutoTasks,
  parseAutoTaskDescription,
  runAutoTask,
  updateAutoTask,
} from "../../../lib/api";
import { formatShanghaiTime } from "../../../lib/time";
import type {
  AutoTask,
  AutoTaskInstructionCandidate,
  AutoTaskMatchedAccount,
  AutoTaskRunResult,
  PlatformAccount,
} from "../../../types";

const { Text, Paragraph } = Typography;
const { TextArea } = Input;

const STATUS_CONFIG: Record<string, { color: string; label: string }> = {
  active: { color: "green", label: "运行中" },
  paused: { color: "default", label: "已暂停" },
  completed: { color: "blue", label: "已完成" },
};

function getStatusTag(s: string) {
  const cfg = STATUS_CONFIG[s] ?? { color: "default", label: s };
  return <Tag color={cfg.color}>{cfg.label}</Tag>;
}

const panelStyle: React.CSSProperties = {
  background: "#1a1a1a",
  borderRadius: 8,
  border: "1px solid #303030",
};

const cardBodyStyle: React.CSSProperties = {
  padding: 16,
};

export function AutoOpsPage() {
  const [tasks, setTasks] = useState<AutoTask[]>([]);
  const [pcAccounts, setPcAccounts] = useState<PlatformAccount[]>([]);
  const [creatorAccounts, setCreatorAccounts] = useState<PlatformAccount[]>([]);
  const [accountFilter, setAccountFilter] = useState<number | undefined>();
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  // Create form state
  const [showCreate, setShowCreate] = useState(false);
  const [createName, setCreateName] = useState("");
  const [createKeywords, setCreateKeywords] = useState("");
  const [createPcAccountId, setCreatePcAccountId] = useState<number | null>(null);
  const [createCreatorAccountId, setCreateCreatorAccountId] = useState<number | null>(null);
  const [createInstruction, setCreateInstruction] = useState("");
  const [createScheduleType, setCreateScheduleType] = useState<string>("manual");
  const [createScheduleTime, setCreateScheduleTime] = useState<string>("09:00");
  const [createScheduleDays, setCreateScheduleDays] = useState<string>("");
  const [createIntervalHours, setCreateIntervalHours] = useState<number>(24);
  const [isCreating, setIsCreating] = useState(false);

  // 一句话生成任务（C-03）。解析结果只填表单，绝不直接创建——发布不可撤回。
  const [aiDescription, setAiDescription] = useState("");
  const [isParsing, setIsParsing] = useState(false);
  const [parseWarnings, setParseWarnings] = useState<string[]>([]);
  const [parsedAccounts, setParsedAccounts] = useState<AutoTaskMatchedAccount[]>([]);
  const [instructionCandidates, setInstructionCandidates] = useState<AutoTaskInstructionCandidate[]>([]);
  const [hasPendingProposal, setHasPendingProposal] = useState(false);

  // Edit modal state
  const [editTask, setEditTask] = useState<AutoTask | null>(null);
  const [editName, setEditName] = useState("");
  const [editKeywords, setEditKeywords] = useState("");
  const [editInstruction, setEditInstruction] = useState("");
  const [editScheduleType, setEditScheduleType] = useState("manual");
  const [editScheduleTime, setEditScheduleTime] = useState("09:00");
  const [editScheduleDays, setEditScheduleDays] = useState("");
  const [editIntervalHours, setEditIntervalHours] = useState(24);
  const [isSaving, setIsSaving] = useState(false);

  // Run state
  const [runningTaskId, setRunningTaskId] = useState<number | null>(null);
  const [lastRunResult, setLastRunResult] = useState<AutoTaskRunResult | null>(null);

  function parseKeywords(text: string): string[] {
    return text
      .split("\n")
      .map((k) => k.trim())
      .filter(Boolean);
  }

  async function loadData() {
    setIsLoading(true);
    setError(null);
    try {
      const [tasksRes, accountsRes] = await Promise.all([
        fetchAutoTasks(),
        fetchAccounts("xhs"),
      ]);
      setTasks(tasksRes.items);
      setPcAccounts(accountsRes.filter((a) => a.sub_type === "pc"));
      setCreatorAccounts(accountsRes.filter((a) => a.sub_type === "creator" || a.sub_type === "android"));
    } catch {
      setError("加载自动运营任务失败。");
    } finally {
      setIsLoading(false);
    }
  }

  useEffect(() => {
    void loadData();
  }, []);

  /**
   * 解析一句话，把建议值填进**现有的创建表单**（不另做一套表单，否则两处必然漂移）。
   * 这里只填表单，不调 createAutoTask——用户必须逐项复核后自己点「创建任务」。
   * 留空的字段保持表单原值为空，让用户自己填，绝不用猜测值补上。
   */
  async function handleParseDescription() {
    const description = aiDescription.trim();
    if (!description) {
      setError("请先用一句话描述你想做的自动运营任务。");
      return;
    }
    setIsParsing(true);
    setError(null);
    setMessage(null);
    setParseWarnings([]);
    setParsedAccounts([]);
    try {
      const result = await parseAutoTaskDescription({ description });
      const { proposal } = result;
      setShowCreate(true);
      setCreateName(proposal.name);
      setCreateKeywords(proposal.keywords.join("\n"));
      setCreateInstruction(proposal.ai_instruction);
      setCreatePcAccountId(proposal.pc_account_id);
      setCreateCreatorAccountId(proposal.creator_account_id);
      setCreateScheduleType(proposal.schedule_type || "manual");
      setCreateScheduleTime(proposal.schedule_time || "");
      setCreateScheduleDays(proposal.schedule_days);
      if (proposal.schedule_interval_hours !== null) {
        setCreateIntervalHours(proposal.schedule_interval_hours);
      }
      setParseWarnings(result.warnings);
      setParsedAccounts(result.matched_accounts);
      setInstructionCandidates(result.instruction_candidates);
      setHasPendingProposal(true);
      setMessage("AI 已生成配置建议，请逐项核对后再点「创建任务」。任务尚未创建。");
    } catch (err: unknown) {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      setError(`解析失败：${detail || "请检查默认文本模型配置。"}`);
    } finally {
      setIsParsing(false);
    }
  }

  async function handleCreate() {
    const keywords = parseKeywords(createKeywords);
    // 采集账号非必填：不选就跳过抓取，直接按关键词让 AI 生成。
    if (!createName.trim() || keywords.length === 0 || !createCreatorAccountId) {
      setError("请填写任务名称、至少一个关键词，并选择发布账号。");
      return;
    }
    // 定时任务必须有明确时间。留空提交会被后端静默当成 09:00——那正是「悄悄用猜测值」。
    if (
      (createScheduleType === "daily" || createScheduleType === "weekly") &&
      !/^([01]\d|2[0-3]):([0-5]\d)$/.test(createScheduleTime.trim())
    ) {
      setError("定时任务需要填写 HH:MM 格式的执行时间。");
      return;
    }
    if (createScheduleType === "weekly" && !createScheduleDays) {
      setError("每周定时需要至少勾选一个执行日期。");
      return;
    }
    setIsCreating(true);
    setError(null);
    setMessage(null);
    try {
      const created = await createAutoTask({
        name: createName.trim(),
        keywords,
        pc_account_id: createPcAccountId,
        creator_account_id: createCreatorAccountId,
        ai_instruction: createInstruction,
        schedule_type: createScheduleType as "manual" | "daily" | "weekly" | "interval",
        schedule_time: createScheduleTime,
        schedule_days: createScheduleDays,
        schedule_interval_hours: createIntervalHours,
      });
      setTasks((prev) => [created, ...prev]);
      setShowCreate(false);
      setCreateName("");
      setCreateKeywords("");
      setCreatePcAccountId(null);
      setCreateCreatorAccountId(null);
      setCreateInstruction("");
      setCreateScheduleType("manual");
      setCreateScheduleTime("09:00");
      setCreateScheduleDays("");
      setCreateIntervalHours(24);
      setAiDescription("");
      setParseWarnings([]);
      setParsedAccounts([]);
      setHasPendingProposal(false);
      setMessage(`自动任务"${created.name}"已创建。`);
    } catch {
      setError("创建自动任务失败。");
    } finally {
      setIsCreating(false);
    }
  }

  async function handleToggleStatus(task: AutoTask) {
    const newStatus = task.status === "active" ? "paused" : "active";
    setError(null);
    setMessage(null);
    try {
      const updated = await updateAutoTask(task.id, { status: newStatus });
      setTasks((prev) => prev.map((t) => (t.id === updated.id ? updated : t)));
      setMessage(`任务"${updated.name}"已${newStatus === "active" ? "恢复" : "暂停"}。`);
    } catch {
      setError("更新任务状态失败。");
    }
  }

  async function handleDelete(taskId: number) {
    setError(null);
    setMessage(null);
    try {
      await deleteAutoTask(taskId);
      setTasks((prev) => prev.filter((t) => t.id !== taskId));
      setMessage("自动任务已删除。");
    } catch {
      setError("删除自动任务失败。");
    }
  }

  async function handleRun(task: AutoTask) {
    setRunningTaskId(task.id);
    setError(null);
    setMessage(null);
    setLastRunResult(null);
    try {
      const result = await runAutoTask(task.id);
      setLastRunResult(result);
      setTasks((prev) => prev.map((t) => (t.id === result.auto_task.id ? result.auto_task : t)));
      setMessage(
        `任务"${task.name}"执行完成 -- 关键词: ${result.keyword}, 来源笔记: ${result.source_note.title}, 已创建发布任务 #${result.publish_job.id}。`
      );
    } catch (err: unknown) {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      setError(`执行失败：${detail || "请检查账号和模型配置。"}`);
    } finally {
      setRunningTaskId(null);
    }
  }

  function openEdit(task: AutoTask) {
    setEditTask(task);
    setEditName(task.name);
    setEditKeywords((task.keywords || []).join("\n"));
    setEditInstruction(task.ai_instruction);
    setEditScheduleType(task.schedule_type || "manual");
    setEditScheduleTime(task.schedule_time || "09:00");
    setEditScheduleDays(task.schedule_days || "");
    setEditIntervalHours(task.schedule_interval_hours || 24);
  }

  async function handleSaveEdit() {
    if (!editTask) return;
    setIsSaving(true);
    setError(null);
    setMessage(null);
    try {
      const keywords = parseKeywords(editKeywords);
      const updated = await updateAutoTask(editTask.id, {
        name: editName.trim() || undefined,
        keywords: keywords.length > 0 ? keywords : undefined,
        ai_instruction: editInstruction,
        schedule_type: editScheduleType as "manual" | "daily" | "weekly" | "interval",
        schedule_time: editScheduleTime,
        schedule_days: editScheduleDays,
        schedule_interval_hours: editIntervalHours,
      });
      setTasks((prev) => prev.map((t) => (t.id === updated.id ? updated : t)));
      setEditTask(null);
      setMessage(`任务"${updated.name}"已更新。`);
    } catch {
      setError("更新任务失败。");
    } finally {
      setIsSaving(false);
    }
  }

  function scheduleDesc(task: AutoTask): string {
    if (task.schedule_type === "daily") return `每日 ${task.schedule_time}`;
    if (task.schedule_type === "weekly") {
      const dayMap: Record<string, string> = {"1":"一","2":"二","3":"三","4":"四","5":"五","6":"六","7":"日"};
      const days = (task.schedule_days || "").split(",").map(d => dayMap[d] || d).join("、");
      return `每周${days} ${task.schedule_time}`;
    }
    if (task.schedule_type === "interval") return `每 ${task.schedule_interval_hours} 小时`;
    return "手动触发";
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <PageHeader
        eyebrow="XHS Auto Operations"
        title="自动运营"
        description="设置关键词、自动抓取热门笔记、AI 改写后自动创建发布任务，实现全自动内容生产管线。"
        action={
          <Button icon={<ReloadOutlined />} onClick={loadData} loading={isLoading}>
            刷新
          </Button>
        }
      />
      {/* 一句话生成任务：入口直接放在页面顶部，不进弹窗也不进折叠面板。
          此前功能被藏在弹窗里，用户两轮反馈「没看到」。 */}
      <Card
        title={
          <Space>
            <ThunderboltOutlined style={{ color: "#faad14" }} />
            <span>一句话生成任务</span>
          </Space>
        }
        style={panelStyle}
        styles={{ body: cardBodyStyle, header: { borderBottom: "1px solid #303030" } }}
      >
        <Paragraph type="secondary" style={{ fontSize: 12, marginBottom: 8 }}>
          描述你想做的自动运营任务，AI 会生成配置建议并填入下方创建表单。
          <Text type="warning"> AI 不会直接创建任务，你必须逐项核对后手动确认。</Text>
        </Paragraph>
        <Space.Compact style={{ width: "100%" }}>
          <TextArea
            placeholder="如：每天早上九点找护腰相关的热门笔记，改写成口语化的种草文，用医麦德护具店账号发"
            value={aiDescription}
            onChange={(e) => setAiDescription(e.target.value)}
            rows={2}
            maxLength={1000}
            disabled={isParsing}
          />
        </Space.Compact>
        <Button
          type="primary"
          icon={<ThunderboltOutlined />}
          onClick={handleParseDescription}
          loading={isParsing}
          disabled={!aiDescription.trim()}
          style={{ marginTop: 12 }}
          block
        >
          {isParsing ? "AI 解析中，可能需要十几秒..." : "AI 生成任务"}
        </Button>
      </Card>

      {parseWarnings.length > 0 && (
        <Alert
          type="warning"
          showIcon
          message="以下内容 AI 无法确定或需要你确认，请手动核对"
          description={
            <ul style={{ margin: 0, paddingLeft: 20 }}>
              {parseWarnings.map((warning) => (
                <li key={warning}>
                  <Text style={{ fontSize: 12 }}>{warning}</Text>
                </li>
              ))}
            </ul>
          }
          closable
          onClose={() => setParseWarnings([])}
        />
      )}

      <Select allowClear value={accountFilter} onChange={setAccountFilter} placeholder="选择账号查看自动运营策略" style={{ width: 300 }} options={creatorAccounts.map((item) => ({ value: item.id, label: item.nickname || `#${item.id}` }))} />

      {error && (
        <Alert type="error" message={error} showIcon closable onClose={() => setError(null)} />
      )}
      {message && (
        <Alert type="success" message={message} showIcon closable onClose={() => setMessage(null)} />
      )}

      {/* Task List */}
      {isLoading ? (
        <Card style={panelStyle} styles={{ body: cardBodyStyle }}>
          <div style={{ textAlign: "center", padding: 48 }}>
            <Spin size="large" />
            <Paragraph style={{ color: "#8c8c8c", marginTop: 16 }}>正在加载自动运营任务...</Paragraph>
          </div>
        </Card>
      ) : tasks.filter((task) => !accountFilter || task.creator_account_id === accountFilter).length === 0 && !showCreate ? (
        <Card style={panelStyle} styles={{ body: cardBodyStyle }}>
          <Empty
            image={<ThunderboltOutlined style={{ fontSize: 48, color: "#8c8c8c" }} />}
            imageStyle={{ height: 64 }}
            description={
              <div>
                <Text strong style={{ fontSize: 16 }}>
                  暂无自动运营任务
                </Text>
                <br />
                <Text type="secondary">点击"新建任务"开始配置关键词自动抓取、AI 改写和发布管线。</Text>
              </div>
            }
          />
        </Card>
      ) : (
        <Row gutter={[16, 16]}>
          {tasks.filter((task) => !accountFilter || task.creator_account_id === accountFilter).map((task) => (
            <Col xs={24} md={12} xl={8} key={task.id}>
              <Card
                style={panelStyle}
                styles={{ body: cardBodyStyle, header: { borderBottom: "1px solid #303030" } }}
                title={
                  <Space>
                    <ThunderboltOutlined style={{ color: task.status === "active" ? "#52c41a" : "#8c8c8c" }} />
                    <Text ellipsis style={{ maxWidth: 180 }}>
                      {task.name}
                    </Text>
                  </Space>
                }
                extra={getStatusTag(task.status)}
              >
                {/* Keywords */}
                <div style={{ marginBottom: 12 }}>
                  <Text type="secondary" style={{ fontSize: 12, display: "block", marginBottom: 4 }}>
                    关键词
                  </Text>
                  <Space size={4} wrap>
                    {(task.keywords || []).map((kw) => (
                      <Tag key={kw} color="blue">
                        {kw}
                      </Tag>
                    ))}
                  </Space>
                </div>

                {/* Stats */}
                <Row gutter={16} style={{ marginBottom: 12 }}>
                  <Col span={8}>
                    <Statistic
                      title="已生成"
                      value={task.total_published}
                      valueStyle={{ fontSize: 20, color: "#e8e8e8" }}
                    />
                  </Col>
                </Row>

                {/* Time info */}
                <Space direction="vertical" size={2} style={{ width: "100%", marginBottom: 12 }}>
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    <ClockCircleOutlined style={{ marginRight: 4 }} />
                    上次运行：{formatShanghaiTime(task.last_run_at)}
                  </Text>
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    <ClockCircleOutlined style={{ marginRight: 4 }} />
                    下次运行：{formatShanghaiTime(task.next_run_at)}
                  </Text>
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    创建时间：{formatShanghaiTime(task.created_at)}
                  </Text>
                </Space>

                {/* AI instruction preview */}
                {task.ai_instruction && (
                  <div style={{ marginBottom: 12 }}>
                    <Text type="secondary" style={{ fontSize: 12, display: "block", marginBottom: 2 }}>
                      AI 指令
                    </Text>
                    <Paragraph
                      type="secondary"
                      ellipsis={{ rows: 2 }}
                      style={{ fontSize: 12, marginBottom: 0 }}
                    >
                      {task.ai_instruction}
                    </Paragraph>
                  </div>
                )}

                {/* Schedule info */}
                <Text type="secondary" style={{ fontSize: 12, display: "block", marginTop: 4 }}>
                  调度：{scheduleDesc(task)}
                </Text>
                {task.next_run_at && (
                  <Text type="secondary" style={{ fontSize: 11, display: "block" }}>
                    下次执行：{formatShanghaiTime(task.next_run_at)}
                  </Text>
                )}

                {/* Actions */}
                <Space wrap style={{ marginTop: 8 }}>
                  <Button
                    type="primary"
                    size="small"
                    icon={<PlayCircleOutlined />}
                    onClick={() => handleRun(task)}
                    loading={runningTaskId === task.id}
                    disabled={runningTaskId !== null && runningTaskId !== task.id}
                  >
                    立即执行
                  </Button>
                  <Button
                    size="small"
                    icon={task.status === "active" ? <PauseCircleOutlined /> : <PlayCircleOutlined />}
                    onClick={() => handleToggleStatus(task)}
                  >
                    {task.status === "active" ? "暂停" : "恢复"}
                  </Button>
                  <Button
                    size="small"
                    icon={<EditOutlined />}
                    onClick={() => openEdit(task)}
                  >
                    编辑
                  </Button>
                  <Popconfirm
                    title="确认删除此自动任务？"
                    onConfirm={() => handleDelete(task.id)}
                    okText="删除"
                    cancelText="取消"
                  >
                    <Button size="small" danger icon={<DeleteOutlined />}>
                      删除
                    </Button>
                  </Popconfirm>
                </Space>
              </Card>
            </Col>
          ))}
        </Row>
      )}

      {/* Last Run Result */}
      {lastRunResult && (
        <Card
          title={
            <Space>
              <CheckCircleOutlined style={{ color: "#52c41a" }} />
              <span>最近一次执行结果</span>
            </Space>
          }
          style={panelStyle}
          styles={{ body: cardBodyStyle, header: { borderBottom: "1px solid #303030" } }}
        >
          <Descriptions column={{ xs: 1, md: 2, lg: 4 }} size="small">
            <Descriptions.Item label="关键词">{lastRunResult.keyword}</Descriptions.Item>
            <Descriptions.Item label="来源笔记">{lastRunResult.source_note.title}</Descriptions.Item>
            <Descriptions.Item label="互动量">
              {lastRunResult.source_note.likes + lastRunResult.source_note.collects + lastRunResult.source_note.comments}
            </Descriptions.Item>
            <Descriptions.Item label="发布任务">#{lastRunResult.publish_job.id}</Descriptions.Item>
          </Descriptions>
          <div style={{ marginTop: 12 }}>
            <Text type="secondary" style={{ fontSize: 12 }}>
              改写后标题：{lastRunResult.draft.title}
            </Text>
            <Paragraph
              type="secondary"
              ellipsis={{ rows: 3, expandable: true, symbol: "展开" }}
              style={{ fontSize: 12, marginTop: 4, marginBottom: 0 }}
            >
              {lastRunResult.draft.body}
            </Paragraph>
          </div>
        </Card>
      )}

      {/* Create Form */}
      {showCreate && (
        <Card
          title={
            <Space>
              <PlusOutlined />
              <span>新建自动运营任务</span>
            </Space>
          }
          style={panelStyle}
          styles={{ body: cardBodyStyle, header: { borderBottom: "1px solid #303030" } }}
          extra={
            <Button type="text" onClick={() => setShowCreate(false)}>
              取消
            </Button>
          }
        >
          {hasPendingProposal && (
            <Alert
              type="info"
              showIcon
              style={{ marginBottom: 16 }}
              message="以下是 AI 的配置建议，任务还没有创建"
              description={
                <div>
                  <Paragraph style={{ fontSize: 12, marginBottom: 4 }}>
                    每个字段都可以改。自动运营任务会自动发布到真实小红书账号，发出去撤不回来，
                    请特别确认发布账号无误。
                  </Paragraph>
                  {parsedAccounts.length > 0 && (
                    <Space direction="vertical" size={2}>
                      {parsedAccounts.map((account) => (
                        <Text key={account.role} style={{ fontSize: 12 }}>
                          {account.role_label}：
                          <Tag color={account.role === "creator_account_id" ? "volcano" : "blue"}>
                            {account.nickname || `#${account.id}`} (#{account.id} ·{" "}
                            {account.sub_type === "android" ? "安卓手机" : account.sub_type})
                          </Tag>
                        </Text>
                      ))}
                    </Space>
                  )}
                </div>
              }
            />
          )}
          <Form layout="vertical">
            {hasPendingProposal && instructionCandidates.length > 0 && (
              <Form.Item label="从指令中心套用 auto_ops 指令（可选）">
                <Select
                  placeholder="选择一条已存指令覆盖下方 AI 改写指令"
                  style={{ width: "100%" }}
                  allowClear
                  onChange={(templateId: number | undefined) => {
                    const template = instructionCandidates.find((item) => item.id === templateId);
                    if (template) setCreateInstruction(template.content);
                  }}
                  options={instructionCandidates.map((item) => ({ value: item.id, label: item.name }))}
                />
              </Form.Item>
            )}
            <Row gutter={16}>
              <Col xs={24} md={12}>
                <Form.Item label="任务名称" required>
                  <Input
                    placeholder="如：低卡早餐自动发布"
                    value={createName}
                    onChange={(e) => setCreateName(e.target.value)}
                    maxLength={128}
                  />
                </Form.Item>
              </Col>
              <Col xs={24} md={12}>
                <Form.Item label="关键词（每行一个）" required>
                  <TextArea
                    placeholder={"低卡早餐\n减脂食谱\n健康饮食"}
                    value={createKeywords}
                    onChange={(e) => setCreateKeywords(e.target.value)}
                    rows={3}
                  />
                </Form.Item>
              </Col>
            </Row>
            <Row gutter={16}>
              <Col xs={24} md={12}>
                <Form.Item
                  label="采集账号（可选）"
                  extra="不选则跳过抓取，直接按关键词让 AI 生成内容"
                >
                  <Select
                    placeholder="不选 = 不抓取，纯 AI 生成"
                    value={createPcAccountId}
                    onChange={(v) => setCreatePcAccountId(v ?? null)}
                    options={pcAccounts.map((a) => ({
                      value: a.id,
                      label: `${a.nickname || "PC"} (#${a.id})`,
                    }))}
                    allowClear
                  />
                </Form.Item>
              </Col>
              <Col xs={24} md={12}>
                <Form.Item label="发布账号（Creator 或安卓手机）" required>
                  <Select
                    placeholder="选择发布账号"
                    value={createCreatorAccountId}
                    onChange={(v) => setCreateCreatorAccountId(v)}
                    options={creatorAccounts.map((a) => ({
                      value: a.id,
                      label: `${a.sub_type === "android" ? "安卓手机" : "Creator"} · ${a.nickname || `#${a.id}`}`,
                    }))}
                    allowClear
                  />
                </Form.Item>
              </Col>
            </Row>
            <Form.Item label="AI 改写指令（可选）">
              <TextArea
                placeholder="如：改写为种草风格，加入个人体验感受，适合 25-35 岁女性阅读"
                value={createInstruction}
                onChange={(e) => setCreateInstruction(e.target.value)}
                rows={3}
                maxLength={2000}
              />
            </Form.Item>

            <Form.Item label="调度方式">
              <Select value={createScheduleType} onChange={setCreateScheduleType} options={[
                { value: "manual", label: "手动触发" },
                { value: "daily", label: "每日定时" },
                { value: "weekly", label: "每周定时" },
                { value: "interval", label: "自定义间隔" },
              ]} />
            </Form.Item>

            {(createScheduleType === "daily" || createScheduleType === "weekly") && (
              <Form.Item label="执行时间">
                <Input value={createScheduleTime} onChange={(e) => setCreateScheduleTime(e.target.value)} placeholder="HH:MM" style={{ width: 120 }} />
              </Form.Item>
            )}

            {createScheduleType === "weekly" && (
              <Form.Item label="执行日期">
                <Checkbox.Group
                  value={createScheduleDays.split(",").filter(Boolean)}
                  onChange={(vals) => setCreateScheduleDays(vals.join(","))}
                  options={[
                    { label: "周一", value: "1" },
                    { label: "周二", value: "2" },
                    { label: "周三", value: "3" },
                    { label: "周四", value: "4" },
                    { label: "周五", value: "5" },
                    { label: "周六", value: "6" },
                    { label: "周日", value: "7" },
                  ]}
                />
              </Form.Item>
            )}

            {createScheduleType === "interval" && (
              <Form.Item label="间隔时间">
                <Space>
                  <InputNumber min={0.5} step={0.5} max={168} value={createIntervalHours} onChange={(v) => setCreateIntervalHours(v ?? 24)} style={{ width: 100 }} />
                  <Text type="secondary">小时（0.5 = 30 分钟，1 = 1 小时，24 = 1 天）</Text>
                </Space>
              </Form.Item>
            )}

            <Form.Item>
              <Button
                type="primary"
                icon={<PlusOutlined />}
                onClick={handleCreate}
                loading={isCreating}
                block
              >
                创建任务
              </Button>
            </Form.Item>
          </Form>
        </Card>
      )}

      {!showCreate && (
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setShowCreate(true)} block style={{ marginTop: 16 }}>
          新建自动运营任务
        </Button>
      )}

      {/* Edit Modal */}
      <Modal
        title="编辑自动运营任务"
        open={editTask !== null}
        onOk={handleSaveEdit}
        onCancel={() => setEditTask(null)}
        confirmLoading={isSaving}
        okText="保存"
        cancelText="取消"
      >
        <Form layout="vertical" style={{ marginTop: 16 }}>
          <Form.Item label="任务名称">
            <Input
              value={editName}
              onChange={(e) => setEditName(e.target.value)}
              maxLength={128}
            />
          </Form.Item>
          <Form.Item label="关键词（每行一个）">
            <TextArea
              value={editKeywords}
              onChange={(e) => setEditKeywords(e.target.value)}
              rows={3}
            />
          </Form.Item>
          <Form.Item label="AI 改写指令">
            <TextArea
              value={editInstruction}
              onChange={(e) => setEditInstruction(e.target.value)}
              rows={3}
              maxLength={2000}
            />
          </Form.Item>

          <Form.Item label="调度方式">
            <Select value={editScheduleType} onChange={setEditScheduleType} options={[
              { value: "manual", label: "手动触发" },
              { value: "daily", label: "每日定时" },
              { value: "weekly", label: "每周定时" },
              { value: "interval", label: "自定义间隔" },
            ]} />
          </Form.Item>

          {(editScheduleType === "daily" || editScheduleType === "weekly") && (
            <Form.Item label="执行时间">
              <Input value={editScheduleTime} onChange={(e) => setEditScheduleTime(e.target.value)} placeholder="HH:MM" style={{ width: 120 }} />
            </Form.Item>
          )}

          {editScheduleType === "weekly" && (
            <Form.Item label="执行日期">
              <Checkbox.Group
                value={editScheduleDays.split(",").filter(Boolean)}
                onChange={(vals) => setEditScheduleDays(vals.join(","))}
                options={[
                  { label: "周一", value: "1" },
                  { label: "周二", value: "2" },
                  { label: "周三", value: "3" },
                  { label: "周四", value: "4" },
                  { label: "周五", value: "5" },
                  { label: "周六", value: "6" },
                  { label: "周日", value: "7" },
                ]}
              />
            </Form.Item>
          )}

          {editScheduleType === "interval" && (
            <Form.Item label="间隔时间">
              <Space>
                <InputNumber min={0.5} step={0.5} max={168} value={editIntervalHours} onChange={(v) => setEditIntervalHours(v ?? 24)} style={{ width: 100 }} />
                <Text type="secondary">小时（0.5 = 30 分钟，1 = 1 小时，24 = 1 天）</Text>
              </Space>
            </Form.Item>
          )}
        </Form>
      </Modal>
    </div>
  );
}
