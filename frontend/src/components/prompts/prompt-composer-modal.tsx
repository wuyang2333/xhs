import { HistoryOutlined, StarFilled, StarOutlined } from "@ant-design/icons";
import {
  Alert, Button, Empty, Input, List, message as antMessage,
  Modal, Radio, Select, Space, Tag, Tooltip, Typography,
} from "antd";
import { useCallback, useEffect, useMemo, useState } from "react";

import { fetchAssetScenes, fetchProducts, fetchPromptTemplates, fetchPromptVersions } from "../../lib/api";
import type { PromptTemplateItem } from "../../lib/api";
import type { AssetScene, Product } from "../../types";
import {
  clearDefaultPrompt, readDefaultPrompt, readFavorites, readRecents,
  saveDefaultPrompt, toggleFavorite,
} from "./prompt-store";
import type { PromptModule } from "./prompt-store";

const { Text, Paragraph } = Typography;

const TYPE_LABELS: Record<string, string> = {
  image: "生图", note: "生文", title: "标题", rewrite: "改写", auto_ops: "自动运营",
};

export type PromptComposerRequest = {
  /** 后端允许的类型：image / note / title / rewrite / auto_ops */
  promptType: string;
  /** 弹窗标题，例如「选择改写指令」 */
  title: string;
  module: PromptModule;
  accountId?: number;
  /** 页面原有的硬编码指令，仅在「自己填写」模式下作为初始文本 */
  fallbackBase?: string;
  /** 预填到自定义输入框的文本 */
  initialExtra?: string;
  confirmText?: string;
};

export type PromptComposerResult = {
  /** 真正发给模型的完整文本 */
  text: string;
  usages: Array<{ promptId: number; version: number }>;
};

type Mode = "library" | "custom";

