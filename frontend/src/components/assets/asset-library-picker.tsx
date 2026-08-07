import { PictureOutlined, ReloadOutlined } from "@ant-design/icons";
import {
  Alert,
  Button,
  Checkbox,
  Col,
  Empty,
  Input,
  Modal,
  Row,
  Select,
  Space,
  Spin,
  Tag,
  Tooltip,
  TreeSelect,
  Typography,
} from "antd";
import { useCallback, useEffect, useMemo, useState } from "react";

import {
  fetchAssetFolders,
  fetchAssetScenes,
  fetchAssetTags,
  fetchMediaAssets,
  fetchProducts,
} from "../../lib/api";
import type { MediaAssetFilters } from "../../lib/api";
import type { AssetFolder, AssetScene, AssetTag, MediaAsset, Product } from "../../types";
import {
  assetThumbUrl,
  buildFolderSelectOptions,
  buildFolderTree,
  formatFileSize,
  isRenderableImageSrc,
} from "./asset-utils";
import type { FolderSelectOption } from "./asset-utils";

const { Text } = Typography;

/** 后端 page_size 上限 100，这里按页取完整筛选集，便于「全选文件夹」与排序覆盖全量。 */
const PAGE_SIZE = 100;
const MAX_PAGES = 20;

type SortMode = "default" | "recent_upload" | "recent_used";

/** 来源筛选。后端 GET /api/assets 目前没有 source 参数，所以在取回完整筛选集后本地过滤。 */
type SourceMode = "all" | "ai" | "upload";

export type AssetLibraryPickerProps = {
  open: boolean;
  confirming: boolean;
  onClose: () => void;
  onConfirm: (assets: MediaAsset[]) => void;
  /** 弹窗标题。 */
  title?: string;
  /** 确认按钮文案前缀，实际显示为 `${confirmLabel}（n）`。 */
  confirmLabel?: string;
  /** 判定某项已被选用（界面置灰不可选）。默认全部可选。 */
  isTaken?: (asset: MediaAsset) => boolean;
  /** 置灰项的悬浮提示与角标文案。 */
  takenHint?: string;
  takenBadge?: string;
};

/** 逐页取完整筛选集；只保留图片（阶段 A 只覆盖图片素材）。 */
async function fetchAllImageAssets(
  filters: MediaAssetFilters,
): Promise<{ items: MediaAsset[]; truncated: boolean }> {
  const collected: MediaAsset[] = [];
  let truncated = false;
  for (let page = 1; page <= MAX_PAGES; page += 1) {
    const result = await fetchMediaAssets({ ...filters, page, page_size: PAGE_SIZE });
    collected.push(...result.items);
    if (result.items.length === 0 || collected.length >= result.total) break;
    if (page === MAX_PAGES) truncated = collected.length < result.total;
  }
  return { items: collected.filter((asset) => asset.asset_type === "image"), truncated };
}

/**
 * 统一素材库选图器。发布中心、草稿工坊共用同一套筛选与选择逻辑，
 * 差异只在「哪些项算已选用」和文案，通过 props 注入。
 */
