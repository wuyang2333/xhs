import {
  AppstoreOutlined,
  ClearOutlined,
  ReloadOutlined,
  SearchOutlined,
  SettingOutlined,
  TagsOutlined,
} from "@ant-design/icons";
import {
  Alert,
  Button,
  Card,
  Col,
  Empty,
  Form,
  Input,
  Modal,
  Pagination,
  Row,
  Select,
  Space,
  Spin,
  Switch,
  Tooltip,
  TreeSelect,
  Typography,
  message as antMessage,
} from "antd";
import { useCallback, useEffect, useMemo, useState } from "react";

import {
  batchUpdateMediaAssets,
  deleteMediaAsset,
  fetchAssetFolders,
  fetchAssetScenes,
  fetchAssetTags,
  fetchMediaAssets,
  fetchProducts,
  reorderMediaAssets,
  updateAssetFolder,
} from "../../lib/api";
import type { AssetFolder, AssetScene, AssetTag, MediaAsset, Product } from "../../types";
import { AssetFolderTree } from "./asset-folder-tree";
import { AssetGrid } from "./asset-grid";
import { AssetTaxonomyDrawer } from "./asset-taxonomy-drawer";
import { AssetUploadPanel } from "./asset-upload-panel";
import {
  assetThumbUrl,
  buildFolderSelectOptions,
  buildFolderTree,
  errorStatus,
  isRenderableImageSrc,
  projectReorderOntoGlobal,
  type FolderNode,
  type FolderSelectOption,
} from "./asset-utils";

const { Text } = Typography;

const PAGE_SIZE_OPTIONS = ["20", "40", "60", "100"];
/** 后端 page_size 上限 100，取全量顺序时按这个步长翻页。 */
const MAX_PAGE_SIZE = 100;

type BatchMode = "folder" | "product" | "scene" | "tags";

