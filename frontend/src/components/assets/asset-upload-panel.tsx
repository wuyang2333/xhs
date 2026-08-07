import { InboxOutlined, UploadOutlined } from "@ant-design/icons";
import {
  Button,
  Card,
  Col,
  Form,
  Input,
  Progress,
  Row,
  Segmented,
  Select,
  Space,
  TreeSelect,
  Typography,
  Upload,
  message as antMessage,
} from "antd";
import { useEffect, useMemo, useState } from "react";

import { createMediaAsset, uploadAssetFile } from "../../lib/api";
import type { AssetFolder, AssetScene, AssetTag, Product } from "../../types";
import {
  buildFolderSelectOptions,
  buildFolderTree,
  type FolderSelectOption,
} from "./asset-utils";

const { Text } = Typography;

type TargetMode = "folder" | "path";

type Props = {
  folders: AssetFolder[];
  products: Product[];
  scenes: AssetScene[];
  tags: AssetTag[];
  /** 当前选中的文件夹，作为上传目标的默认值。 */
  defaultFolderId: number | null;
  /** 上传结束后通知外层刷新文件夹与素材列表。 */
  onUploaded: () => Promise<void> | void;
};

export function AssetUploadPanel({
  folders,
  products,
  scenes,
  tags,
  defaultFolderId,
  onUploaded,
}: Props) {
  const [targetMode, setTargetMode] = useState<TargetMode>("folder");
  const [folderId, setFolderId] = useState<number | null>(defaultFolderId);
  const [folderPath, setFolderPath] = useState("");
  const [productId, setProductId] = useState<number | null>(null);
  const [sceneId, setSceneId] = useState<number | null>(null);
  const [tagIds, setTagIds] = useState<number[]>([]);
  const [totalCount, setTotalCount] = useState(0);
  const [doneCount, setDoneCount] = useState(0);
  const [uploadingCount, setUploadingCount] = useState(0);
  const [uploadFailedCount, setUploadFailedCount] = useState(0);
  const [failedNames, setFailedNames] = useState<string[]>([]);

  const folderOptions = useMemo<FolderSelectOption[]>(
    () => buildFolderSelectOptions(buildFolderTree(folders)),
    [folders],
  );

  // 左侧树换选中项时，把上传目标跟过去，省得每次手动再选一遍。
  useEffect(() => {
    setFolderId(defaultFolderId);
  }, [defaultFolderId]);

  const isUploading = uploadingCount > 0;

  function resetCounters(): void {
    setTotalCount(0);
    setDoneCount(0);
    setUploadFailedCount(0);
    setFailedNames([]);
  }

  /**
   * 两步链路：先把文件传到 /files/upload 拿 UploadedFile，
   * 再 POST /assets 落成素材记录。只做第一步的话素材库里什么都不会出现。
   */
  async function uploadOne(file: File): Promise<void> {
    setUploadingCount((count) => count + 1);
    try {
      const uploaded = await uploadAssetFile(file);
      const trimmedPath = folderPath.trim();
      await createMediaAsset({
        asset_type: uploaded.asset_type,
        url: uploaded.download_url,
        local_path: uploaded.file_name,
        original_name: file.name,
        mime_type: file.type,
        file_size: uploaded.size,
        source: "upload",
        // folder_id 与 folder_path 同时给时后端以 folder_id 为准，所以按模式二选一。
        ...(targetMode === "folder"
          ? { folder_id: folderId }
          : trimmedPath
            ? { folder_path: trimmedPath }
            : {}),
        product_id: productId,
        scene_id: sceneId,
        tag_ids: tagIds,
      });
      setDoneCount((count) => count + 1);
    } catch {
      setUploadFailedCount((count) => count + 1);
      setFailedNames((names) => (names.includes(file.name) ? names : [...names, file.name]));
    } finally {
      setUploadingCount((count) => {
        const next = Math.max(0, count - 1);
        if (next === 0) void onUploaded();
        return next;
      });
    }
  }

  const progressPercent = totalCount === 0 ? 0 : Math.round(((doneCount + uploadFailedCount) / totalCount) * 100);

  return (
    <Card
      size="small"
      title={
        <Space>
          <UploadOutlined /> 批量上传
        </Space>
      }
      styles={{ body: { padding: 12 } }}
    >
      <Form layout="vertical" size="small">
        <Form.Item label="上传目标" style={{ marginBottom: 12 }}>
          <Segmented<TargetMode>
            value={targetMode}
            onChange={setTargetMode}
            options={[
              { label: "选择文件夹", value: "folder" },
              { label: "填写路径", value: "path" },
            ]}
            block
          />
        </Form.Item>

        {targetMode === "folder" ? (
          <Form.Item label="目标文件夹" help="留空表示不归入任何文件夹。" style={{ marginBottom: 12 }}>
            <TreeSelect<number, FolderSelectOption>
              allowClear
              style={{ width: "100%" }}
              value={folderId ?? undefined}
              onChange={(value) => setFolderId(typeof value === "number" ? value : null)}
              treeData={folderOptions}
              fieldNames={{ label: "title", value: "value", children: "children" }}
              treeDefaultExpandAll
              placeholder="不归入文件夹"
            />
          </Form.Item>
        ) : (
          <Form.Item
            label="文件夹路径"
            help="用「/」分层，缺失的层级会自动建出来。例如：坐浴盆/卫生间场景"
            style={{ marginBottom: 12 }}
          >
            <Input
              value={folderPath}
              onChange={(event) => setFolderPath(event.target.value)}
              placeholder="坐浴盆/卫生间场景"
            />
          </Form.Item>
        )}

        <Row gutter={8}>
          <Col span={12}>
            <Form.Item label="产品" style={{ marginBottom: 12 }}>
              <Select<number>
                allowClear
                value={productId ?? undefined}
                onChange={(value) => setProductId(value ?? null)}
                placeholder="不指定"
                options={products.map((item) => ({ value: item.id, label: item.name }))}
              />
            </Form.Item>
          </Col>
          <Col span={12}>
            <Form.Item label="场景" style={{ marginBottom: 12 }}>
              <Select<number>
                allowClear
                value={sceneId ?? undefined}
                onChange={(value) => setSceneId(value ?? null)}
                placeholder="不指定"
                options={scenes.map((item) => ({ value: item.id, label: item.name }))}
              />
            </Form.Item>
          </Col>
        </Row>

        <Form.Item label="标签" style={{ marginBottom: 12 }}>
          <Select<number[]>
            mode="multiple"
            allowClear
            value={tagIds}
            onChange={(value) => setTagIds(value ?? [])}
            placeholder="不指定"
            options={tags.map((item) => ({ value: item.id, label: item.name }))}
          />
        </Form.Item>
      </Form>

      <Upload.Dragger
        accept="image/*,video/*"
        multiple
        showUploadList={false}
        disabled={isUploading}
        beforeUpload={(file, fileList) => {
          // fileList 的第一项到达时代表新一批开始，重置本批计数。
          if (fileList[0]?.uid === file.uid) {
            resetCounters();
            setTotalCount(fileList.length);
          }
          void uploadOne(file);
          return false; // 阻止 antd 自动上传，走我们自己的两步链路
        }}
        style={{ padding: "12px 8px" }}
      >
        <p style={{ margin: 0 }}>
          <InboxOutlined style={{ fontSize: 24, color: "#1668dc" }} />
        </p>
        <p style={{ margin: "8px 0 0" }}>点击或拖拽文件到此处，支持多选</p>
        <Text type="secondary" style={{ fontSize: 11 }}>
          上传后自动落成素材记录，带上以上产品 / 场景 / 标签
        </Text>
      </Upload.Dragger>

      {totalCount > 0 && (
        <div style={{ marginTop: 12 }}>
          <Progress
            percent={progressPercent}
            status={isUploading ? "active" : uploadFailedCount > 0 ? "exception" : "success"}
            size="small"
          />
          <Text type="secondary" style={{ fontSize: 12 }}>
            共 {totalCount} 个，成功 {doneCount} 个
            {uploadFailedCount > 0 ? `，失败 ${uploadFailedCount} 个` : ""}
            {isUploading ? `，正在上传 ${uploadingCount} 个` : ""}
          </Text>
          {!isUploading && uploadFailedCount > 0 && (
            <div style={{ marginTop: 4 }}>
              <Text type="danger" style={{ fontSize: 12 }}>
                失败：{failedNames.join("、")}
              </Text>
              <Button
                type="link"
                size="small"
                onClick={() => {
                  resetCounters();
                  antMessage.info("已清空失败记录，可重新选择失败的文件。");
                }}
              >
                清空
              </Button>
            </div>
          )}
        </div>
      )}
    </Card>
  );
}
