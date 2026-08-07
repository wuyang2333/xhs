import { DeleteOutlined, HolderOutlined, PictureOutlined, StarOutlined } from "@ant-design/icons";
import { closestCenter, DndContext, PointerSensor, useSensor, useSensors } from "@dnd-kit/core";
import type { DragEndEvent } from "@dnd-kit/core";
import { arrayMove, rectSortingStrategy, SortableContext, useSortable } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import {
  Button,
  Card,
  Checkbox,
  Col,
  Empty,
  Popconfirm,
  Row,
  Space,
  Tag,
  Tooltip,
  Typography,
} from "antd";

import type { MediaAsset } from "../../types";
import { assetThumbUrl, formatFileSize, isRenderableImageSrc } from "./asset-utils";

const { Text } = Typography;

type CardProps = {
  asset: MediaAsset;
  index: number;
  selected: boolean;
  onToggleSelect: (assetId: number) => void;
  onDelete: (asset: MediaAsset) => void;
  onSetCover: (asset: MediaAsset) => void;
  isFolderCover: boolean;
  /** 拖拽是否可用；跨筛选条件时仍可用，排序作用于全局顺序。 */
  draggable: boolean;
};

function SortableAssetCard({
  asset,
  index,
  selected,
  onToggleSelect,
  onDelete,
  onSetCover,
  isFolderCover,
  draggable,
}: CardProps) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({
    id: asset.id,
    disabled: !draggable,
  });
  const src = assetThumbUrl(asset);

  return (
    <div
      ref={setNodeRef}
      style={{
        transform: CSS.Transform.toString(transform),
        transition,
        opacity: isDragging ? 0.5 : 1,
      }}
    >
      <Card
        size="small"
        hoverable
        styles={{ body: { padding: 8 } }}
        style={{ borderColor: selected ? "#1668dc" : undefined }}
      >
        <div style={{ position: "relative" }}>
          <div
            style={{
              height: 120,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              marginBottom: 6,
              overflow: "hidden",
              borderRadius: 4,
              background: "#1a1a1a",
              cursor: draggable ? "grab" : "pointer",
            }}
            {...attributes}
            {...listeners}
          >
            {isRenderableImageSrc(src) ? (
              <img
                src={src}
                alt={asset.original_name || asset.local_path}
                style={{
                  maxHeight: 120,
                  maxWidth: "100%",
                  objectFit: "contain",
                  pointerEvents: "none",
                }}
                referrerPolicy="no-referrer"
              />
            ) : (
              <PictureOutlined style={{ fontSize: 28, color: "#555" }} />
            )}
          </div>

          {/* 包一层拦掉指针事件，否则拖拽 sensor 会吃掉勾选框的点击。 */}
          <span
            onClick={(event) => event.stopPropagation()}
            onPointerDown={(event) => event.stopPropagation()}
            style={{
              position: "absolute",
              top: 4,
              left: 4,
              background: "rgba(0,0,0,.55)",
              borderRadius: 4,
              padding: "0 4px",
              lineHeight: 1,
            }}
          >
            <Checkbox checked={selected} onChange={() => onToggleSelect(asset.id)} />
          </span>

          {draggable && (
            <HolderOutlined
              style={{
                position: "absolute",
                top: 6,
                right: 6,
                color: "#bbb",
                background: "rgba(0,0,0,.55)",
                borderRadius: 4,
                padding: 2,
                pointerEvents: "none",
              }}
            />
          )}

          <Text
            style={{
              position: "absolute",
              bottom: 10,
              left: 4,
              fontSize: 10,
              background: "rgba(0,0,0,.6)",
              borderRadius: 4,
              padding: "0 4px",
            }}
          >
            #{index + 1}
          </Text>
        </div>

        <Text strong ellipsis style={{ fontSize: 12, display: "block" }}>
          {asset.original_name || asset.local_path || `素材 ${asset.id}`}
        </Text>
        <Text type="secondary" style={{ fontSize: 10 }}>
          {formatFileSize(asset.file_size)}
          {asset.asset_type === "video" ? " · 视频" : ""}
        </Text>

        {asset.tags.length > 0 && (
          <div style={{ marginTop: 4 }}>
            {asset.tags.map((tag) => (
              <Tag
                key={tag.id}
                color={tag.color || undefined}
                style={{ fontSize: 10, padding: "0 4px", marginInlineEnd: 4 }}
              >
                {tag.name}
              </Tag>
            ))}
          </div>
        )}

        <Space size={4} style={{ marginTop: 4, width: "100%" }}>
          <Tooltip
            title={
              asset.folder_id === null
                ? "素材需先归入文件夹才能设为该文件夹封面"
                : isFolderCover
                  ? "已是所在文件夹的封面"
                  : "设为所在文件夹封面"
            }
          >
            <Button
              type="text"
              size="small"
              icon={<StarOutlined style={{ color: isFolderCover ? "#faad14" : undefined }} />}
              disabled={asset.folder_id === null || isFolderCover}
              onClick={() => onSetCover(asset)}
            />
          </Tooltip>
          <Popconfirm
            title="删除这个素材？"
            description="已被草稿引用的素材无法删除。"
            okText="删除"
            cancelText="取消"
            okButtonProps={{ danger: true }}
            onConfirm={() => onDelete(asset)}
          >
            <Button type="text" danger size="small" icon={<DeleteOutlined />} />
          </Popconfirm>
        </Space>
      </Card>
    </div>
  );
}