export function AssetLibraryPanel() {
  const [folders, setFolders] = useState<AssetFolder[]>([]);
  const [products, setProducts] = useState<Product[]>([]);
  const [scenes, setScenes] = useState<AssetScene[]>([]);
  const [tags, setTags] = useState<AssetTag[]>([]);
  const [assets, setAssets] = useState<MediaAsset[]>([]);
  const [total, setTotal] = useState(0);

  const [folderId, setFolderId] = useState<number | null>(null);
  const [productId, setProductId] = useState<number | null>(null);
  const [sceneId, setSceneId] = useState<number | null>(null);
  const [tagId, setTagId] = useState<number | null>(null);
  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [recentOnly, setRecentOnly] = useState(false);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);

  const [selectedIds, setSelectedIds] = useState<number[]>([]);
  const [isLoadingAssets, setIsLoadingAssets] = useState(true);
  const [isLoadingMeta, setIsLoadingMeta] = useState(true);
  const [isReordering, setIsReordering] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [taxonomyOpen, setTaxonomyOpen] = useState(false);
  const [batchMode, setBatchMode] = useState<BatchMode | null>(null);
  const [batchFolderId, setBatchFolderId] = useState<number | null>(null);
  const [batchProductId, setBatchProductId] = useState<number | null>(null);
  const [batchSceneId, setBatchSceneId] = useState<number | null>(null);
  const [batchTagIds, setBatchTagIds] = useState<number[]>([]);
  const [isBatchSaving, setIsBatchSaving] = useState(false);

  const [coverTarget, setCoverTarget] = useState<FolderNode | null>(null);
  const [coverCandidates, setCoverCandidates] = useState<MediaAsset[]>([]);
  const [isLoadingCover, setIsLoadingCover] = useState(false);

  const folderTree = useMemo(() => buildFolderTree(folders), [folders]);
  const folderOptions = useMemo<FolderSelectOption[]>(
    () => buildFolderSelectOptions(folderTree),
    [folderTree],
  );
  const coverAssetIds = useMemo(() => {
    const ids = new Set<number>();
    for (const folder of folders) {
      if (folder.cover_asset_id !== null) ids.add(folder.cover_asset_id);
    }
    return ids;
  }, [folders]);

  const hasActiveFilter =
    folderId !== null ||
    productId !== null ||
    sceneId !== null ||
    tagId !== null ||
    search.trim() !== "" ||
    recentOnly;

  const loadTaxonomy = useCallback(async (): Promise<void> => {
    setIsLoadingMeta(true);
    try {
      const [folderList, productList, sceneList, tagList] = await Promise.all([
        fetchAssetFolders(),
        fetchProducts(),
        fetchAssetScenes(),
        fetchAssetTags(),
      ]);
      setFolders(folderList);
      setProducts(productList);
      setScenes(sceneList);
      setTags(tagList);
    } catch {
      setLoadError("素材库分类信息加载失败。");
    } finally {
      setIsLoadingMeta(false);
    }
  }, []);

  const loadAssets = useCallback(async (): Promise<void> => {
    setIsLoadingAssets(true);
    try {
      const result = await fetchMediaAssets({
        folder_id: folderId ?? undefined,
        product_id: productId ?? undefined,
        scene_id: sceneId ?? undefined,
        tag_id: tagId ?? undefined,
        search: search.trim() || undefined,
        recent: recentOnly || undefined,
        page,
        page_size: pageSize,
      });
      setAssets(result.items);
      setTotal(result.total);
      setLoadError(null);
    } catch {
      setLoadError("素材列表加载失败。");
    } finally {
      setIsLoadingAssets(false);
    }
  }, [folderId, productId, sceneId, tagId, search, recentOnly, page, pageSize]);

  useEffect(() => {
    void loadTaxonomy();
  }, [loadTaxonomy]);

  useEffect(() => {
    void loadAssets();
  }, [loadAssets]);

  // 筛选条件一变就回到第一页，否则会停在一个不存在的页码上看到空列表。
  useEffect(() => {
    setPage(1);
    setSelectedIds([]);
  }, [folderId, productId, sceneId, tagId, search, recentOnly, pageSize]);

  async function refreshAll(): Promise<void> {
    await Promise.all([loadTaxonomy(), loadAssets()]);
  }

  function resetFilters(): void {
    setFolderId(null);
    setProductId(null);
    setSceneId(null);
    setTagId(null);
    setSearchInput("");
    setSearch("");
    setRecentOnly(false);
  }

  async function handleDeleteAsset(asset: MediaAsset): Promise<void> {
    try {
      await deleteMediaAsset(asset.id, true);
      antMessage.success("素材已删除。");
      setSelectedIds((ids) => ids.filter((id) => id !== asset.id));
      await refreshAll();
    } catch (error) {
      antMessage.error(
        errorStatus(error) === 409
          ? "该素材仍被草稿引用，请先从草稿里移除再删除。"
          : "删除素材失败。",
      );
    }
  }

  /** 把某素材设为它所在文件夹的封面。后端要求封面必须属于该文件夹，否则 400。 */
  async function handleSetCover(asset: MediaAsset): Promise<void> {
    if (asset.folder_id === null) {
      antMessage.warning("请先把素材移动到某个文件夹，再设为该文件夹封面。");
      return;
    }
    try {
      await updateAssetFolder(asset.folder_id, { cover_asset_id: asset.id }, true);
      antMessage.success("已设为文件夹封面。");
      await loadTaxonomy();
    } catch (error) {
      antMessage.error(
        errorStatus(error) === 400
          ? "封面素材必须属于该文件夹。"
          : "设置封面失败。",
      );
    }
  }

  async function openCoverPicker(node: FolderNode): Promise<void> {
    setCoverTarget(node);
    setIsLoadingCover(true);
    try {
      const result = await fetchMediaAssets({ folder_id: node.folder.id, page_size: MAX_PAGE_SIZE });
      setCoverCandidates(result.items);
    } catch {
      setCoverCandidates([]);
      antMessage.error("读取该文件夹素材失败。");
    } finally {
      setIsLoadingCover(false);
    }
  }

  async function applyCover(assetId: number | null): Promise<void> {
    if (!coverTarget) return;
    try {
      await updateAssetFolder(coverTarget.folder.id, { cover_asset_id: assetId }, true);
      antMessage.success(assetId === null ? "已清除文件夹封面。" : "文件夹封面已更新。");
      setCoverTarget(null);
      await loadTaxonomy();
    } catch (error) {
      antMessage.error(
        errorStatus(error) === 400 ? "封面素材必须属于该文件夹。" : "设置封面失败。",
      );
    }
  }

  function openBatch(mode: BatchMode): void {
    if (selectedIds.length === 0) {
      antMessage.warning("请先勾选素材。");
      return;
    }
    setBatchFolderId(folderId);
    setBatchProductId(null);
    setBatchSceneId(null);
    setBatchTagIds([]);
    setBatchMode(mode);
  }

  /**
   * 批量改属性。后端只写 payload 里显式出现的字段，
   * 所以每次只带当前这一项，null 表示清空关联。
   */
  async function handleBatchSave(): Promise<void> {
    if (!batchMode || selectedIds.length === 0) return;
    setIsBatchSaving(true);
    try {
      const payload: Parameters<typeof batchUpdateMediaAssets>[0] = { asset_ids: selectedIds };
      if (batchMode === "folder") payload.folder_id = batchFolderId;
      if (batchMode === "product") payload.product_id = batchProductId;
      if (batchMode === "scene") payload.scene_id = batchSceneId;
      if (batchMode === "tags") payload.tag_ids = batchTagIds;
      const result = await batchUpdateMediaAssets(payload);
      antMessage.success(`已更新 ${result.updated_count} 个素材。`);
      setBatchMode(null);
      setSelectedIds([]);
      await refreshAll();
    } catch {
      antMessage.error("批量更新失败。");
    } finally {
      setIsBatchSaving(false);
    }
  }

  /** 取全量素材 id（按 sort_order 升序），reorder 接口要求提交完整集合。 */
  async function fetchAllAssetIds(): Promise<number[]> {
    const ids: number[] = [];
    let current = 1;
    for (;;) {
      const result = await fetchMediaAssets({ page: current, page_size: MAX_PAGE_SIZE });
      ids.push(...result.items.map((item) => item.id));
      if (ids.length >= result.total || result.items.length === 0) break;
      current += 1;
    }
    return ids;
  }

  async function handleReorder(orderedVisibleIds: number[]): Promise<void> {
    const before = assets.map((asset) => asset.id);
    // 先乐观更新，接口失败再回滚成后端的真实顺序。
    const byId = new Map(assets.map((asset) => [asset.id, asset]));
    const optimistic = orderedVisibleIds
      .map((id) => byId.get(id))
      .filter((asset): asset is MediaAsset => asset !== undefined);
    setAssets(optimistic);
    setIsReordering(true);
    try {
      const globalIds = await fetchAllAssetIds();
      const projected = projectReorderOntoGlobal(globalIds, before, orderedVisibleIds);
      if (!projected) {
        antMessage.error("排序已过期，请刷新后重试。");
        await loadAssets();
        return;
      }
      await reorderMediaAssets(projected, true);
      antMessage.success("排序已保存。");
      await loadAssets();
    } catch {
      antMessage.error("排序保存失败，已恢复原顺序。");
      await loadAssets();
    } finally {
      setIsReordering(false);
    }
  }

  const allVisibleSelected = assets.length > 0 && selectedIds.length === assets.length;

  return (
    <div>
      {loadError && (
        <Alert
          type="error"
          message={loadError}
          showIcon
          closable
          onClose={() => setLoadError(null)}
          style={{ marginBottom: 12 }}
        />
      )}

      <Row gutter={[16, 16]}>
        <Col xs={24} lg={7} xl={6}>
          <Space direction="vertical" size={12} style={{ width: "100%" }}>
            <AssetFolderTree
              folders={folders}
              selectedFolderId={folderId}
              onSelectFolder={setFolderId}
              onFoldersChanged={refreshAll}
              onPickCover={(node) => void openCoverPicker(node)}
              totalAssetCount={total}
            />
            <AssetUploadPanel
              folders={folders}
              products={products}
              scenes={scenes}
              tags={tags}
              defaultFolderId={folderId}
              onUploaded={refreshAll}
            />
          </Space>
        </Col>

        <Col xs={24} lg={17} xl={18}>
          <Card
            size="small"
            styles={{ body: { padding: 12 } }}
            title={
              <Space>
                <AppstoreOutlined /> 素材（{total}）
              </Space>
            }
            extra={
              <Space>
                <Button
                  size="small"
                  icon={<TagsOutlined />}
                  onClick={() => setTaxonomyOpen(true)}
                >
                  产品 / 场景 / 标签
                </Button>
                <Button
                  size="small"
                  icon={<ReloadOutlined />}
                  loading={isLoadingAssets || isLoadingMeta}
                  onClick={() => void refreshAll()}
                >
                  刷新
                </Button>
              </Space>
            }
          >
            <Row gutter={[8, 8]} align="middle" style={{ marginBottom: 12 }}>
              <Col xs={24} md={8}>
                <Input
                  allowClear
                  value={searchInput}
                  onChange={(event) => setSearchInput(event.target.value)}
                  onPressEnter={() => setSearch(searchInput)}
                  onClear={() => setSearch("")}
                  prefix={<SearchOutlined />}
                  placeholder="搜索文件名或链接，回车生效"
                />
              </Col>
              <Col xs={12} md={4}>
                <Select<number>
                  allowClear
                  style={{ width: "100%" }}
                  value={productId ?? undefined}
                  onChange={(value) => setProductId(value ?? null)}
                  placeholder="产品"
                  options={products.map((item) => ({ value: item.id, label: item.name }))}
                />
              </Col>
              <Col xs={12} md={4}>
                <Select<number>
                  allowClear
                  style={{ width: "100%" }}
                  value={sceneId ?? undefined}
                  onChange={(value) => setSceneId(value ?? null)}
                  placeholder="场景"
                  options={scenes.map((item) => ({ value: item.id, label: item.name }))}
                />
              </Col>
              <Col xs={12} md={4}>
                <Select<number>
                  allowClear
                  style={{ width: "100%" }}
                  value={tagId ?? undefined}
                  onChange={(value) => setTagId(value ?? null)}
                  placeholder="标签"
                  options={tags.map((item) => ({ value: item.id, label: item.name }))}
                />
              </Col>
              <Col xs={12} md={4}>
                <Space size={6}>
                  <Tooltip title="只看 30 天内创建或使用过的素材">
                    <Space size={4}>
                      <Switch size="small" checked={recentOnly} onChange={setRecentOnly} />
                      <Text style={{ fontSize: 12 }}>最近</Text>
                    </Space>
                  </Tooltip>
                  {hasActiveFilter && (
                    <Tooltip title="清空全部筛选">
                      <Button size="small" type="text" icon={<ClearOutlined />} onClick={resetFilters} />
                    </Tooltip>
                  )}
                </Space>
              </Col>
            </Row>

            <Row justify="space-between" align="middle" style={{ marginBottom: 12 }}>
              <Col>
                <Space size={6} wrap>
                  <Button
                    size="small"
                    onClick={() =>
                      setSelectedIds(allVisibleSelected ? [] : assets.map((asset) => asset.id))
                    }
                    disabled={assets.length === 0}
                  >
                    {allVisibleSelected ? "取消全选" : "全选本页"}
                  </Button>
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    已选 {selectedIds.length} 个
                  </Text>
                  <Button size="small" disabled={selectedIds.length === 0} onClick={() => openBatch("folder")}>
                    移动到文件夹
                  </Button>
                  <Button size="small" disabled={selectedIds.length === 0} onClick={() => openBatch("product")}>
                    改产品
                  </Button>
                  <Button size="small" disabled={selectedIds.length === 0} onClick={() => openBatch("scene")}>
                    改场景
                  </Button>
                  <Button size="small" disabled={selectedIds.length === 0} onClick={() => openBatch("tags")}>
                    改标签
                  </Button>
                </Space>
              </Col>
              <Col>
                {isReordering && (
                  <Space size={4}>
                    <Spin size="small" />
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      正在保存排序…
                    </Text>
                  </Space>
                )}
              </Col>
            </Row>

            {hasActiveFilter && assets.length > 1 && (
              <Text type="secondary" style={{ fontSize: 11, display: "block", marginBottom: 8 }}>
                当前有筛选条件。拖拽只调整这些素材的相对顺序，其余素材位置不变。
              </Text>
            )}

            {isLoadingAssets ? (
              <div style={{ textAlign: "center", padding: 48 }}>
                <Spin tip="正在加载素材..." />
              </div>
            ) : (
              <AssetGrid
                assets={assets}
                selectedIds={selectedIds}
                onSelectedIdsChange={setSelectedIds}
                onDelete={(asset) => void handleDeleteAsset(asset)}
                onSetCover={(asset) => void handleSetCover(asset)}
                onReorder={(ids) => void handleReorder(ids)}
                coverAssetIds={coverAssetIds}
                reorderEnabled={!isReordering && assets.length > 1}
              />
            )}

            {total > 0 && (
              <Row justify="end" style={{ marginTop: 12 }}>
                <Pagination
                  current={page}
                  pageSize={pageSize}
                  total={total}
                  showSizeChanger
                  pageSizeOptions={PAGE_SIZE_OPTIONS}
                  onChange={(nextPage, nextSize) => {
                    if (nextSize !== pageSize) {
                      setPageSize(nextSize);
                      return;
                    }
                    setPage(nextPage);
                    setSelectedIds([]);
                  }}
                  showTotal={(count) => `共 ${count} 个素材`}
                  size="small"
                />
              </Row>
            )}
          </Card>
        </Col>
      </Row>

      <AssetTaxonomyDrawer
        open={taxonomyOpen}
        onClose={() => setTaxonomyOpen(false)}
        products={products}
        scenes={scenes}
        tags={tags}
        onChanged={refreshAll}
      />

      <Modal
        title={`批量修改 ${selectedIds.length} 个素材`}
        open={batchMode !== null}
        onCancel={() => setBatchMode(null)}
        onOk={handleBatchSave}
        confirmLoading={isBatchSaving}
        okText="应用"
        cancelText="取消"
        destroyOnClose
      >
        <Form layout="vertical">
          {batchMode === "folder" && (
            <Form.Item label="移动到文件夹" help="留空表示移出文件夹，素材本身保留。">
              <TreeSelect<number, FolderSelectOption>
                allowClear
                style={{ width: "100%" }}
                value={batchFolderId ?? undefined}
                onChange={(value) => setBatchFolderId(typeof value === "number" ? value : null)}
                treeData={folderOptions}
                fieldNames={{ label: "title", value: "value", children: "children" }}
                treeDefaultExpandAll
                placeholder="不归入文件夹"
              />
            </Form.Item>
          )}
          {batchMode === "product" && (
            <Form.Item label="产品" help="留空表示清除产品关联。">
              <Select<number>
                allowClear
                style={{ width: "100%" }}
                value={batchProductId ?? undefined}
                onChange={(value) => setBatchProductId(value ?? null)}
                placeholder="清除产品"
                options={products.map((item) => ({ value: item.id, label: item.name }))}
              />
            </Form.Item>
          )}
          {batchMode === "scene" && (
            <Form.Item label="场景" help="留空表示清除场景关联。">
              <Select<number>
                allowClear
                style={{ width: "100%" }}
                value={batchSceneId ?? undefined}
                onChange={(value) => setBatchSceneId(value ?? null)}
                placeholder="清除场景"
                options={scenes.map((item) => ({ value: item.id, label: item.name }))}
              />
            </Form.Item>
          )}
          {batchMode === "tags" && (
            <Form.Item label="标签" help="这里选定的标签会整体替换所选素材的原有标签；留空表示清空标签。">
              <Select<number[]>
                mode="multiple"
                allowClear
                style={{ width: "100%" }}
                value={batchTagIds}
                onChange={(value) => setBatchTagIds(value ?? [])}
                placeholder="清空标签"
                options={tags.map((item) => ({ value: item.id, label: item.name }))}
              />
            </Form.Item>
          )}
        </Form>
      </Modal>

      <Modal
        title={coverTarget ? `设置「${coverTarget.folder.name}」的封面` : "设置文件夹封面"}
        open={coverTarget !== null}
        onCancel={() => setCoverTarget(null)}
        footer={
          <Space>
            <Button onClick={() => void applyCover(null)}>清除封面</Button>
            <Button onClick={() => setCoverTarget(null)}>取消</Button>
          </Space>
        }
        width={640}
        destroyOnClose
      >
        {isLoadingCover ? (
          <div style={{ textAlign: "center", padding: 32 }}>
            <Spin tip="正在读取该文件夹素材..." />
          </div>
        ) : coverCandidates.length === 0 ? (
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description="该文件夹内还没有素材。封面必须是该文件夹内的素材，请先上传或移入。"
            style={{ padding: 24 }}
          />
        ) : (
          <>
            <Text type="secondary" style={{ fontSize: 12, display: "block", marginBottom: 8 }}>
              点选一张作为封面。只能选该文件夹内的素材。
            </Text>
            <Row gutter={[8, 8]}>
              {coverCandidates.map((asset) => {
                const src = assetThumbUrl(asset);
                const isCurrent = coverTarget?.folder.cover_asset_id === asset.id;
                return (
                  <Col span={6} key={asset.id}>
                    <div
                      onClick={() => void applyCover(asset.id)}
                      style={{
                        cursor: "pointer",
                        borderRadius: 4,
                        overflow: "hidden",
                        border: `2px solid ${isCurrent ? "#faad14" : "#333"}`,
                        height: 80,
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "center",
                        background: "#1a1a1a",
                      }}
                    >
                      {isRenderableImageSrc(src) ? (
                        <img
                          src={src}
                          alt={asset.original_name || asset.local_path}
                          style={{ maxHeight: 80, maxWidth: "100%", objectFit: "contain" }}
                          referrerPolicy="no-referrer"
                        />
                      ) : (
                        <SettingOutlined style={{ fontSize: 20, color: "#555" }} />
                      )}
                    </div>
                  </Col>
                );
              })}
            </Row>
          </>
        )}
      </Modal>
    </div>
  );
}
