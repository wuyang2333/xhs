import {
  CheckCircleOutlined,
  DeleteOutlined,
  ExclamationCircleOutlined,
  EyeOutlined,
  HolderOutlined,
  LoadingOutlined,
  PictureOutlined,
  StarFilled,
  StarOutlined,
  SwapOutlined,
} from "@ant-design/icons";
import { closestCenter, DndContext, PointerSensor, useSensor, useSensors } from "@dnd-kit/core";
import type { DragEndEvent } from "@dnd-kit/core";
import { arrayMove, rectSortingStrategy, SortableContext, useSortable } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { Button, Empty, Popconfirm, Space, Tag, Tooltip, Typography } from "antd";

import type { DraftAsset } from "../../types";
import { assetThumbUrl, isRenderableImageSrc } from "../assets/asset-utils";

const { Text } = Typography;

/** 顺序落库状态。error 与 saving 都必须挡住「送发布」。 */
export type OrderSaveStatus = "idle" | "saving" | "saved" | "error";

type CardProps = {
  asset: DraftAsset;
  index: number;
  disabled: boolean;
  onSetCover: () => void;
  onRemove: () => void;
  onReplace: () => void;
  onView: () => void;
};

function SortableDraftAsset({
  asset,
  index,
  disabled,
  onSetCover,
  onRemove,
  onReplace,
  onView,
}: CardProps) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({
    id: asset.id,
    disabled,
  });
  const src = assetThumbUrl(asset);

  return (
    <div
      ref={setNodeRef}
      style={{
        transform: CSS.Transform.toString(transform),
        transition,
        opacity: isDragging ? 0.5 : 1,
        width: 116,
        border: `1px solid ${asset.is_cover ? "#faad14" : "#303030"}`,
        borderRadius: 6,
        padding: 6,
        background: "rgba(255,255,255,.02)",
      }}
    >
      <div
        style={{
          position: "relative",
          height: 92,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          background: "#1a1a1a",
          borderRadius: 4,
          overflow: "hidden",
          cursor: disabled ? "default" : "grab",
        }}
        {...attributes}
        {...listeners}
      >
        {isRenderableImageSrc(src) ? (
          <img
            src={src}
            alt={`第 ${index + 1} 张`}
            style={{ maxHeight: 92, maxWidth: "100%", objectFit: "contain", pointerEvents: "none" }}
            referrerPolicy="no-referrer"
          />
        ) : (
          <PictureOutlined style={{ fontSize: 24, color: "#555" }} />
        )}

        {/* 序号是用户核对顺序的主要依据，放最显眼处。 */}
        <span
          style={{
            position: "absolute",
            top: 4,
            left: 4,
            minWidth: 18,
            height: 18,
            lineHeight: "18px",
            textAlign: "center",
            borderRadius: 9,
            background: "#1668dc",
            color: "#fff",
            fontSize: 11,
            fontWeight: 600,
            padding: "0 5px",
            pointerEvents: "none",
          }}
        >
          {index + 1}
        </span>

        {!disabled && (
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

        {asset.is_cover && (
          <Tag
            color="warning"
            style={{ position: "absolute", bottom: 4, left: 4, fontSize: 10, margin: 0 }}
          >
            封面
          </Tag>
        )}
      </div>

      <Space size={0} style={{ marginTop: 4, width: "100%", justifyContent: "space-between" }}>
        <Tooltip title={asset.is_cover ? "已是封面" : "设为封面"}>
          <Button
            type="text"
            size="small"
            disabled={asset.is_cover || disabled}
            icon={
              asset.is_cover ? <StarFilled style={{ color: "#faad14" }} /> : <StarOutlined />
            }
            onClick={onSetCover}
            style={{ padding: "0 4px" }}
          />
        </Tooltip>
        <Tooltip title="替换图片">
          <Button
            type="text"
            size="small"
            disabled={disabled}
            icon={<SwapOutlined />}
            onClick={onReplace}
            style={{ padding: "0 4px" }}
          />
        </Tooltip>
        <Tooltip title="预览">
          <Button
            type="text"
            size="small"
            icon={<EyeOutlined />}
            onClick={onView}
            style={{ padding: "0 4px" }}
          />
        </Tooltip>
        <Popconfirm
          title="移除这张图片？"
          okText="移除"
          cancelText="取消"
          okButtonProps={{ danger: true }}
          onConfirm={onRemove}
        >
          <Tooltip title="移除">
            <Button
              type="text"
              danger
              size="small"
              disabled={disabled}
              icon={<DeleteOutlined />}
              style={{ padding: "0 4px" }}
            />
          </Tooltip>
        </Popconfirm>
      </Space>
    </div>
  );
}

export type DraftAssetOrderPanelProps = {
  /** 已按 sort_order 排好的图片素材。 */
  assets: DraftAsset[];
  status: OrderSaveStatus;
  /** 无草稿（素材还没落库）时禁用一切写操作。 */
  disabled: boolean;
  onReorder: (orderedImageIds: number[]) => void;
  onSetCover: (asset: DraftAsset) => void;
  onRemove: (asset: DraftAsset) => void;
  onReplace: (asset: DraftAsset) => void;
  onView: (src: string) => void;
};

export function OrderSaveIndicator({ status }: { status: OrderSaveStatus }) {
  if (status === "saving") {
    return (
      <Text type="warning" style={{ fontSize: 12 }}>
        <LoadingOutlined /> 顺序保存中…
      </Text>
    );
  }
  if (status === "saved") {
    return (
      <Text type="success" style={{ fontSize: 12 }}>
        <CheckCircleOutlined /> 顺序已保存
      </Text>
    );
  }
  if (status === "error") {
    return (
      <Text type="danger" style={{ fontSize: 12 }}>
        <ExclamationCircleOutlined /> 顺序保存失败，请重新拖动一次
      </Text>
    );
  }
  return null;
}

export function DraftAssetOrderPanel({
  assets,
  status,
  disabled,
  onReorder,
  onSetCover,
  onRemove,
  onReplace,
  onView,
}: DraftAssetOrderPanelProps) {
  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 5 } }));

  function handleDragEnd(event: DragEndEvent): void {
    const { active, over } = event;
    if (!over || active.id === over.id) return;
    const ids = assets.map((asset) => asset.id);
    const from = ids.indexOf(Number(active.id));
    const to = ids.indexOf(Number(over.id));
    if (from < 0 || to < 0) return;
    onReorder(arrayMove(ids, from, to));
  }

  if (assets.length === 0) {
    return (
      <Empty
        image={<PictureOutlined style={{ fontSize: 32, color: "#595959" }} />}
        imageStyle={{ height: 42 }}
        description="当前草稿还没有图片，从素材库选图或重新生成"
      />
    );
  }

  return (
    <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={handleDragEnd}>
      <SortableContext items={assets.map((asset) => asset.id)} strategy={rectSortingStrategy}>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 10, paddingTop: 4 }}>
          {assets.map((asset, index) => (
            <SortableDraftAsset
              key={asset.id}
              asset={asset}
              index={index}
              disabled={disabled || status === "saving"}
              onSetCover={() => onSetCover(asset)}
              onRemove={() => onRemove(asset)}
              onReplace={() => onReplace(asset)}
              onView={() => onView(assetThumbUrl(asset))}
            />
          ))}
        </div>
      </SortableContext>
    </DndContext>
  );
}
