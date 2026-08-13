import {
  CloudSyncOutlined, DatabaseOutlined, EyeOutlined, FolderOpenOutlined, PlusOutlined,
  SafetyCertificateOutlined, SyncOutlined,
} from "@ant-design/icons";
import {
  Alert, Button, Card, Col, Drawer, Form, Input, InputNumber, List, message, Modal,
  Row, Select, Space, Statistic, Table, Tag, Typography,
} from "antd";
import { useEffect, useMemo, useState } from "react";
import type { Key } from "react";
import {
  createBackup, createPromptTemplate, fetchAccounts, fetchAssetScenes, fetchBackups,
  fetchObsidianSyncConfig, fetchProducts, fetchPromptTemplates, importObsidianPrompts,
  saveObsidianSyncConfig, scanObsidianVault, updatePromptTemplate,
} from "../../../lib/api";
import type {
  BackupItem, ObsidianScanItem, ObsidianSyncConfig, PromptTemplateItem,
} from "../../../lib/api";
import type { AssetScene, PlatformAccount, Product } from "../../../types";

const { Title, Text, Paragraph } = Typography;
const labels: Record<string, string> = { image: "生图", note: "生文", title: "标题", rewrite: "改写", auto_ops: "自动运营" };
const actionLabels = {
  new: { color: "green", text: "新增" },
  update: { color: "gold", text: "更新" },
  unchanged: { color: "default", text: "无变化" },
} as const;

