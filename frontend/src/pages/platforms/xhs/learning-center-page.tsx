import { PlusOutlined } from "@ant-design/icons";
import { Button, Card, Col, Form, Input, InputNumber, message, Modal, Row, Select, Statistic, Table, Tag, Typography } from "antd";
import { useEffect, useMemo, useState } from "react";
import { createLearningInsight, fetchAccounts, fetchContentPerformance, fetchLearningInsights, fetchLearningOverview } from "../../../lib/api";
import type { ContentPerformanceItem, LearningInsight, LearningOverview, PlatformAccount } from "../../../types";

const { Title, Text } = Typography;
export function XhsLearningCenterPage() {
  const [overview, setOverview] = useState<LearningOverview>({ profile_count: 0, active_insight_count: 0, today_plan_count: 0, published_count: 0, synced_note_count: 0 });
  const [insights, setInsights] = useState<LearningInsight[]>([]);
  const [performance, setPerformance] = useState<ContentPerformanceItem[]>([]);
  const [accounts, setAccounts] = useState<PlatformAccount[]>([]);
  const [accountFilter, setAccountFilter] = useState<number | undefined>();
  const [open, setOpen] = useState(false);
  const [form] = Form.useForm();
  const accountMap = useMemo(() => new Map(accounts.map((item) => [item.id, item.nickname || `#${item.id}`])), [accounts]);
  async function load() {
    const [summary, rows, accountItems, contentRows] = await Promise.all([fetchLearningOverview(), fetchLearningInsights(), fetchAccounts("xhs"), fetchContentPerformance()]);
    setOverview(summary); setInsights(rows); setAccounts(accountItems); setPerformance(contentRows);
  }
  useEffect(() => { void load(); }, []);
  async function save(values: Record<string, unknown>) {
    await createLearningInsight({
      platform_account_id: values.platform_account_id ? Number(values.platform_account_id) : null,
      insight_type: String(values.insight_type), title: String(values.title), conclusion: String(values.conclusion),
      evidence: {}, sample_size: Number(values.sample_size || 0), confidence: Number(values.confidence || 0) / 100,
    });
    message.success("学习结论已沉淀"); setOpen(false); form.resetFields(); await load();
  }
  return <div>
    <Text type="secondary" style={{ letterSpacing: 1.5 }}>XHS LEARNING CENTER</Text>
    <Title level={3} style={{ margin: "4px 0" }}>学习中心</Title>
    <Text type="secondary">沉淀各账号的有效内容规律，为运营计划提供可追溯的优化依据。</Text>
    <div style={{ marginTop: 16 }}><Select allowClear value={accountFilter} onChange={setAccountFilter} placeholder="选择账号查看学习结果" style={{ width: 280 }} options={accounts.map((item) => ({ value: item.id, label: item.nickname || `#${item.id}` }))} /></div>
    <Row gutter={16} style={{ marginTop: 24 }}>{[
      ["已同步账号内容", overview.synced_note_count], ["有效学习结论", overview.active_insight_count],
      ["今日规划内容", overview.today_plan_count], ["累计成功发布", overview.published_count],
    ].map(([label, value]) => <Col xs={12} lg={6} key={String(label)}><Card><Statistic title={label} value={value} /></Card></Col>)}</Row>
    <Card style={{ marginTop: 18 }} title="账号学习结论" extra={<Button type="primary" icon={<PlusOutlined />} onClick={() => setOpen(true)}>新增结论</Button>}>
      <Table rowKey="id" dataSource={insights.filter((item) => !accountFilter || item.platform_account_id === accountFilter)} columns={[
        { title: "账号", dataIndex: "platform_account_id", render: (id) => id ? accountMap.get(id) || `#${id}` : "全局" },
        { title: "类型", dataIndex: "insight_type", render: (value) => <Tag>{value}</Tag> },
        { title: "结论标题", dataIndex: "title" }, { title: "学习结论", dataIndex: "conclusion" },
        { title: "样本", dataIndex: "sample_size" },
        { title: "置信度", dataIndex: "confidence", render: (value) => `${Math.round(Number(value) * 100)}%` },
      ]} />
    </Card>
    <Card style={{ marginTop: 18 }} title="已同步内容表现" extra={<Text type="secondary">按点赞、收藏、评论、分享总和排序</Text>}>
      <Table rowKey="id" dataSource={performance.filter((item) => !accountFilter || item.platform_account_id === accountFilter)} pagination={{ pageSize: 10 }} locale={{ emptyText: "请先到“运营计划”点击账号的“同步内容”" }} columns={[
        { title: "账号", dataIndex: "platform_account_id", render: (id) => accountMap.get(id) || `#${id}` },
        { title: "笔记标题", dataIndex: "title" },
        { title: "阅读", dataIndex: "views", sorter: (a, b) => a.views - b.views },
        { title: "赞", dataIndex: "likes" }, { title: "藏", dataIndex: "collects" },
        { title: "评", dataIndex: "comments" }, { title: "分享", dataIndex: "shares" },
        { title: "总互动", dataIndex: "engagement", sorter: (a, b) => a.engagement - b.engagement },
        { title: "来源", dataIndex: "source", render: (value) => <Tag>{value === "publish_job" ? "工作台发布" : "小红书主页"}</Tag> },
      ]} />
    </Card>
    <Modal title="新增学习结论" open={open} onCancel={() => setOpen(false)} onOk={() => form.submit()}>
      <Form form={form} layout="vertical" onFinish={(values) => void save(values)} initialValues={{ insight_type: "content", confidence: 70, sample_size: 0 }}>
        <Form.Item label="适用账号" name="platform_account_id"><Select allowClear placeholder="不选则为全局规律" options={accounts.map((item) => ({ value: item.id, label: item.nickname || `#${item.id}` }))} /></Form.Item>
        <Form.Item label="类型" name="insight_type" rules={[{ required: true }]}><Select options={[{ value: "content", label: "内容方向" }, { value: "title", label: "标题" }, { value: "timing", label: "发布时间" }, { value: "visual", label: "图片风格" }, { value: "audience", label: "人群反馈" }]} /></Form.Item>
        <Form.Item label="结论标题" name="title" rules={[{ required: true }]}><Input maxLength={200} /></Form.Item>
        <Form.Item label="具体结论" name="conclusion" rules={[{ required: true }]}><Input.TextArea rows={4} /></Form.Item>
        <Row gutter={16}><Col span={12}><Form.Item label="样本数量" name="sample_size"><InputNumber min={0} style={{ width: "100%" }} /></Form.Item></Col><Col span={12}><Form.Item label="置信度（%）" name="confidence"><InputNumber min={0} max={100} style={{ width: "100%" }} /></Form.Item></Col></Row>
      </Form>
    </Modal>
  </div>;
}
