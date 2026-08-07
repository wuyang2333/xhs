import {
  AppstoreOutlined,
  DeleteOutlined,
  EditOutlined,
  FolderAddOutlined,
  FolderOutlined,
  PictureOutlined,
  PlusOutlined,
  SwapOutlined,
} from "@ant-design/icons";
import {
  Button,
  Card,
  Dropdown,
  Empty,
  Form,
  Input,
  Modal,
  Space,
  Tree,
  TreeSelect,
  Typography,
  message as antMessage,
} from "antd";
import type { MenuProps, TreeDataNode, TreeProps } from "antd";
import { useMemo, useState } from "react";

import {
  createAssetFolder,
  deleteAssetFolder,
  updateAssetFolder,
} from "../../lib/api";
import type { AssetFolder } from "../../types";
import {
  buildFolderSelectOptions,
  buildFolderTree,
  errorDetail,
  errorStatus,
  flattenFolderTree,
  subtreeIds,
  type FolderNode,
  type FolderSelectOption,
} from "./asset-utils";

const { Text } = Typography;

type Props = {
  folders: AssetFolder[];
  selectedFolderId: number | null;
  onSelectFolder: (folderId: number | null) => void;
  /** 文件夹增删改后通知外层重新拉取列表。 */
  onFoldersChanged: () => Promise<void> | void;
  /** 打开某文件夹的封面选择器，由外层负责取该文件夹下的素材。 */
  onPickCover: (node: FolderNode) => void;
  /** 素材总数，展示在「全部素材」一行。 */
  totalAssetCount: number;
};

