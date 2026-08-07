import { DeleteOutlined, EditOutlined, PlusOutlined } from "@ant-design/icons";
import {
  Button,
  Drawer,
  Empty,
  Input,
  List,
  Popconfirm,
  Space,
  Tabs,
  Tag,
  Typography,
  message as antMessage,
} from "antd";
import { useState } from "react";

import {
  createAssetScene,
  createAssetTag,
  createProduct,
  deleteAssetScene,
  deleteAssetTag,
  deleteProduct,
  updateAssetScene,
  updateAssetTag,
  updateProduct,
} from "../../lib/api";
import type { AssetScene, AssetTag, Product } from "../../types";
import { errorStatus } from "./asset-utils";

const { Text } = Typography;

type Props = {
  open: boolean;
  onClose: () => void;
  products: Product[];
  scenes: AssetScene[];
  tags: AssetTag[];
  /** 分类增删改后通知外层重拉分类与素材（删除会把素材上的关联清空）。 */
  onChanged: () => Promise<void> | void;
};

type EditState = { id: number; name: string; extra: string } | null;

export function AssetTaxonomyDrawer({ open, onClose, products, scenes, tags, onChanged }: Props) {
  const [newName, setNewName] = useState("");
  const [newExtra, setNewExtra] = useState("");
  const [editing, setEditing] = useState<EditState>(null);
  const [activeKey, setActiveKey] = useState("products");
  const [isBusy, setIsBusy] = useState(false);

  function resetInputs(): void {
    setNewName("");
    setNewExtra("");
    setEditing(null);
  }

  function reportError(error: unknown, fallback: string): void {
    antMessage.error(errorStatus(error) === 409 ? "名称已存在，请换一个。" : fallback);
  }

  async function runAction(action: () => Promise<void>, success: string, fallback: string): Promise<void> {
    setIsBusy(true);
    try {
      await action();
      antMessage.success(success);
      resetInputs();
      await onChanged();
    } catch (error) {
      reportError(error, fallback);
    } finally {
      setIsBusy(false);
    }
  }

  function renderList<T extends { id: number; name: string }>(
    items: T[],
    options: {
      emptyText: string;
      extraOf?: (item: T) => string;
      renderTitle?: (item: T) => React.ReactNode;
      onSave: (item: T, name: string, extra: string) => Promise<void>;
      onRemove: (item: T) => Promise<void>;
      removeHint: string;
    },
  ) {
    if (items.length === 0) {
      return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={options.emptyText} />;
    }
    return (
      <List
        size="small"
        dataSource={items}
        renderItem={(item) => {
          const isEditing = editing?.id === item.id;
          return (
            <List.Item
              actions={
                isEditing
                  ? [
                      <Button
                        key="save"
                        type="link"
                        size="small"
                        loading={isBusy}
                        onClick={() => {
                          const name = editing.name.trim();
                          if (!name) {
                            antMessage.warning("名称不能为空。");
                            return;
                          }
                          void runAction(
                            () => options.onSave(item, name, editing.extra),
                            "已保存。",
                            "保存失败。",
                          );
                        }}
                      >
                        保存
                      </Button>,
                      <Button key="cancel" type="link" size="small" onClick={() => setEditing(null)}>
                        取消
                      </Button>,
                    ]
                  : [
                      <Button
                        key="edit"
                        type="text"
                        size="small"
                        icon={<EditOutlined />}
                        onClick={() =>
                          setEditing({
                            id: item.id,
                            name: item.name,
                            extra: options.extraOf ? options.extraOf(item) : "",
                          })
                        }
                      />,
                      <Popconfirm
                        key="delete"
                        title={`删除「${item.name}」？`}
                        description={options.removeHint}
                        okText="删除"
                        cancelText="取消"
                        okButtonProps={{ danger: true }}
                        onConfirm={() =>
                          void runAction(() => options.onRemove(item), "已删除。", "删除失败。")
                        }
                      >
                        <Button type="text" danger size="small" icon={<DeleteOutlined />} />
                      </Popconfirm>,
                    ]
              }
            >
              {isEditing ? (
                <Space direction="vertical" size={4} style={{ width: "100%" }}>
                  <Input
                    value={editing.name}
                    onChange={(event) =>
                      setEditing({ ...editing, name: event.target.value })
                    }
                    size="small"
                  />
                  {options.extraOf && (
                    <Input
                      value={editing.extra}
                      onChange={(event) =>
                        setEditing({ ...editing, extra: event.target.value })
                      }
                      size="small"
                      placeholder={activeKey === "tags" ? "颜色，如 #ff4d4f 或 volcano" : "描述"}
                    />
                  )}
                </Space>
              ) : options.renderTitle ? (
                options.renderTitle(item)
              ) : (
                <Space direction="vertical" size={0}>
                  <Text>{item.name}</Text>
                  {options.extraOf && options.extraOf(item) && (
                    <Text type="secondary" style={{ fontSize: 11 }}>
                      {options.extraOf(item)}
                    </Text>
                  )}
                </Space>
              )}
            </List.Item>
          );
        }}
      />
    );
  }

  function renderCreateRow(placeholder: string, extraPlaceholder: string | null, onCreate: () => void) {
    return (
      <Space.Compact style={{ width: "100%", marginBottom: 12 }}>
        <Input
          value={newName}
          onChange={(event) => setNewName(event.target.value)}
          placeholder={placeholder}
          onPressEnter={onCreate}
        />
        {extraPlaceholder && (
          <Input
            value={newExtra}
            onChange={(event) => setNewExtra(event.target.value)}
            placeholder={extraPlaceholder}
            style={{ maxWidth: 140 }}
            onPressEnter={onCreate}
          />
        )}
        <Button type="primary" icon={<PlusOutlined />} loading={isBusy} onClick={onCreate}>
          添加
        </Button>
      </Space.Compact>
    );
  }

  function guardedCreate(create: () => Promise<void>): void {
    const name = newName.trim();
    if (!name) {
      antMessage.warning("请填写名称。");
      return;
    }
    void runAction(create, "已添加。", "添加失败。");
  }

  return (
    <Drawer
      title="产品 / 场景 / 标签管理"
      open={open}
      onClose={() => {
        resetInputs();
        onClose();
      }}
      width={480}
      destroyOnClose
    >
      <Tabs
        activeKey={activeKey}
        onChange={(key) => {
          setActiveKey(key);
          resetInputs();
        }}
        items={[
          {
            key: "products",
            label: `产品（${products.length}）`,
            children: (
              <>
                {renderCreateRow("产品名称，例如：坐浴盆", "描述（可选）", () =>
                  guardedCreate(async () => {
                    await createProduct({ name: newName.trim(), description: newExtra.trim() });
                  }),
                )}
                {renderList(products, {
                  emptyText: "还没有产品。",
                  extraOf: (item) => item.description,
                  removeHint: "删除后，素材上的产品关联会被清空，素材本身保留。",
                  onSave: async (item, name, extra) => {
                    await updateProduct(item.id, { name, description: extra.trim() });
                  },
                  onRemove: async (item) => {
                    await deleteProduct(item.id);
                  },
                })}
              </>
            ),
          },
          {
            key: "scenes",
            label: `场景（${scenes.length}）`,
            children: (
              <>
                {renderCreateRow("场景名称，例如：卫生间场景", null, () =>
                  guardedCreate(async () => {
                    await createAssetScene({ name: newName.trim() });
                  }),
                )}
                {renderList(scenes, {
                  emptyText: "还没有场景。",
                  removeHint: "删除后，素材上的场景关联会被清空，素材本身保留。",
                  onSave: async (item, name) => {
                    await updateAssetScene(item.id, { name });
                  },
                  onRemove: async (item) => {
                    await deleteAssetScene(item.id);
                  },
                })}
              </>
            ),
          },
          {
            key: "tags",
            label: `标签（${tags.length}）`,
            children: (
              <>
                {renderCreateRow("标签名称，例如：实拍", "颜色，如 #ff4d4f", () =>
                  guardedCreate(async () => {
                    await createAssetTag({ name: newName.trim(), color: newExtra.trim() });
                  }),
                )}
                {renderList(tags, {
                  emptyText: "还没有标签。",
                  extraOf: (item) => item.color,
                  renderTitle: (item) => (
                    <Space>
                      <Tag color={item.color || undefined}>{item.name}</Tag>
                      {item.color && (
                        <Text type="secondary" style={{ fontSize: 11 }}>
                          {item.color}
                        </Text>
                      )}
                    </Space>
                  ),
                  removeHint: "删除后，素材上的该标签关联会被移除，素材本身保留。",
                  onSave: async (item, name, extra) => {
                    await updateAssetTag(item.id, { name, color: extra.trim() });
                  },
                  onRemove: async (item) => {
                    await deleteAssetTag(item.id);
                  },
                })}
              </>
            ),
          },
        ]}
      />
    </Drawer>
  );
}