function errorMessage(error: unknown): string {
  const detail = (error as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
  return detail || (error instanceof Error ? error.message : "操作失败");
}

export function XhsPromptCenterPage() {
  const [items, setItems] = useState<PromptTemplateItem[]>([]);
  const [backups, setBackups] = useState<BackupItem[]>([]);
  const [accounts, setAccounts] = useState<PlatformAccount[]>([]);
  const [obsidian, setObsidian] = useState<ObsidianSyncConfig>({ configured: false, vault_path: "" });
  const [vaultPath, setVaultPath] = useState("");
  const [scanItems, setScanItems] = useState<ObsidianScanItem[]>([]);
  const [scanErrors, setScanErrors] = useState<Array<{ relative_path: string; message: string }>>([]);
  const [selectedPaths, setSelectedPaths] = useState<Key[]>([]);
  const [preview, setPreview] = useState<ObsidianScanItem | null>(null);
  const [open, setOpen] = useState(false);
  const [products, setProducts] = useState<Product[]>([]);
  const [scenes, setScenes] = useState<AssetScene[]>([]);
  const [editing, setEditing] = useState<PromptTemplateItem | null>(null);
  const [editProductId, setEditProductId] = useState<number | undefined>();
  const [editSceneId, setEditSceneId] = useState<number | undefined>();
  const [editContent, setEditContent] = useState("");
  const [editBusy, setEditBusy] = useState(false);
  const [busy, setBusy] = useState(false);
  const [scanBusy, setScanBusy] = useState(false);
  const [syncBusy, setSyncBusy] = useState(false);
  const [form] = Form.useForm();
  const accountMap = useMemo(() => new Map(accounts.map(x => [x.id, x.nickname || `#${x.id}`])), [accounts]);
  const productMap = useMemo(() => new Map(products.map(x => [x.id, x.name])), [products]);
  const sceneMap = useMemo(() => new Map(scenes.map(x => [x.id, x.name])), [scenes]);

  async function load() {
    const [p, b, a, o, prod, sc] = await Promise.all([
      fetchPromptTemplates(), fetchBackups(), fetchAccounts("xhs"), fetchObsidianSyncConfig(),
      fetchProducts(), fetchAssetScenes(),
    ]);
    setItems(p.items); setBackups(b.items); setAccounts(a); setObsidian(o); setVaultPath(o.vault_path || "");
    setProducts(prod); setScenes(sc);
  }

  function openEdit(row: PromptTemplateItem) {
    setEditing(row);
    setEditProductId(row.product_id ?? undefined);
    setEditSceneId(row.scene_id ?? undefined);
    setEditContent(row.content);
  }

  async function saveBindings() {
    if (!editing) return;
    setEditBusy(true);
    try {
      const contentChanged = editContent.trim() !== editing.content.trim();
      await updatePromptTemplate(editing.id, {
        name: editing.name, prompt_type: editing.prompt_type,
        platform_account_id: editing.platform_account_id ?? null,
        product_id: editProductId ?? null, scene_id: editSceneId ?? null,
        scenes: editing.scenes, tags: editing.tags,
        content: editContent,
        status: editing.status, priority: editing.priority,
        change_note: contentChanged ? "修改指令正文" : "调整产品 / 场景绑定",
      });
      message.success(contentChanged ? "指令正文已更新，生成新版本" : "绑定已更新");
      setEditing(null);
      await load();
    } catch (error) { message.error(errorMessage(error)); }
    finally { setEditBusy(false); }
  }

  useEffect(() => { void load(); }, []);

  async function save(values: Record<string, unknown>) {
    await createPromptTemplate({
      name: String(values.name), prompt_type: String(values.prompt_type),
      platform_account_id: values.platform_account_id ? Number(values.platform_account_id) : null,
      product_id: values.product_id ? Number(values.product_id) : null,
      scene_id: values.scene_id ? Number(values.scene_id) : null,
      scenes: String(values.scenes || "").split(/[,，\n]/).map(x => x.trim()).filter(Boolean), tags: [],
      content: String(values.content), status: "active", priority: Number(values.priority || 0),
    });
    message.success("指令已保存为版本 1"); setOpen(false); form.resetFields(); await load();
  }

  async function backup() {
    setBusy(true);
    try { const row = await createBackup(); message.success(`备份完成：${row.id}`); await load(); }
    finally { setBusy(false); }
  }

  async function saveVault() {
    if (!vaultPath.trim()) { message.warning("请填写 Obsidian 仓库的绝对路径"); return; }
    setScanBusy(true);
    try {
      const config = await saveObsidianSyncConfig(vaultPath.trim());
      setObsidian(config); setVaultPath(config.vault_path); message.success("Obsidian 仓库已配置");
      await scanVault();
    } catch (error) { message.error(errorMessage(error)); }
    finally { setScanBusy(false); }
  }

  async function scanVault() {
    setScanBusy(true);
    try {
      const result = await scanObsidianVault();
      setObsidian(result.config); setScanItems(result.items); setScanErrors(result.errors);
      setSelectedPaths(result.items.filter(item => item.action !== "unchanged").map(item => item.relative_path));
      message.success(`扫描完成：发现 ${result.total} 个 Markdown 文件`);
    } catch (error) { message.error(errorMessage(error)); }
    finally { setScanBusy(false); }
  }

  async function syncSelected() {
    if (!selectedPaths.length) { message.warning("请先勾选需要同步的内容"); return; }
    setSyncBusy(true);
    try {
      const result = await importObsidianPrompts(selectedPaths.map(String));
      message.success(`同步完成：新增 ${result.created}，更新 ${result.updated}，未变化 ${result.unchanged}`);
      await Promise.all([load(), scanVault()]);
    } catch (error) { message.error(errorMessage(error)); }
    finally { setSyncBusy(false); }
  }

  function confirmSync() {
    Modal.confirm({
      title: `确认同步已勾选的 ${selectedPaths.length} 项？`,
      content: "同步前会自动创建数据库回撤点；新增内容进入正式指令库，已有内容变化时生成新版本。",
      okText: "确认同步", cancelText: "取消", onOk: syncSelected,
    });
  }

  return <div>
    <Text type="secondary" style={{ letterSpacing: 1.5 }}>PROMPT & SAFETY CENTER</Text>
    <Title level={3} style={{ margin: "4px 0" }}>指令中心与安全备份</Title>
    <Text type="secondary">集中管理生图、生文和自动运营指令；从 Obsidian 勾选同步后形成可追溯的正式指令版本。</Text>

    <Card
      title={<Space><CloudSyncOutlined />Obsidian 指令同步</Space>}
      style={{ marginTop: 16 }}
      extra={<Tag color={obsidian.configured ? "green" : "default"}>{obsidian.configured ? "已配置" : "未配置"}</Tag>}
    >
      <Alert
        type={obsidian.configured ? "success" : "info"} showIcon
        message={obsidian.configured ? "已连接本地 Obsidian 仓库" : "先配置 Obsidian 仓库路径"}
        description="系统只扫描 Markdown 文件；扫描结果可预览和勾选，确认后才会写入正式指令库。隐藏目录、.obsidian 配置和附件不会导入。"
      />
      <Space.Compact style={{ width: "100%", marginTop: 16 }}>
        <Input
          prefix={<FolderOpenOutlined />} value={vaultPath} onChange={event => setVaultPath(event.target.value)}
          placeholder="例如：/Users/你的名字/Documents/Obsidian/Vault"
        />
        <Button type="primary" loading={scanBusy} onClick={() => void saveVault()}>保存并扫描</Button>
        <Button icon={<SyncOutlined />} disabled={!obsidian.configured} loading={scanBusy} onClick={() => void scanVault()}>重新扫描</Button>
      </Space.Compact>
      {(obsidian.last_scanned_at || obsidian.last_synced_at) && <Text type="secondary" style={{ display: "block", marginTop: 8 }}>
        {obsidian.last_scanned_at ? `上次扫描：${new Date(obsidian.last_scanned_at).toLocaleString("zh-CN")}` : ""}
        {obsidian.last_synced_at ? `　上次同步：${new Date(obsidian.last_synced_at).toLocaleString("zh-CN")}` : ""}
      </Text>}
      {scanErrors.length > 0 && <Alert
        style={{ marginTop: 12 }} type="warning" showIcon
        message={`${scanErrors.length} 个文件未能读取`}
        description={scanErrors.slice(0, 5).map(item => `${item.relative_path}：${item.message}`).join("；")}
      />}
      {scanItems.length > 0 && <>
        <Table<ObsidianScanItem>
          style={{ marginTop: 16 }} rowKey="relative_path" size="small" pagination={{ pageSize: 8 }}
          rowSelection={{
            selectedRowKeys: selectedPaths, onChange: keys => setSelectedPaths(keys),
            getCheckboxProps: record => ({ disabled: record.action === "unchanged" }),
          }}
          dataSource={scanItems}
          columns={[
            { title: "状态", dataIndex: "action", width: 90, render: value => <Tag color={actionLabels[value as keyof typeof actionLabels].color}>{actionLabels[value as keyof typeof actionLabels].text}</Tag> },
            { title: "名称", dataIndex: "name", ellipsis: true },
            { title: "类型", dataIndex: "prompt_type", width: 90, render: value => <Tag color="blue">{labels[value] || value}</Tag> },
            { title: "文件", dataIndex: "relative_path", ellipsis: true },
            { title: "场景", dataIndex: "scenes", render: value => value.length ? value.map((scene: string) => <Tag key={scene}>{scene}</Tag>) : "—" },
            { title: "版本", width: 80, render: (_, record) => record.action === "new" ? "新建" : `v${record.current_version}${record.action === "update" ? " → 新版" : ""}` },
            { title: "预览", width: 76, render: (_, record) => <Button type="link" icon={<EyeOutlined />} onClick={() => setPreview(record)}>查看</Button> },
          ]}
        />
        <Space style={{ width: "100%", justifyContent: "flex-end" }}>
          <Text type="secondary">已勾选 {selectedPaths.length} 项</Text>
          <Button type="primary" icon={<CloudSyncOutlined />} loading={syncBusy} disabled={!selectedPaths.length} onClick={confirmSync}>同步到指令库</Button>
        </Space>
      </>}
    </Card>

    <Row gutter={16} style={{ marginTop: 16 }}>
      <Col span={8}><Card><Statistic title="正式指令" value={items.length} /></Card></Col>
      <Col span={8}><Card><Statistic title="有效备份" value={backups.filter(x => x.valid).length} /></Card></Col>
      <Col span={8}><Card><Statistic title="指令版本" value={items.reduce((n, x) => n + x.current_version, 0)} /></Card></Col>
    </Row>
    <Card title="指令库" style={{ marginTop: 16 }} extra={<Button type="primary" icon={<PlusOutlined />} onClick={() => setOpen(true)}>新增指令</Button>}>
      <Table rowKey="id" dataSource={items} pagination={false} columns={[
        { title: "类型", dataIndex: "prompt_type", render: value => <Tag color="blue">{labels[value] || value}</Tag> },
        { title: "名称", dataIndex: "name" },
        { title: "账号", dataIndex: "platform_account_id", render: value => value ? accountMap.get(value) : "全局" },
        { title: "产品", dataIndex: "product_id", render: value => value ? <Tag color="geekblue">{productMap.get(value) || `#${value}`}</Tag> : "通用" },
        { title: "场景绑定", dataIndex: "scene_id", render: value => value ? <Tag color="cyan">{sceneMap.get(value) || `#${value}`}</Tag> : "通用" },
        { title: "场景标签", dataIndex: "scenes", render: value => value.length ? value.map((scene: string) => <Tag key={scene}>{scene}</Tag>) : "—" },
        { title: "版本", dataIndex: "current_version", render: value => `v${value}` },
        { title: "官方", dataIndex: "is_official", render: value => value ? <Tag color="gold">官方</Tag> : <Tag>自建</Tag> },
        { title: "来源", dataIndex: "source_type", render: value => <Tag color={value === "obsidian" ? "purple" : "default"}>{value === "obsidian" ? "Obsidian" : "手工"}</Tag> },
        { title: "操作", width: 90, render: (_, record: PromptTemplateItem) => <Button type="link" size="small" onClick={() => openEdit(record)}>改绑定</Button> },
      ]} />
    </Card>
    <Card title={<Space><SafetyCertificateOutlined />备份与回撤点</Space>} style={{ marginTop: 16 }} extra={<Button icon={<DatabaseOutlined />} loading={busy} onClick={() => void backup()}>立即完整备份</Button>}>
      <List dataSource={backups.slice(0, 10)} locale={{ emptyText: "暂无备份" }} renderItem={item => <List.Item extra={<Tag color={item.valid ? "green" : "red"}>{item.valid ? "校验通过" : "校验失败"}</Tag>}><List.Item.Meta title={item.id} description={`${item.reason} · ${new Date(item.created_at).toLocaleString("zh-CN")} · ${(item.files.reduce((n, file) => n + file.size, 0) / 1024 / 1024).toFixed(1)} MB`} /></List.Item>} />
      <Text type="secondary">恢复为离线安全操作：系统会先再次备份当前状态，再校验并恢复指定回撤点，避免误覆盖。</Text>
    </Card>

    <Drawer title={preview?.name || "指令预览"} open={Boolean(preview)} onClose={() => setPreview(null)} width={620}>
      {preview && <>
        <Space wrap>
          <Tag color="blue">{labels[preview.prompt_type] || preview.prompt_type}</Tag>
          <Tag color={actionLabels[preview.action].color}>{actionLabels[preview.action].text}</Tag>
          <Text type="secondary">{preview.relative_path}</Text>
        </Space>
        <Paragraph style={{ marginTop: 20, whiteSpace: "pre-wrap" }}>{preview.content_preview}{preview.content_length > preview.content_preview.length ? "\n\n……" : ""}</Paragraph>
        <Text type="secondary">正文共 {preview.content_length} 个字符</Text>
      </>}
    </Drawer>

    <Modal title="新增正式指令" open={open} onCancel={() => setOpen(false)} onOk={() => form.submit()} width={760}>
      <Form form={form} layout="vertical" onFinish={values => void save(values)} initialValues={{ prompt_type: "note", priority: 0 }}>
        <Row gutter={16}><Col span={12}><Form.Item name="name" label="指令名称" rules={[{ required: true }]}><Input /></Form.Item></Col><Col span={6}><Form.Item name="prompt_type" label="类型"><Select options={Object.entries(labels).map(([value, label]) => ({ value, label }))} /></Form.Item></Col><Col span={6}><Form.Item name="priority" label="优先级"><InputNumber min={-100} max={100} style={{ width: "100%" }} /></Form.Item></Col></Row>
        <Form.Item name="platform_account_id" label="适用账号"><Select allowClear placeholder="不选则为全局指令" options={accounts.map(x => ({ value: x.id, label: x.nickname || `#${x.id}` }))} /></Form.Item>
        <Row gutter={16}>
          <Col span={12}><Form.Item name="product_id" label="绑定产品"><Select allowClear placeholder="不选则为通用指令" options={products.map(x => ({ value: x.id, label: x.name }))} /></Form.Item></Col>
          <Col span={12}><Form.Item name="scene_id" label="绑定场景"><Select allowClear placeholder="不选则为通用指令" options={scenes.map(x => ({ value: x.id, label: x.name }))} /></Form.Item></Col>
        </Row>
        <Form.Item name="scenes" label="适用场景（逗号或换行分隔）"><Input placeholder="护腰选购，久坐科普，搬运防护" /></Form.Item>
        <Form.Item name="content" label="指令正文" rules={[{ required: true }]}><Input.TextArea rows={10} /></Form.Item>
      </Form>
    </Modal>

    <Modal
      title={`编辑指令 · ${editing?.name ?? ""}`} open={editing !== null}
      onCancel={() => setEditing(null)} onOk={() => void saveBindings()}
      confirmLoading={editBusy} okText="保存" cancelText="取消" width={640}
    >
      <Alert
        type="info" showIcon style={{ marginBottom: 16 }}
        message="修改正文会生成新版本（版本号 +1）；只改绑定不动版本号"
      />
      <Space direction="vertical" style={{ width: "100%" }} size={12}>
        <div>
          <Text>指令正文</Text>
          <Input.TextArea
            value={editContent}
            onChange={(e) => setEditContent(e.target.value)}
            rows={8}
            style={{ marginTop: 4 }}
          />
        </div>
        <div>
          <Text>绑定产品</Text>
          <Select
            allowClear style={{ width: "100%" }} placeholder="不选则为通用指令"
            value={editProductId} onChange={value => setEditProductId(value)}
            options={products.map(x => ({ value: x.id, label: x.name }))}
          />
        </div>
        <div>
          <Text>绑定场景</Text>
          <Select
            allowClear style={{ width: "100%" }} placeholder="不选则为通用指令"
            value={editSceneId} onChange={value => setEditSceneId(value)}
            options={scenes.map(x => ({ value: x.id, label: x.name }))}
          />
        </div>
        <Text type="secondary">
          当前：{editing?.is_official ? "官方模板" : "自建模板"} · v{editing?.current_version ?? 1}
        </Text>
      </Space>
    </Modal>
  </div>;
}
