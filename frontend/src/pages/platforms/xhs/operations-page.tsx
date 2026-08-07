import { CalendarOutlined, CheckOutlined, EditOutlined, ExperimentOutlined, PlusOutlined, SyncOutlined } from "@ant-design/icons";
import { Alert, Button, Card, Col, DatePicker, Descriptions, Divider, Form, Input, InputNumber, List, message, Modal, Row, Select, Space, Switch, Table, Tag, Typography } from "antd";
import dayjs from "dayjs";
import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { analyzeAccountStrategy, approveStrategyProposal, fetchAccounts, fetchOperationPlans, fetchOperationProfiles, fetchStrategyProposals, generateOperationPlans, rejectStrategyProposal, saveOperationProfile, syncAccountContent } from "../../../lib/api";
import type { OperationPlan, OperationProfile, PlatformAccount, StrategyProposal } from "../../../types";

const { Title, Text } = Typography;
const split = (value = "") => value.split(/[,，\n]/).map((item) => item.trim()).filter(Boolean);

export function XhsOperationsPage() {
  const [searchParams] = useSearchParams();
  const [accounts, setAccounts] = useState<PlatformAccount[]>([]);
  const [profiles, setProfiles] = useState<OperationProfile[]>([]);
  const [plans, setPlans] = useState<OperationPlan[]>([]);
  const [date, setDate] = useState(dayjs());
  const [accountFilter, setAccountFilter] = useState<number | undefined>(() => Number(searchParams.get("account_id")) || undefined);
  const [editing, setEditing] = useState<PlatformAccount | null>(null);
  const [loading, setLoading] = useState(false);
  const [syncing, setSyncing] = useState<Set<number>>(new Set());
  const [proposals, setProposals] = useState<StrategyProposal[]>([]);
  const [reviewing, setReviewing] = useState<StrategyProposal | null>(null);
  const [analyzing, setAnalyzing] = useState<Set<number>>(new Set());
  const [deciding, setDeciding] = useState(false);
  const [form] = Form.useForm();
  const accountMap = useMemo(() => new Map(accounts.map((item) => [item.id, item])), [accounts]);

  async function load() {
    const [accountItems, profileItems, planItems, proposalItems] = await Promise.all([
      fetchAccounts("xhs"), fetchOperationProfiles(), fetchOperationPlans(date.format("YYYY-MM-DD")), fetchStrategyProposals({ status: "pending" }),
    ]);
    setAccounts(accountItems); setProfiles(profileItems); setPlans(planItems); setProposals(proposalItems);
    const requested = Number(searchParams.get("account_id"));
    if (requested && !editing) openProfile(accountItems.find((item) => item.id === requested) || null, profileItems);
  }
  useEffect(() => { void load(); }, [date.format("YYYY-MM-DD")]);

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
      content_directions: split(String(values.content_directions || "")), daily_post_count: Number(values.daily_post_count || 0),
      daily_generate_count: Number(values.daily_generate_count || 0), daily_publish_count: Number(values.daily_publish_count || 0),
      preferred_times: split(String(values.preferred_times || "")), goals: split(String(values.goals || "")),
      tone: String(values.tone || ""), forbidden_topics: split(String(values.forbidden_topics || "")),
      review_required: Boolean(values.review_required), status: values.status as "active" | "paused",
    });
    message.success("运营档案已保存"); setEditing(null); await load();
  }
  async function syncContent(account: PlatformAccount) {
    setSyncing((current) => new Set(current).add(account.id));
    try {
      const result = await syncAccountContent(account.id);
      if (result.remote_error) message.warning(`已同步 ${result.total} 篇；${result.remote_error}`);
      else message.success(`同步与分析完成，账号内容库共 ${result.total} 篇`);
      if (result.strategy_proposal) setReviewing(result.strategy_proposal);
    } catch { message.error("账号内容同步失败"); }
    finally { setSyncing((current) => { const next = new Set(current); next.delete(account.id); return next; }); }
  }
  async function analyze(account: PlatformAccount) {
    setAnalyzing((current) => new Set(current).add(account.id));
    try {
      const proposal = await analyzeAccountStrategy(account.id);
      setReviewing(proposal); message.success("分析方案已生成，请确认");
      await load();
    } catch { message.error("策略分析失败，请检查默认文本模型"); }
    finally { setAnalyzing((current) => { const next = new Set(current); next.delete(account.id); return next; }); }
  }
  async function approve() {
    if (!reviewing) return;
    setDeciding(true);
    try {
      const result = await approveStrategyProposal(reviewing.id, reviewing.strategy);
      message.success(`方案已应用：${result.insights_created}条学习结论，${result.plans_created}条计划，自动任务新增${result.auto_tasks_created}个/更新${result.auto_tasks_updated}个`);
      setReviewing(null); await load();
    } finally { setDeciding(false); }
  }
  async function reject() {
    if (!reviewing) return;
    setDeciding(true);
    try { await rejectStrategyProposal(reviewing.id); message.info("方案已拒绝，未修改运营档案"); setReviewing(null); await load(); }
    finally { setDeciding(false); }
  }
  async function generate() {
    setLoading(true);
    try {
      const result = await generateOperationPlans(date.format("YYYY-MM-DD"));
      message.success(result.created ? `已生成 ${result.created} 条计划` : "当天计划已存在，无需重复生成");
      await load();
    } finally { setLoading(false); }
  }

  return <div>
    <Text type="secondary" style={{ letterSpacing: 1.5 }}>XHS OPERATIONS</Text>
    <Title level={3} style={{ margin: "4px 0" }}>运营计划</Title>
    <Text type="secondary">按账号定位反向规划每日发文数量、内容方向与时间；保留现有自动运营流程。</Text>
    <div style={{ marginTop: 16 }}><Select allowClear value={accountFilter} onChange={setAccountFilter} placeholder="选择账号查看运营方案" style={{ width: 280 }} options={accounts.map((item) => ({ value: item.id, label: item.nickname || `#${item.id}` }))} /></div>
    <Row gutter={[16, 16]} style={{ marginTop: 24 }}>
      {accounts.filter((account) => !accountFilter || account.id === accountFilter).map((account) => {
        const profile = profiles.find((item) => item.platform_account_id === account.id);
        const pending = proposals.find((item) => item.platform_account_id === account.id);
        return <Col xs={24} md={12} lg={8} key={account.id}><Card title={account.nickname || `账号 #${account.id}`} extra={<Space wrap><Button size="small" loading={syncing.has(account.id)} icon={<SyncOutlined />} onClick={() => void syncContent(account)}>同步并分析</Button><Button size="small" loading={analyzing.has(account.id)} icon={<ExperimentOutlined />} onClick={() => void analyze(account)}>重新分析</Button><Button size="small" icon={<EditOutlined />} onClick={() => openProfile(account)}>运营档案</Button></Space>}>
          {pending ? <Alert type="info" showIcon message={`待确认策略 · ${pending.source_note_count}篇样本`} action={<Button size="small" type="link" onClick={() => setReviewing(pending)}>查看方案</Button>} style={{ marginBottom: 12 }} /> : null}
          {profile ? <Space direction="vertical"><Tag color={profile.status === "active" ? "green" : "default"}>{profile.status === "active" ? "规划中" : "已暂停"}</Tag><Text>{profile.positioning || "未填写账号定位"}</Text><Text type="secondary">每日 {profile.daily_post_count} 篇 · {profile.content_directions.join(" / ") || "未设内容方向"}</Text></Space> : <Button type="dashed" icon={<PlusOutlined />} onClick={() => openProfile(account)}>建立运营档案</Button>}
        </Card></Col>;
      })}
    </Row>
    <Card style={{ marginTop: 18 }} title={<Space><CalendarOutlined />每日内容排期</Space>} extra={<Space><DatePicker value={date} onChange={(value) => value && setDate(value)} /><Button type="primary" loading={loading} onClick={() => void generate()}>生成当日计划</Button></Space>}>
      <Table rowKey="id" dataSource={plans.filter((item) => !accountFilter || item.platform_account_id === accountFilter)} pagination={false} locale={{ emptyText: "先为账号建立运营档案，再生成当日计划" }} columns={[
        { title: "账号", dataIndex: "platform_account_id", render: (id) => accountMap.get(id)?.nickname || `#${id}` },
        { title: "时间", dataIndex: "planned_at", render: (value) => value ? dayjs(value).format("HH:mm") : "-" },
        { title: "内容方向", dataIndex: "content_direction" }, { title: "选题", dataIndex: "topic" },
        { title: "状态", dataIndex: "status", render: (value) => <Tag color={value === "published" ? "green" : "blue"}>{value}</Tag> },
        { title: "规划依据", dataIndex: "rationale" },
      ]} />
    </Card>
    <Modal title={`运营档案 · ${editing?.nickname || ""}`} open={Boolean(editing)} onCancel={() => setEditing(null)} onOk={() => form.submit()} width={720}>
      <Form form={form} layout="vertical" onFinish={(values) => void save(values)}>
        <Form.Item label="小红书主页链接" name="homepage_url" tooltip="安卓账号需要填写对应的小红书主页分享链接；同步时使用已绑定的 PC 账号读取公开主页内容"><Input placeholder="https://www.xiaohongshu.com/user/profile/..." /></Form.Item>
        <Row gutter={16}><Col span={12}><Form.Item label="账号定位" name="positioning"><Input.TextArea rows={2} placeholder="例：护腰科普与选购建议" /></Form.Item></Col><Col span={12}><Form.Item label="目标人群" name="target_audience"><Input.TextArea rows={2} placeholder="例：久坐办公、搬运人群" /></Form.Item></Col></Row>
        <Form.Item label="内容方向（每行一个）" name="content_directions"><Input.TextArea rows={3} placeholder={"护腰选购\n使用方法\n腰部健康科普"} /></Form.Item>
        <Row gutter={16}><Col span={8}><Form.Item label="每日生成草稿" name="daily_generate_count"><InputNumber min={1} max={10} style={{ width: "100%" }} /></Form.Item></Col><Col span={8}><Form.Item label="每日计划发布" name="daily_publish_count"><InputNumber min={1} max={10} style={{ width: "100%" }} /></Form.Item></Col><Col span={8}><Form.Item label="兼容发布篇数" name="daily_post_count"><InputNumber min={1} max={10} style={{ width: "100%" }} /></Form.Item></Col></Row>
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
      footer={<Space><Button danger loading={deciding} onClick={() => void reject()}>拒绝，不应用</Button><Button type="primary" icon={<CheckOutlined />} loading={deciding} onClick={() => void approve()}>确认并形成档案与7天计划</Button></Space>}
    >
      {reviewing ? <>
        <Alert type={reviewing.analysis_error ? "warning" : "info"} showIcon message={`仅基于当前账号 ${reviewing.source_note_count} 篇同步内容分析`} description={reviewing.analysis_error || reviewing.strategy.summary} />
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
        <List size="small" dataSource={reviewing.strategy.learning_insights || []} renderItem={(item) => <List.Item><List.Item.Meta title={item.title} description={item.conclusion} /><Tag>{Math.round((item.confidence || 0) * 100)}%</Tag></List.Item>} />
        <Divider>未来7天选题</Divider>
        <Table size="small" pagination={false} rowKey={(_, index) => String(index)} dataSource={reviewing.strategy.seven_day_topics || []} columns={[{ title: "内容方向", dataIndex: "direction" }, { title: "选题", dataIndex: "topic" }]} />
      </> : null}
    </Modal>
  </div>;
}