export function PromptComposerModal(props: {
  open: boolean;
  request: PromptComposerRequest | null;
  onCancel: () => void;
  onConfirm: (result: PromptComposerResult) => void;
}) {
  const { open, request, onCancel, onConfirm } = props;
  const promptType = request?.promptType ?? "";
  const promptTypeLabel = TYPE_LABELS[promptType] ?? promptType;
  const module = request?.module ?? "draft_studio";

  const [mode, setMode] = useState<Mode>("library");
  const [templates, setTemplates] = useState<PromptTemplateItem[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [customText, setCustomText] = useState("");
  const [editedText, setEditedText] = useState<string | null>(null);
  const [productId, setProductId] = useState<number | undefined>();
  const [sceneId, setSceneId] = useState<number | undefined>();
  const [products, setProducts] = useState<Product[]>([]);
  const [scenes, setScenes] = useState<AssetScene[]>([]);
  const [favorites, setFavorites] = useState<number[]>([]);
  const [recents, setRecents] = useState<number[]>([]);
  const [favoritesOnly, setFavoritesOnly] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [historyFor, setHistoryFor] = useState<PromptTemplateItem | null>(null);
  const [versions, setVersions] = useState<Array<{ version: number; content: string; change_note: string; created_at: string }>>([]);

  useEffect(() => {
    if (!open) return;
    setFavorites(readFavorites());
    setRecents(readRecents(module, promptType));
    setEditedText(null);
    setCustomText(request?.initialExtra?.trim() || request?.fallbackBase?.trim() || "");
    const preferred = readDefaultPrompt(module, promptType);
    setSelectedId(preferred);
    setMode(preferred ? "library" : "library");
    void (async () => {
      try {
        const [p, s] = await Promise.all([fetchProducts(), fetchAssetScenes()]);
        setProducts(p);
        setScenes(s);
      } catch { /* 分类拉取失败不阻塞选指令 */ }
    })();
  }, [open, module, promptType, request?.initialExtra, request?.fallbackBase]);

  useEffect(() => {
    if (!open || !promptType) return;
    setIsLoading(true);
    void (async () => {
      try {
        const { items } = await fetchPromptTemplates({
          prompt_type: promptType, status: "active",
          account_id: request?.accountId, product_id: productId, scene_id: sceneId,
        });
        setTemplates(items);
      } catch {
        antMessage.error("指令库读取失败");
      } finally {
        setIsLoading(false);
      }
    })();
  }, [open, promptType, request?.accountId, productId, sceneId]);

  const visible = useMemo(
    () => (favoritesOnly ? templates.filter((item) => favorites.includes(item.id)) : templates),
    [templates, favoritesOnly, favorites],
  );

  const selected = useMemo(
    () => templates.find((item) => item.id === selectedId) ?? null,
    [templates, selectedId],
  );

  /** 最终发给模型的文本：选库里的就用它的正文，自己填就用输入框，两者都可再编辑。 */
  const baseText = mode === "library" ? (selected?.content ?? "") : customText;
  const finalText = editedText ?? baseText;

  const openHistory = useCallback(async (item: PromptTemplateItem) => {
    setHistoryFor(item);
    try {
      const { items } = await fetchPromptVersions(item.id);
      setVersions(items);
    } catch {
      antMessage.error("版本记录读取失败");
      setVersions([]);
    }
  }, []);

  function handleConfirm() {
    const text = finalText.trim();
    if (!text) {
      antMessage.warning(mode === "library" ? "请选择一条指令" : "请填写指令内容");
      return;
    }
    // 只有原样使用库里的指令才算一次「使用」；改过或自己填的不记账，
    // 否则统计会把临时内容算作该指令的效果。
    const usages = mode === "library" && selected && editedText === null
      ? [{ promptId: selected.id, version: selected.current_version }]
      : [];
    onConfirm({ text, usages });
  }

  return (
    <>
      <Modal
        open={open}
        title={request?.title ?? "选择指令"}
        onCancel={onCancel}
        onOk={handleConfirm}
        okText={request?.confirmText ?? "确认并执行"}
        cancelText="取消"
        width={760}
        destroyOnHidden
      >
        <Radio.Group
          value={mode}
          onChange={(e) => { setMode(e.target.value as Mode); setEditedText(null); }}
          optionType="button"
          buttonStyle="solid"
          style={{ marginBottom: 12 }}
        >
          <Radio.Button value="library">从指令库选</Radio.Button>
          <Radio.Button value="custom">自己填写</Radio.Button>
        </Radio.Group>

        {mode === "library" && templates.length === 0 && !isLoading ? (
          <Alert
            type="warning" showIcon style={{ marginBottom: 12 }}
            message={`指令库里还没有「${promptTypeLabel}」类型的指令`}
            description={`可切换到「自己填写」直接执行；若要沉淀可复用的指令，请到「指令中心」新增，类型选「${promptTypeLabel}」。`}
          />
        ) : null}

        {mode === "library" ? (
          <>
            <Space size={8} wrap style={{ marginBottom: 8, width: "100%" }}>
              <Select
                allowClear style={{ width: 200 }} placeholder="按产品筛选"
                value={productId} onChange={(v: number | undefined) => { setProductId(v); setEditedText(null); }}
                options={products.map((x) => ({ value: x.id, label: x.name }))}
              />
              <Select
                allowClear style={{ width: 200 }} placeholder="按场景筛选"
                value={sceneId} onChange={(v: number | undefined) => { setSceneId(v); setEditedText(null); }}
                options={scenes.map((x) => ({ value: x.id, label: x.name }))}
              />
              <Button
                size="small" type={favoritesOnly ? "primary" : "default"}
                icon={favoritesOnly ? <StarFilled /> : <StarOutlined />}
                onClick={() => setFavoritesOnly((v) => !v)}
              >
                只看收藏
              </Button>
            </Space>

            {recents.length > 0 ? (
              <div style={{ marginBottom: 8 }}>
                <Text type="secondary" style={{ fontSize: 12, marginRight: 6 }}>最近用过：</Text>
                {recents.slice(0, 5).map((id) => {
                  const item = templates.find((x) => x.id === id);
                  if (!item) return null;
                  return (
                    <Tag
                      key={id} color={selectedId === id ? "blue" : undefined}
                      style={{ cursor: "pointer" }}
                      onClick={() => { setSelectedId(id); setEditedText(null); }}
                    >
                      {item.name}
                    </Tag>
                  );
                })}
              </div>
            ) : null}

            <Select
              style={{ width: "100%", marginBottom: 8 }}
              placeholder={isLoading ? "读取中…" : "选择一条指令，将完全按它执行"}
              loading={isLoading}
              value={selectedId ?? undefined}
              onChange={(v: number | undefined) => { setSelectedId(v ?? null); setEditedText(null); }}
              allowClear
              showSearch
              optionFilterProp="label"
              options={visible.map((item) => ({
                value: item.id,
                label: `${favorites.includes(item.id) ? "★ " : ""}${item.name}（v${item.current_version}${item.is_official ? "·官方" : ""}）`,
              }))}
            />

            {selected ? (
              <Space size={8} style={{ marginBottom: 8 }} wrap>
                <Button
                  size="small" type="text"
                  icon={favorites.includes(selected.id) ? <StarFilled style={{ color: "#faad14" }} /> : <StarOutlined />}
                  onClick={() => setFavorites(toggleFavorite(selected.id))}
                >
                  {favorites.includes(selected.id) ? "取消收藏" : "收藏"}
                </Button>
                <Button size="small" type="text" icon={<HistoryOutlined />} onClick={() => void openHistory(selected)}>
                  版本记录
                </Button>
                {readDefaultPrompt(module, promptType) === selected.id ? (
                  <Button size="small" type="text" onClick={() => { clearDefaultPrompt(module, promptType); antMessage.success("已取消默认"); }}>
                    取消默认
                  </Button>
                ) : (
                  <Tooltip title="下次打开自动选中这条">
                    <Button size="small" type="text" onClick={() => { saveDefaultPrompt(module, promptType, selected.id); antMessage.success("已设为默认"); }}>
                      设为默认
                    </Button>
                  </Tooltip>
                )}
              </Space>
            ) : null}
          </>
        ) : (
          <Input.TextArea
            value={customText}
            onChange={(e) => { setCustomText(e.target.value); setEditedText(null); }}
            placeholder="直接写这次要用的指令，将完全按它执行"
            rows={5}
            style={{ marginBottom: 8 }}
          />
        )}

        <Text strong style={{ display: "block", marginBottom: 4 }}>
          最终执行指令
          <Text type="secondary" style={{ fontWeight: 400, fontSize: 12, marginLeft: 8 }}>
            这就是发给模型的全文，可直接改；改动只作用于本次，不写回指令库
          </Text>
        </Text>
        <Input.TextArea
          value={finalText}
          onChange={(e) => setEditedText(e.target.value)}
          rows={7}
          placeholder={mode === "library" ? "选一条指令后在此显示" : "在上方填写"}
        />
        <Text type="secondary" style={{ fontSize: 12 }}>
          {finalText.length} / 6000
          {editedText !== null ? "　·　已临时修改，不计入该指令的使用统计" : ""}
        </Text>
      </Modal>

      <Modal
        open={historyFor !== null}
        title={`版本记录：${historyFor?.name ?? ""}`}
        onCancel={() => setHistoryFor(null)}
        footer={null}
        width={640}
      >
        <List
          dataSource={versions}
          locale={{ emptyText: <Empty description="暂无版本记录" /> }}
          renderItem={(v) => (
            <List.Item>
              <div style={{ width: "100%" }}>
                <Space size={8}>
                  <Tag>v{v.version}</Tag>
                  <Text type="secondary" style={{ fontSize: 12 }}>{v.change_note || "无备注"}</Text>
                </Space>
                <Paragraph style={{ marginTop: 4, marginBottom: 0, whiteSpace: "pre-wrap", fontSize: 12 }}>
                  {v.content}
                </Paragraph>
              </div>
            </List.Item>
          )}
        />
      </Modal>
    </>
  );
}