type GridProps = {
  assets: MediaAsset[];
  selectedIds: number[];
  onSelectedIdsChange: (ids: number[]) => void;
  onDelete: (asset: MediaAsset) => void;
  onSetCover: (asset: MediaAsset) => void;
  /** 返回拖拽后的可见顺序，由外层投影回全量顺序再提交。 */
  onReorder: (orderedVisibleIds: number[]) => void;
  coverAssetIds: Set<number>;
  reorderEnabled: boolean;
};

export function AssetGrid({
  assets,
  selectedIds,
  onSelectedIdsChange,
  onDelete,
  onSetCover,
  onReorder,
  coverAssetIds,
  reorderEnabled,
}: GridProps) {
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 6 } }),
  );

  function handleDragEnd(event: DragEndEvent): void {
    const { active, over } = event;
    if (!over || active.id === over.id) return;
    const ids = assets.map((asset) => asset.id);
    const from = ids.indexOf(Number(active.id));
    const to = ids.indexOf(Number(over.id));
    if (from < 0 || to < 0) return;
    onReorder(arrayMove(ids, from, to));
  }

  function toggleSelect(assetId: number): void {
    onSelectedIdsChange(
      selectedIds.includes(assetId)
        ? selectedIds.filter((id) => id !== assetId)
        : [...selectedIds, assetId],
    );
  }

  if (assets.length === 0) {
    return (
      <Empty
        image={Empty.PRESENTED_IMAGE_SIMPLE}
        description="当前条件下没有素材。换个筛选条件，或先上传一批。"
        style={{ padding: 32 }}
      />
    );
  }

  return (
    <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={handleDragEnd}>
      <SortableContext items={assets.map((asset) => asset.id)} strategy={rectSortingStrategy}>
        <Row gutter={[12, 12]}>
          {assets.map((asset, index) => (
            <Col xs={12} sm={8} md={6} xl={4} key={asset.id}>
              <SortableAssetCard
                asset={asset}
                index={index}
                selected={selectedIds.includes(asset.id)}
                onToggleSelect={toggleSelect}
                onDelete={onDelete}
                onSetCover={onSetCover}
                isFolderCover={coverAssetIds.has(asset.id)}
                draggable={reorderEnabled}
              />
            </Col>
          ))}
        </Row>
      </SortableContext>
    </DndContext>
  );
}