export function AssetFolderTree({
  folders,
  selectedFolderId,
  onSelectFolder,
  onFoldersChanged,
  onPickCover,
  totalAssetCount,
}: Props) {
  const [expandedKeys, setExpandedKeys] = useState<React.Key[]>([]);
  const [createOpen, setCreateOpen] = useState(false);
  const [createParent, setCreateParent] = useState<FolderNode | null>(null);
  const [createName, setCreateName] = useState("");
  const [renameTarget, setRenameTarget] = useState<FolderNode | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const [moveTarget, setMoveTarget] = useState<FolderNode | null>(null);
  const [moveParentId, setMoveParentId] = useState<number | null>(null);
  const [isBusy, setIsBusy] = useState(false);

  const roots = useMemo(() => buildFolderTree(folders), [folders]);
  const flat = useMemo(() => flattenFolderTree(roots), [roots]);
  const nodeById = useMemo(() => {
    const map = new Map<number, FolderNode>();
    for (const node of flat) map.set(node.folder.id, node);
    return map;
  }, [flat]);

  function reportFolderError(error: unknown, fallback: string): void {
    const status = errorStatus(error);
    if (status === 409) {
      antMessage.error("同一层级下已存在同名文件夹，请换个名字。");
      return;
    }
    if (status === 400) {
      const detail = errorDetail(error) ?? "";
      antMessage.error(
        detail.includes("cycle") || detail.includes("own parent")
          ? "不能把文件夹移动到它自己或它的子文件夹下。"
          : fallback,
      );
      return;
    }
    antMessage.error(fallback);
  }

  async function handleCreate(): Promise<void> {
    const name = createName.trim();
    if (!name) {
      antMessage.warning("请填写文件夹名称。");
      return;
    }
    setIsBusy(true);
    try {
      const created = await createAssetFolder(
        { name, parent_id: createParent ? createParent.folder.id : null },
        true,
      );
      antMessage.success(`已新建文件夹「${created.name}」。`);
      setCreateOpen(false);
      setCreateName("");
      if (createParent) {
        setExpandedKeys((keys) =>
          keys.includes(createParent.folder.id) ? keys : [...keys, createParent.folder.id],
        );
      }
      await onFoldersChanged();
    } catch (error) {
      reportFolderError(error, "新建文件夹失败。");
    } finally {
      setIsBusy(false);
    }
  }

  async function handleRename(): Promise<void> {
    if (!renameTarget) return;
    const name = renameValue.trim();
    if (!name) {
      antMessage.warning("请填写文件夹名称。");
      return;
    }
    if (name === renameTarget.folder.name) {
      setRenameTarget(null);
      return;
    }
    setIsBusy(true);
    try {
      await updateAssetFolder(renameTarget.folder.id, { name }, true);
      antMessage.success("文件夹已重命名。");
      setRenameTarget(null);
      await onFoldersChanged();
    } catch (error) {
      reportFolderError(error, "重命名失败。");
    } finally {
      setIsBusy(false);
    }
  }

  async function moveFolder(folderId: number, parentId: number | null): Promise<boolean> {
    try {
      await updateAssetFolder(folderId, { parent_id: parentId }, true);
      antMessage.success("文件夹已移动。");
      await onFoldersChanged();
      return true;
    } catch (error) {
      reportFolderError(error, "移动文件夹失败。");
      return false;
    }
  }

  async function handleMoveConfirm(): Promise<void> {
    if (!moveTarget) return;
    if (moveParentId === moveTarget.folder.parent_id) {
      setMoveTarget(null);
      return;
    }
    setIsBusy(true);
    const ok = await moveFolder(moveTarget.folder.id, moveParentId);
    setIsBusy(false);
    if (ok) setMoveTarget(null);
  }

  function confirmDelete(node: FolderNode): void {
    Modal.confirm({
      title: `删除文件夹「${node.folder.name}」？`,
      content: "只能删除空文件夹。文件夹内如有子文件夹或素材，请先移出。",
      okText: "删除",
      okButtonProps: { danger: true },
      cancelText: "取消",
      onOk: async () => {
        try {
          await deleteAssetFolder(node.folder.id, true);
          antMessage.success("文件夹已删除。");
          if (selectedFolderId === node.folder.id) onSelectFolder(null);
          await onFoldersChanged();
        } catch (error) {
          if (errorStatus(error) === 409) {
            antMessage.error("文件夹非空，请先移出其中内容。");
            return;
          }
          antMessage.error("删除文件夹失败。");
        }
      },
    });
  }

  function folderMenu(node: FolderNode): MenuProps["items"] {
    return [
      {
        key: "child",
        icon: <PlusOutlined />,
        label: "新建子文件夹",
        onClick: () => {
          setCreateParent(node);
          setCreateName("");
          setCreateOpen(true);
        },
      },
      {
        key: "rename",
        icon: <EditOutlined />,
        label: "重命名",
        onClick: () => {
          setRenameTarget(node);
          setRenameValue(node.folder.name);
        },
      },
      {
        key: "move",
        icon: <SwapOutlined />,
        label: "移动到…",
        onClick: () => {
          setMoveTarget(node);
          setMoveParentId(node.folder.parent_id);
        },
      },
      {
        key: "cover",
        icon: <PictureOutlined />,
        label: "设置封面…",
        onClick: () => onPickCover(node),
      },
      { type: "divider" },
      {
        key: "delete",
        icon: <DeleteOutlined />,
        label: "删除",
        danger: true,
        onClick: () => confirmDelete(node),
      },
    ];
  }

  // 不做 memo：节点 title 里闭包了 folderMenu 等回调，memo 住反而会拿到过期的闭包，
  // 而重建这棵树的成本可以忽略。
  const treeData: TreeDataNode[] = (() => {
    const toNode = (node: FolderNode): TreeDataNode => ({
      key: node.folder.id,
      icon: <FolderOutlined />,
      title: (
        <Dropdown menu={{ items: folderMenu(node) }} trigger={["contextMenu"]}>
          <Space size={4}>
            <span>{node.folder.name}</span>
            {node.folder.cover_asset_id !== null && (
              <PictureOutlined style={{ fontSize: 10, color: "#1668dc" }} />
            )}
          </Space>
        </Dropdown>
      ),
      children: node.children.map(toNode),
    });
    return roots.map(toNode);
  })();

  const handleDrop: NonNullable<TreeProps["onDrop"]> = (info) => {
    const dragId = Number(info.dragNode.key);
    const dropId = Number(info.node.key);
    if (!Number.isFinite(dragId) || !Number.isFinite(dropId)) return;
    const dragged = nodeById.get(dragId);
    if (!dragged) return;
    // dropToGap 表示落在节点之间，视作「移到目标的同级」；否则落在节点上，视作「移入目标内部」。
    const dropNode = nodeById.get(dropId);
    const nextParentId = info.dropToGap
      ? (dropNode ? dropNode.folder.parent_id : null)
      : dropId;
    if (nextParentId === dragged.folder.parent_id) return;
    if (nextParentId !== null && subtreeIds(dragged).includes(nextParentId)) {
      antMessage.error("不能把文件夹移动到它自己或它的子文件夹下。");
      return;
    }
    void moveFolder(dragId, nextParentId);
  };

  const moveOptions = useMemo<FolderSelectOption[]>(() => {
    const excluded = moveTarget ? new Set(subtreeIds(moveTarget)) : new Set<number>();
    return buildFolderSelectOptions(roots, excluded);
  }, [roots, moveTarget]);

  return (
    <Card
      size="small"
      title={
        <Space>
          <FolderOutlined /> 文件夹
        </Space>
      }
      extra={
        <Button
          type="text"
          size="small"
          icon={<FolderAddOutlined />}
          onClick={() => {
            setCreateParent(null);
            setCreateName("");
            setCreateOpen(true);
          }}
        >
          新建
        </Button>
      }
      styles={{ body: { padding: 8 } }}
    >
      <Button
        type={selectedFolderId === null ? "primary" : "text"}
        size="small"
        block
        icon={<AppstoreOutlined />}
        onClick={() => onSelectFolder(null)}
        style={{ textAlign: "left", marginBottom: 6 }}
      >
        全部素材（{totalAssetCount}）
      </Button>

      {roots.length === 0 ? (
        <Empty
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          description="还没有文件夹。新建一个，或上传时直接填写路径。"
          style={{ padding: "16px 8px" }}
        />
      ) : (
        <Tree
          showIcon
          blockNode
          draggable
          selectedKeys={selectedFolderId === null ? [] : [selectedFolderId]}
          expandedKeys={expandedKeys}
          onExpand={setExpandedKeys}
          treeData={treeData}
          onDrop={handleDrop}
          onSelect={(keys) => {
            const first = keys[0];
            onSelectFolder(typeof first === "number" ? first : null);
          }}
        />
      )}

      <Text type="secondary" style={{ fontSize: 11, display: "block", marginTop: 8 }}>
        右键文件夹可重命名、移动、设封面、删除；直接拖拽也能改层级。
      </Text>

      <Modal
        title={createParent ? `在「${createParent.folder.name}」下新建子文件夹` : "新建文件夹"}
        open={createOpen}
        onCancel={() => setCreateOpen(false)}
        onOk={handleCreate}
        confirmLoading={isBusy}
        okText="创建"
        cancelText="取消"
        destroyOnClose
      >
        <Input
          value={createName}
          onChange={(event) => setCreateName(event.target.value)}
          placeholder="文件夹名称，例如：坐浴盆"
          onPressEnter={handleCreate}
          autoFocus
        />
      </Modal>

      <Modal
        title="重命名文件夹"
        open={renameTarget !== null}
        onCancel={() => setRenameTarget(null)}
        onOk={handleRename}
        confirmLoading={isBusy}
        okText="保存"
        cancelText="取消"
        destroyOnClose
      >
        <Input
          value={renameValue}
          onChange={(event) => setRenameValue(event.target.value)}
          onPressEnter={handleRename}
          autoFocus
        />
      </Modal>

      <Modal
        title={moveTarget ? `移动「${moveTarget.folder.name}」` : "移动文件夹"}
        open={moveTarget !== null}
        onCancel={() => setMoveTarget(null)}
        onOk={handleMoveConfirm}
        confirmLoading={isBusy}
        okText="移动"
        cancelText="取消"
        destroyOnClose
      >
        <Form layout="vertical">
          <Form.Item label="目标父文件夹" help="留空表示移到顶层。自己与自己的子文件夹不会出现在选项里。">
            <TreeSelect<number, FolderSelectOption>
              allowClear
              style={{ width: "100%" }}
              value={moveParentId ?? undefined}
              onChange={(value) => setMoveParentId(typeof value === "number" ? value : null)}
              treeData={moveOptions}
              fieldNames={{ label: "title", value: "value", children: "children" }}
              treeDefaultExpandAll
              placeholder="顶层"
            />
          </Form.Item>
        </Form>
      </Modal>
    </Card>
  );
}
