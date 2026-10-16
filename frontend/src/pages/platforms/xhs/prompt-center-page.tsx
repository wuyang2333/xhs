import {
  CloudSyncOutlined, CopyOutlined, DatabaseOutlined, DeleteOutlined, EditOutlined, EyeOutlined,
  FolderOpenOutlined, HistoryOutlined, MoreOutlined, PauseCircleOutlined, PlayCircleOutlined,
  PlusOutlined, SafetyCertificateOutlined, SyncOutlined,
} from "@ant-design/icons";
import {
  Alert, Button, Card, Checkbox, Col, Collapse, Drawer, Dropdown, Empty, Form,
  Input, InputNumber, List, message, Modal, Row, Select, Space, Spin, Statistic, Table,
  Tag, Typography,
} from "antd";
import { useEffect, useMemo, useState } from "react";
import type { Key } from "react";
import {
  activatePromptTemplate, archivePromptTemplate, createBackup, createPromptTemplate,
  deletePromptTemplate, fetchAccounts, fetchAssetScenes, fetchBackups, fetchObsidianSyncConfig,
  fetchProducts, fetchPromptTemplates, fetchPromptVersions, importObsidianPrompts,
  restorePromptVersion, saveObsidianSyncConfig, scanObsidianVault, updatePromptTemplate,
} from "../../../lib/api";
import type {
  BackupItem, ObsidianScanItem, ObsidianSyncConfig, PromptTemplateItem, PromptVersionItem,
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
  const [editBusy, setEditBusy] = useState(false);
  const [historyPrompt, setHistoryPrompt] = useState<PromptTemplateItem | null>(null);
  const [versions, setVersions] = useState<PromptVersionItem[]>([]);
  const [versionsBusy, setVersionsBusy] = useState(false);
  const [restoreBusy, setRestoreBusy] = useState<number | null>(null);
  const [compareVersions, setCompareVersions] = useState<number[]>([]);
  const [busy, setBusy] = useState(false);
  const [scanBusy, setScanBusy] = useState(false);
  const [syncBusy, setSyncBusy] = useState(false);
  const [form] = Form.useForm();
  const [editForm] = Form.useForm();
  const accountMap = useMemo(() => new Map(accounts.map(x => [x.id, x.nickname || `#${x.id}`])), [accounts]);
  const productMap = useMemo(() => new Map(products.map(x => [x.id, x.name])), [products]);
  const sceneMap = useMemo(() => new Map(scenes.map(x => [x.id, x.name])), [scenes]);

  async function load() {
    const [p, b, a, o, prod, sc] = await Promise.all([
      fetchPromptTemplates({ status: "" }), fetchBackups(), fetchAccounts("xhs"), fetchObsidianSyncConfig(),
      fetchProducts(), fetchAssetScenes(),
    ]);
    setItems(p.items); setBackups(b.items); setAccounts(a); setObsidian(o); setVaultPath(o.vault_path || "");
    setProducts(prod); setScenes(sc);
  }

  function openEdit(row: PromptTemplateItem) {
    setEditing(row);
    editForm.setFieldsValue({
      name: row.name, prompt_type: row.prompt_type, priority: row.priority,
      platform_account_id: row.platform_account_id ?? undefined,
      product_id: row.product_id ?? undefined, scene_id: row.scene_id ?? undefined,
      scenes: row.scenes.join("，"), content: row.content,
      change_note: "",
    });
  }

  async function saveEditing(values: Record<string, unknown>) {
    if (!editing) return;
    setEditBusy(true);
    try {
      const content = String(values.content || "").trim();
      const contentChanged = content !== editing.content.trim();
      await updatePromptTemplate(editing.id, {
        name: String(values.name).trim(), prompt_type: String(values.prompt_type),
        platform_account_id: values.platform_account_id ? Number(values.platform_account_id) : null,
        product_id: values.product_id ? Number(values.product_id) : null,
        scene_id: values.scene_id ? Number(values.scene_id) : null,
        scenes: String(values.scenes || "").split(/[,，\n]/).map(x => x.trim()).filter(Boolean),
        tags: editing.tags, content,
        status: editing.status, priority: Number(values.priority || 0),
        change_note: contentChanged ? String(values.change_note || "人工修改指令正文").trim() : "更新指令信息",
      });
      message.success(contentChanged ? "指令已更新，并生成一个新版本" : "指令信息已更新，版本号不变");
      setEditing(null);
      await load();
    } catch (error) { message.error(errorMessage(error)); }
    finally { setEditBusy(false); }
  }

  async function openHistory(row: PromptTemplateItem) {
    setHistoryPrompt(row); setVersions([]); setCompareVersions([]); setVersionsBusy(true);
    try { const result = await fetchPromptVersions(row.id); setVersions(result.items); }
    catch (error) { message.error(errorMessage(error)); }
    finally { setVersionsBusy(false); }
  }

  async function copyPrompt(row: PromptTemplateItem) {
    try {
      const copy = await createPromptTemplate({
        name: `${row.name}（副本）`, prompt_type: row.prompt_type,
        platform_account_id: row.platform_account_id ?? null, product_id: row.product_id ?? null,
        scene_id: row.scene_id ?? null, scenes: row.scenes, tags: row.tags, content: row.content,
        status: "active", priority: row.priority,
      });
      message.success("副本已创建，可继续编辑"); await load(); openEdit(copy);
    } catch (error) { message.error(errorMessage(error)); }
  }

  async function setPromptActive(row: PromptTemplateItem, active: boolean) {
    try {
      if (active) await activatePromptTemplate(row.id); else await archivePromptTemplate(row.id);
      message.success(active ? "指令已启用" : "指令已停用，不再出现在指令选择器中"); await load();
    } catch (error) { message.error(errorMessage(error)); }
  }

  function confirmDelete(row: PromptTemplateItem) {
    Modal.confirm({
      title: `永久删除“${row.name}”？`,
      content: "只有从未被使用过的指令才能删除。删除后无法恢复；如需保留记录，请选择停用。",
      okText: "永久删除", okButtonProps: { danger: true }, cancelText: "取消",
      onOk: async () => {
        try { await deletePromptTemplate(row.id); message.success("指令已永久删除"); await load(); }
        catch (error) { message.error(errorMessage(error)); throw error; }
      },
    });
  }

  function confirmRestore(version: PromptVersionItem) {
    if (!historyPrompt) return;
    Modal.confirm({
      title: `恢复 v${version.version} 的正文？`,
      content: `不会覆盖已有历史。系统会将 v${version.version} 的正文保存为 v${historyPrompt.current_version + 1}。`,
      okText: "恢复为新版本", cancelText: "取消",
      onOk: async () => {
        setRestoreBusy(version.version);
        try {
          const updated = await restorePromptVersion(historyPrompt.id, version.version);
          message.success(`已恢复并生成 v${updated.current_version}`);
          setHistoryPrompt(updated); const result = await fetchPromptVersions(updated.id); setVersions(result.items); await load();
        } catch (error) { message.error(errorMessage(error)); throw error; }
        finally { setRestoreBusy(null); }
      },
    });
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

  const compared = compareVersions
    .map(version => versions.find(item => item.version === version))
    .filter((item): item is PromptVersionItem => Boolean(item));

  function renderComparison() {
    if (compared.length !== 2) return null;
    const [left, right] = [...compared].sort((a, b) => a.version - b.version);
    const leftLines = left.content.split("\n"); const rightLines = right.content.split("\n");
    const count = Math.max(leftLines.length, rightLines.length);
    return <Card size="small" title={`正文对比 · v${left.version} → v${right.version}`} style={{ marginBottom: 16 }}>
      <Row gutter={12}>
        {[left, right].map((version, side) => <Col span={12} key={version.version}>
          <Text strong>v{version.version}</Text>
          <div style={{ marginTop: 8, maxHeight: 280, overflow: "auto", fontFamily: "monospace", fontSize: 12 }}>
            {Array.from({ length: count }, (_, index) => {
              const own = side === 0 ? leftLines[index] : rightLines[index];
              const other = side === 0 ? rightLines[index] : leftLines[index];
              const changed = own !== other;
              return <div key={index} style={{ minHeight: 22, padding: "2px 6px", whiteSpace: "pre-wrap", background: changed ? (side === 0 ? "rgba(255,77,79,.14)" : "rgba(82,196,26,.14)") : undefined }}>
                <Text type="secondary" style={{ marginRight: 8 }}>{index + 1}</Text>{own ?? ""}
              </div>;
            })}
          </div>
        </Col>)}
      </Row>
    </Card>;
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
      <Table rowKey="id" dataSource={items} pagination={false} scroll={{ x: 1450 }} columns={[
        { title: "类型", dataIndex: "prompt_type", render: value => <Tag color="blue">{labels[value] || value}</Tag> },
        { title: "名称", dataIndex: "name" },
        { title: "账号", dataIndex: "platform_account_id", render: value => value ? accountMap.get(value) : "全局" },
        { title: "产品", dataIndex: "product_id", render: value => value ? <Tag color="geekblue">{productMap.get(value) || `#${value}`}</Tag> : "通用" },
        { title: "内容场景分类", dataIndex: "scene_id", render: value => value ? <Tag color="cyan">{sceneMap.get(value) || `#${value}`}</Tag> : "通用" },
        { title: "匹配关键词", dataIndex: "scenes", render: value => value.length ? value.map((scene: string) => <Tag key={scene}>{scene}</Tag>) : "—" },
        { title: "版本", dataIndex: "current_version", render: (value, record: PromptTemplateItem) => <Button type="link" size="small" icon={<HistoryOutlined />} onClick={() => void openHistory(record)}>v{value}</Button> },
        { title: "官方", dataIndex: "is_official", render: value => value ? <Tag color="gold">官方</Tag> : <Tag>自建</Tag> },
        { title: "来源", dataIndex: "source_type", render: value => <Tag color={value === "obsidian" ? "purple" : "default"}>{value === "obsidian" ? "Obsidian" : "手工"}</Tag> },
        { title: "状态", dataIndex: "status", render: value => value === "active" ? <Tag color="green">使用中</Tag> : <Tag>已停用</Tag> },
        { title: "操作", width: 150, fixed: "right", render: (_, record: PromptTemplateItem) => <Space size={0}>
          <Button type="link" size="small" icon={<EditOutlined />} onClick={() => openEdit(record)}>编辑</Button>
          <Dropdown menu={{ items: [
            { key: "copy", icon: <CopyOutlined />, label: "创建副本", onClick: () => void copyPrompt(record) },
            { key: "state", icon: record.status === "active" ? <PauseCircleOutlined /> : <PlayCircleOutlined />, label: record.status === "active" ? "停用" : "启用", onClick: () => void setPromptActive(record, record.status !== "active") },
            { type: "divider" },
            { key: "delete", danger: true, icon: <DeleteOutlined />, label: "永久删除", onClick: () => confirmDelete(record) },
          ] }}><Button type="text" size="small" aria-label={`更多操作：${record.name}`} icon={<MoreOutlined />} /></Dropdown>
        </Space> },
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

    <Modal title="新增正式指令" open={open} onCancel={() => setOpen(false)} onOk={() => form.submit()} width={760} okText="创建指令" cancelText="取消">
      <Form form={form} layout="vertical" onFinish={values => void save(values)} initialValues={{ prompt_type: "note", priority: 0 }}>
        <Row gutter={16}><Col span={16}><Form.Item name="name" label="指令名称" rules={[{ required: true, message: "请输入指令名称" }]}><Input placeholder="例如：小红书种草文案" /></Form.Item></Col><Col span={8}><Form.Item name="prompt_type" label="类型"><Select options={Object.entries(labels).map(([value, label]) => ({ value, label }))} /></Form.Item></Col></Row>
        <Form.Item name="content" label="指令正文" rules={[{ required: true }]}><Input.TextArea rows={10} /></Form.Item>
        <Collapse ghost items={[{ key: "advanced", label: "高级设置", children: <>
          <Form.Item name="platform_account_id" label="适用账号" extra="不选时，所有账号都可以使用这条指令。"><Select allowClear placeholder="全局指令" options={accounts.map(x => ({ value: x.id, label: x.nickname || `#${x.id}` }))} /></Form.Item>
          <Row gutter={16}>
            <Col span={12}><Form.Item name="product_id" label="绑定产品"><Select allowClear placeholder="通用产品" options={products.map(x => ({ value: x.id, label: x.name }))} /></Form.Item></Col>
            <Col span={12}><Form.Item name="scene_id" label="内容场景分类"><Select allowClear placeholder="通用场景" options={scenes.map(x => ({ value: x.id, label: x.name }))} /></Form.Item></Col>
          </Row>
          <Form.Item name="scenes" label="匹配关键词" extra="用逗号或换行分隔，用于按使用情境推荐指令。"><Input placeholder="开箱测评，使用教程，场景分享" /></Form.Item>
          <Form.Item name="priority" label="优先级" extra="数值越高，在同类候选指令中越靠前。普通指令保留 0 即可。"><InputNumber min={-100} max={100} style={{ width: "100%" }} /></Form.Item>
        </> }]} />
      </Form>
    </Modal>

    <Modal
      title={`编辑指令 · ${editing?.name ?? ""}`} open={editing !== null}
      onCancel={() => setEditing(null)} onOk={() => editForm.submit()}
      confirmLoading={editBusy} okText="保存修改" cancelText="取消" width={760}
    >
      <Alert
        type="info" showIcon style={{ marginBottom: 16 }}
        message="只有修改正文才会生成新版本"
        description="名称、类型、账号、分类、关键词和优先级的调整不会增加版本号。"
      />
      <Form form={editForm} layout="vertical" onFinish={values => void saveEditing(values)}>
        <Row gutter={16}><Col span={16}><Form.Item name="name" label="指令名称" rules={[{ required: true, message: "请输入指令名称" }]}><Input /></Form.Item></Col><Col span={8}><Form.Item name="prompt_type" label="类型" rules={[{ required: true }]}><Select options={Object.entries(labels).map(([value, label]) => ({ value, label }))} /></Form.Item></Col></Row>
        <Form.Item name="content" label="指令正文" rules={[{ required: true, message: "请输入指令正文" }]}><Input.TextArea rows={10} /></Form.Item>
        <Form.Item noStyle shouldUpdate={(previous, current) => previous.content !== current.content}>{({ getFieldValue }) =>
          editing && String(getFieldValue("content") || "").trim() !== editing.content.trim()
            ? <Form.Item name="change_note" label="本次修改说明" extra="会显示在版本历史中，方便以后判断为什么修改。"><Input placeholder="例如：加强口语表达并限制标题长度" maxLength={200} /></Form.Item>
            : null
        }</Form.Item>
        <Collapse ghost items={[{ key: "advanced", label: "高级设置", children: <>
          <Form.Item name="platform_account_id" label="适用账号" extra="不选时，所有账号都可以使用这条指令。"><Select allowClear placeholder="全局指令" options={accounts.map(x => ({ value: x.id, label: x.nickname || `#${x.id}` }))} /></Form.Item>
          <Row gutter={16}>
            <Col span={12}><Form.Item name="product_id" label="绑定产品"><Select allowClear placeholder="通用产品" options={products.map(x => ({ value: x.id, label: x.name }))} /></Form.Item></Col>
            <Col span={12}><Form.Item name="scene_id" label="内容场景分类"><Select allowClear placeholder="通用场景" options={scenes.map(x => ({ value: x.id, label: x.name }))} /></Form.Item></Col>
          </Row>
          <Form.Item name="scenes" label="匹配关键词" extra="用逗号或换行分隔，用于按使用情境推荐指令。"><Input placeholder="开箱测评，使用教程，场景分享" /></Form.Item>
          <Form.Item name="priority" label="优先级" extra="数值越高，在同类候选指令中越靠前。"><InputNumber min={-100} max={100} style={{ width: "100%" }} /></Form.Item>
        </> }]} />
        <Text type="secondary">当前：{editing?.is_official ? "官方模板" : "自建模板"} · v{editing?.current_version ?? 1}</Text>
      </Form>
    </Modal>

    <Drawer
      title={historyPrompt ? `版本历史 · ${historyPrompt.name}` : "版本历史"}
      open={Boolean(historyPrompt)} onClose={() => setHistoryPrompt(null)} width={760}
      extra={compareVersions.length > 0 && <Button type="link" onClick={() => setCompareVersions([])}>清除对比</Button>}
    >
      <Alert type="info" showIcon style={{ marginBottom: 16 }} message="历史版本不会被覆盖" description="可勾选两个版本比较正文；恢复旧版时，会将其正文保存为一个新版本。" />
      {renderComparison()}
      <Spin spinning={versionsBusy}>
        {!versionsBusy && versions.length === 0 ? <Empty description="暂无版本记录" /> : <List
          dataSource={versions}
          renderItem={version => {
            const current = version.version === historyPrompt?.current_version;
            const checked = compareVersions.includes(version.version);
            return <List.Item
              actions={[!current && <Button key="restore" type="link" loading={restoreBusy === version.version} onClick={() => confirmRestore(version)}>恢复此版本</Button>]}
            >
              <List.Item.Meta
                avatar={<Checkbox
                  aria-label={`选择 v${version.version} 进行对比`} checked={checked}
                  disabled={!checked && compareVersions.length >= 2}
                  onChange={event => setCompareVersions(values => event.target.checked ? [...values, version.version] : values.filter(value => value !== version.version))}
                />}
                title={<Space><Text strong>v{version.version}</Text>{current && <Tag color="green">当前版本</Tag>}<Text type="secondary">{version.change_note || "未填写修改说明"}</Text></Space>}
                description={<>
                  <Text type="secondary">{version.created_at ? new Date(version.created_at).toLocaleString("zh-CN") : "时间未知"} · {version.created_by || "人工修改"}</Text>
                  <Paragraph ellipsis={{ rows: 4, expandable: true, symbol: "展开正文" }} style={{ marginTop: 10, marginBottom: 0, whiteSpace: "pre-wrap" }}>{version.content}</Paragraph>
                </>}
              />
            </List.Item>;
          }}
        />}
      </Spin>
    </Drawer>
  </div>;
}