export function AssetLibraryPicker({
  open,
  confirming,
  onClose,
  onConfirm,
  title = "从素材库选图",
  confirmLabel = "加入",
  isTaken,
  takenHint = "已加入",
  takenBadge = "已加入",
}: AssetLibraryPickerProps) {
  const [folders, setFolders] = useState<AssetFolder[]>([]);
  const [products, setProducts] = useState<Product[]>([]);
  const [scenes, setScenes] = useState<AssetScene[]>([]);
  const [tags, setTags] = useState<AssetTag[]>([]);

  const [folderId, setFolderId] = useState<number | undefined>();
  const [productId, setProductId] = useState<number | undefined>();
  const [sceneId, setSceneId] = useState<number | undefined>();
  const [tagId, setTagId] = useState<number | undefined>();
  const [search, setSearch] = useState("");
  const [sortMode, setSortMode] = useState<SortMode>("default");
  const [sourceMode, setSourceMode] = useState<SourceMode>("all");

  const [assets, setAssets] = useState<MediaAsset[]>([]);
  const [selectedIds, setSelectedIds] = useState<number[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [truncated, setTruncated] = useState(false);

  const taken = useCallback(
    (asset: MediaAsset): boolean => (isTaken ? isTaken(asset) : false),
    [isTaken],
  );

  const folderTree = useMemo(() => buildFolderTree(folders), [folders]);
  const folderOptions = useMemo(() => buildFolderSelectOptions(folderTree), [folderTree]);

  const visibleAssets = useMemo(() => {
    // 回填进来的历史素材 source 为 "ai" / "upload"；手工建的可能是 "manual" 等其他值，
    // 选 "上传" 时把非 ai 的都算进来，避免历史数据在筛选下凭空消失。
    const list = assets.filter((asset) => {
      if (sourceMode === "all") return true;
      if (sourceMode === "ai") return asset.source === "ai";
      return asset.source !== "ai";
    });
    if (sortMode === "recent_upload") {
      list.sort((a, b) => b.created_at.localeCompare(a.created_at) || b.id - a.id);
    } else if (sortMode === "recent_used") {
      list.sort((a, b) => {
        // 从未使用的排在后面，避免挤掉真正「最近使用」的素材。
        if (a.last_used_at === null && b.last_used_at === null) return b.id - a.id;
        if (a.last_used_at === null) return 1;
        if (b.last_used_at === null) return -1;
        return b.last_used_at.localeCompare(a.last_used_at);
      });
    }
    return list;
  }, [assets, sortMode, sourceMode]);

  const selectableIds = useMemo(
    () => visibleAssets.filter((asset) => !taken(asset)).map((asset) => asset.id),
    [visibleAssets, taken],
  );

  const loadAssets = useCallback(async () => {
    setIsLoading(true);
    setLoadError(null);
    try {
      const result = await fetchAllImageAssets({
        folder_id: folderId,
        product_id: productId,
        scene_id: sceneId,
        tag_id: tagId,
        search,
      });
      setAssets(result.items);
      setTruncated(result.truncated);
    } catch {
      setAssets([]);
      setLoadError("素材加载失败，请重试。");
    } finally {
      setIsLoading(false);
    }
  }, [folderId, productId, sceneId, tagId, search]);

  useEffect(() => {
    if (!open) return;
    void fetchAssetFolders().then(setFolders).catch(() => setFolders([]));
    void fetchProducts().then(setProducts).catch(() => setProducts([]));
    void fetchAssetScenes().then(setScenes).catch(() => setScenes([]));
    void fetchAssetTags().then(setTags).catch(() => setTags([]));
  }, [open]);

  useEffect(() => {
    if (!open) {
      setSelectedIds([]);
      return;
    }
    void loadAssets();
  }, [open, loadAssets]);

  function toggle(assetId: number): void {
    setSelectedIds((prev) =>
      prev.includes(assetId) ? prev.filter((id) => id !== assetId) : [...prev, assetId],
    );
  }

  function handleConfirm(): void {
    const byId = new Map(visibleAssets.map((asset) => [asset.id, asset]));
    // 按用户勾选的先后顺序返回，序号才和用户预期一致。
    const picked = selectedIds
      .map((id) => byId.get(id))
      .filter((asset): asset is MediaAsset => asset !== undefined && !taken(asset));
    if (picked.length === 0) return;
    onConfirm(picked);
  }

  return (
    <Modal
      title={title}
      open={open}
      onCancel={onClose}
      width={900}
      okText={`${confirmLabel}（${selectedIds.length}）`}
      cancelText="取消"
      okButtonProps={{ disabled: selectedIds.length === 0, loading: confirming }}
      onOk={handleConfirm}
      destroyOnHidden
    >
      <Space direction="vertical" size={12} style={{ width: "100%" }}>
        <Space wrap size={8}>
          <TreeSelect<number, FolderSelectOption>
            style={{ width: 190 }}
            placeholder="文件夹"
            allowClear
            treeDefaultExpandAll
            value={folderId}
            onChange={(value) => setFolderId(typeof value === "number" ? value : undefined)}
            treeData={folderOptions}
            fieldNames={{ label: "title", value: "value", children: "children" }}
          />
          <Select<SourceMode>
            style={{ width: 130 }}
            value={sourceMode}
            onChange={setSourceMode}
            options={[
              { value: "all", label: "全部来源" },
              { value: "ai", label: "AI 生成" },
              { value: "upload", label: "上传" },
            ]}
          />
          <Select
            style={{ width: 150 }}
            placeholder="产品"
            allowClear
            value={productId}
            onChange={(value?: number) => setProductId(value)}
            options={products.map((item) => ({ value: item.id, label: item.name }))}
          />
          <Select
            style={{ width: 150 }}
            placeholder="场景"
            allowClear
            value={sceneId}
            onChange={(value?: number) => setSceneId(value)}
            options={scenes.map((item) => ({ value: item.id, label: item.name }))}
          />
          <Select
            style={{ width: 150 }}
            placeholder="标签"
            allowClear
            value={tagId}
            onChange={(value?: number) => setTagId(value)}
            options={tags.map((item) => ({ value: item.id, label: item.name }))}
          />
          <Select<SortMode>
            style={{ width: 140 }}
            value={sortMode}
            onChange={setSortMode}
            options={[
              { value: "default", label: "默认顺序" },
              { value: "recent_upload", label: "最近上传" },
              { value: "recent_used", label: "最近使用" },
            ]}
          />
          <Input.Search
            style={{ width: 200 }}
            placeholder="搜索名称 / 路径"
            allowClear
            onSearch={setSearch}
          />
          <Button icon={<ReloadOutlined />} onClick={() => void loadAssets()} loading={isLoading}>
            刷新
          </Button>
        </Space>

        <Space wrap size={8}>
          <Tooltip title={folderId === undefined ? "先选一个文件夹" : "全选当前文件夹内可选素材"}>
            <Button
              size="small"
              disabled={folderId === undefined || selectableIds.length === 0}
              onClick={() => setSelectedIds(selectableIds)}
            >
              一键全选当前文件夹
            </Button>
          </Tooltip>
          <Button
            size="small"
            disabled={selectableIds.length === 0}
            onClick={() => setSelectedIds(selectableIds)}
          >
            全选当前结果（{selectableIds.length}）
          </Button>
          <Button size="small" disabled={selectedIds.length === 0} onClick={() => setSelectedIds([])}>
            清空选择
          </Button>
          <Text type="secondary" style={{ fontSize: 12 }}>
            共 {visibleAssets.length} 张，{takenHint}的已置灰不可选
          </Text>
        </Space>

        {loadError !== null && <Alert type="error" showIcon message={loadError} />}
        {truncated && (
          <Alert
            type="warning"
            showIcon
            message={`结果超过 ${PAGE_SIZE * MAX_PAGES} 张，仅载入前 ${PAGE_SIZE * MAX_PAGES} 张。请用筛选条件收窄范围。`}
          />
        )}

        <div style={{ maxHeight: 420, overflowY: "auto" }}>
          {isLoading ? (
            <div style={{ textAlign: "center", padding: 40 }}>
              <Spin />
            </div>
          ) : visibleAssets.length === 0 ? (
            <Empty
              image={Empty.PRESENTED_IMAGE_SIMPLE}
              description="当前条件下没有图片素材。换个筛选条件，或先去图片工坊上传。"
              style={{ padding: 32 }}
            />
          ) : (
            <Row gutter={[10, 10]}>
              {visibleAssets.map((asset) => {
                const isUsed = taken(asset);
                const checked = selectedIds.includes(asset.id);
                const src = assetThumbUrl(asset);
                return (
                  <Col xs={12} sm={8} md={6} lg={4} key={asset.id}>
                    <Tooltip title={isUsed ? takenHint : asset.original_name || asset.local_path}>
                      <div
                        onClick={() => { if (!isUsed) toggle(asset.id); }}
                        style={{
                          border: `1px solid ${checked ? "#1668dc" : "#303030"}`,
                          borderRadius: 4,
                          padding: 6,
                          cursor: isUsed ? "not-allowed" : "pointer",
                          opacity: isUsed ? 0.4 : 1,
                          background: checked ? "rgba(22,104,220,.12)" : undefined,
                        }}
                      >
                        <div
                          style={{
                            height: 92,
                            display: "flex",
                            alignItems: "center",
                            justifyContent: "center",
                            background: "#1a1a1a",
                            borderRadius: 4,
                            overflow: "hidden",
                            position: "relative",
                          }}
                        >
                          {isRenderableImageSrc(src) ? (
                            <img
                              src={src}
                              alt={asset.original_name || asset.local_path}
                              style={{ maxHeight: 92, maxWidth: "100%", objectFit: "contain" }}
                              referrerPolicy="no-referrer"
                            />
                          ) : (
                            <PictureOutlined style={{ fontSize: 24, color: "#555" }} />
                          )}
                          <span
                            style={{ position: "absolute", top: 4, left: 4 }}
                            onClick={(event) => event.stopPropagation()}
                          >
                            <Checkbox
                              checked={checked}
                              disabled={isUsed}
                              onChange={() => toggle(asset.id)}
                            />
                          </span>
                          {asset.source === "ai" && (
                            <Tag
                              color="purple"
                              style={{ position: "absolute", top: 4, right: 4, fontSize: 10, margin: 0 }}
                            >
                              AI
                            </Tag>
                          )}
                          {isUsed && (
                            <Tag
                              color="default"
                              style={{ position: "absolute", bottom: 4, right: 4, fontSize: 10, margin: 0 }}
                            >
                              {takenBadge}
                            </Tag>
                          )}
                        </div>
                        <Text ellipsis style={{ fontSize: 11, display: "block", marginTop: 4 }}>
                          {asset.original_name || asset.local_path || `素材 ${asset.id}`}
                        </Text>
                        <Text type="secondary" style={{ fontSize: 10 }}>
                          {formatFileSize(asset.file_size)}
                        </Text>
                      </div>
                    </Tooltip>
                  </Col>
                );
              })}
            </Row>
          )}
        </div>
      </Space>
    </Modal>
  );
}
