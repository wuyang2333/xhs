import {
  DeleteOutlined,
  FileImageOutlined,
  FolderOutlined,
  LinkOutlined,
  PictureOutlined,
  PlusOutlined,
  ReloadOutlined,
  RobotOutlined,
  StarOutlined,
  UploadOutlined,
} from "@ant-design/icons";
import {
  Alert,
  Button,
  Card,
  Checkbox,
  Col,
  Empty,
  Image,
  Input,
  Modal,
  Pagination,
  Row,
  Space,
  Spin,
  Tabs,
  Tag,
  Typography,
  Upload,
} from "antd";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import { AssetLibraryPanel } from "../../../components/assets/asset-library-panel";
import { PageHeader } from "../../../components/layout/app-shell";
import { EMPTY_PROMPT_PICK, PromptPicker } from "../../../components/prompts/prompt-picker";
import type { PromptPickerValue } from "../../../components/prompts/prompt-picker";
import { usePromptComposer } from "../../../components/prompts/use-prompt-composer";
import {
  deleteGeneratedImageAsset,
  deleteUserImage,
  describeImageWithAi,
  fetchGeneratedImageAssets,
  fetchUserImages,
  generateImageWithAi,
  uploadAssetFile,
} from "../../../lib/api";
import { formatShanghaiTime } from "../../../lib/time";
import type { GeneratedImageAsset, UserImageFile } from "../../../types";

const { Text, Paragraph } = Typography;
const { TextArea } = Input;

function isRenderableImage(value: string): boolean {
  return (
    value.startsWith("http://") ||
    value.startsWith("https://") ||
    value.startsWith("data:image/") ||
    value.startsWith("/api/")
  );
}

export function XhsImageStudioPage() {
  const [assets, setAssets] = useState<GeneratedImageAsset[]>([]);
  const [userImages, setUserImages] = useState<UserImageFile[]>([]);
  const [prompt, setPrompt] = useState("");
  const [referenceImages, setReferenceImages] = useState<string[]>([]);
  const [imageUrl, setImageUrl] = useState("");
  const [description, setDescription] = useState("");
  const [isLoading, setIsLoading] = useState(true);
  const [isGenerating, setIsGenerating] = useState(false);
  const [isDescribing, setIsDescribing] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refPickerOpen, setRefPickerOpen] = useState(false);
  const [saveToAssets, setSaveToAssets] = useState(true);
  const [generatedPreview, setGeneratedPreview] = useState<string | null>(null);
  const [uploadingCount, setUploadingCount] = useState(0);
  const [uploadFailedCount, setUploadFailedCount] = useState(0);

  // For the reference picker modal: which callback mode
  const [pickerMode, setPickerMode] = useState<"reference" | "describe">(
    "reference",
  );
  const [pickerUrlInput, setPickerUrlInput] = useState("");
  const composer = usePromptComposer();
  const navigate = useNavigate();
  const [imagePrompt, setImagePrompt] = useState<PromptPickerValue>(EMPTY_PROMPT_PICK);
  const [describePrompt, setDescribePrompt] = useState<PromptPickerValue>(EMPTY_PROMPT_PICK);
  const [aiPage, setAiPage] = useState(1);
  const [aiPageSize, setAiPageSize] = useState(20);
  const [aiTotal, setAiTotal] = useState(0);

  async function loadAssets(targetPage = aiPage, targetSize = aiPageSize) {
    setIsLoading(true);
    setError(null);
    try {
      const [aiResult, userResult] = await Promise.all([
        // 此前没传分页参数，后端默认只给 20 条且界面无翻页控件，
        // 生成量一多就再也看不到早期的图。
        fetchGeneratedImageAssets({ page: targetPage, page_size: targetSize }),
        fetchUserImages(),
      ]);
      setAssets(aiResult.items);
      setAiTotal(aiResult.total);
      setAiPage(aiResult.page);
      setAiPageSize(aiResult.page_size);
      setUserImages(userResult.items);
    } catch {
      setError("图片资产加载失败。");
    } finally {
      setIsLoading(false);
    }
  }

  async function handleGenerate() {
    // 选了指令就用指令，两者都空才拦。此前只看输入框，导致「想从库里选」的人
    // 必须先手打一段才点得动生成。
    const finalPrompt = imagePrompt.promptId !== null
      ? [imagePrompt.content, prompt.trim()].filter(Boolean).join("\n")
      : prompt.trim();
    if (!finalPrompt) {
      setError("请选择一条生图指令，或填写提示词。");
      return;
    }
    setIsGenerating(true);
    setError(null);
    setMessage(null);
    setGeneratedPreview(null);
    try {
      const result = await generateImageWithAi({
        prompt: finalPrompt,
        reference_images:
          referenceImages.length > 0 ? referenceImages : undefined,
        save_to_assets: saveToAssets,
      });
      if (imagePrompt.promptId !== null && imagePrompt.version !== null) {
        await composer.recordUsages(
          [{ promptId: imagePrompt.promptId, version: imagePrompt.version }],
          { module: "image_studio", promptType: "image", targetType: "image" },
        );
      }
      setGeneratedPreview(result.url);
      if (result.asset) {
        setAssets((prev) => [result.asset!, ...prev]);
      }
      setMessage("图片生成成功。");
    } catch {
      setError("AI 图片生成失败，请确认已配置图片生成模型。");
    } finally {
      setIsGenerating(false);
    }
  }

  async function handleDescribeImage() {
    if (!imageUrl.trim()) {
      setError("请先填写图片 URL。");
      return;
    }
    setIsDescribing(true);
    setError(null);
    setMessage(null);
    try {
      const result = await describeImageWithAi({
        image_url: imageUrl.trim(),
        // 未选指令时留空，由后端用默认人设。
        system_prompt_override: describePrompt.content || undefined,
      });
      if (describePrompt.promptId !== null && describePrompt.version !== null) {
        await composer.recordUsages(
          [{ promptId: describePrompt.promptId, version: describePrompt.version }],
          { module: "image_studio", promptType: "image", targetType: "image_describe" },
        );
      }
      setDescription(result.text);
      setMessage("图片描述已生成。");
    } catch {
      setError("图片描述失败，请确认已配置支持视觉理解的图片模型。");
    } finally {
      setIsDescribing(false);
    }
  }

  function openRefPicker(mode: "reference" | "describe") {
    setPickerMode(mode);
    setPickerUrlInput("");
    setRefPickerOpen(true);
  }

  function handlePickerSelect(url: string) {
    if (pickerMode === "reference") {
      setReferenceImages((prev) =>
        prev.includes(url) ? prev : [...prev, url],
      );
    } else {
      setImageUrl(url);
    }
    setRefPickerOpen(false);
  }

  function handlePickerUrlAdd() {
    const trimmed = pickerUrlInput.trim();
    if (!trimmed) return;
    handlePickerSelect(trimmed);
  }

  async function handleUploadFile(file: File) {
    setUploadingCount((count) => count + 1);
    try {
      const uploaded = await uploadAssetFile(file);
      const newItem: UserImageFile = {
        file_name: uploaded.file_name,
        url: uploaded.download_url,
        size: uploaded.size,
      };
      setUserImages((prev) => [newItem, ...prev]);
    } catch {
      setUploadFailedCount((count) => count + 1);
      setError(`批量上传中有文件失败：${file.name}`);
    } finally {
      setUploadingCount((count) => Math.max(0, count - 1));
    }
    return false; // prevent antd auto-upload
  }

  useEffect(() => {
    void loadAssets();
  }, []);

  // AI 生图与图片描述两张工具卡。主流程已改为素材库上传，这里降级为辅助功能保留。
  const aiToolsRow = (
    <Row gutter={[16, 16]} style={{ marginBottom: 24 }}>
        {/* Left Card: AI Image Generation */}
        <Col xs={24} md={14}>
          <Card
            title={
              <Space>
                <StarOutlined /> AI 图片生成
              </Space>
            }
            extra={
              <Text type="secondary" style={{ fontSize: 11 }}>
                需配置图片生成模型（如 gpt-image-2、豆包 Seedream）
              </Text>
            }
          >
            {/* 指令选择器直接放在表单里。此前做成弹窗，用户两次反馈「没看到」。 */}
            <PromptPicker
              promptType="image"
              module="image_studio"
              value={imagePrompt}
              onChange={setImagePrompt}
              disabled={isGenerating}
            />
            <Space size={8} style={{ marginBottom: 4 }} wrap>
              <Text type="secondary" style={{ fontSize: 12 }}>
                {imagePrompt.promptId !== null
                  ? "已选指令，下方内容作为本次补充说明（可留空）"
                  : "未选指令，将按下方内容生成"}
              </Text>
              <Button
                size="small"
                type="link"
                style={{ padding: 0, height: "auto", fontSize: 12 }}
                onClick={() => navigate("/platforms/xhs/prompts")}
              >
                管理指令库
              </Button>
            </Space>
            <TextArea
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              placeholder="充满活力的特写编辑肖像，模特眼神犀利，头戴雕塑感帽子，色彩拼接丰富，具有 Vogue 杂志封面的美学风格..."
              rows={4}
              disabled={isGenerating}
              style={{ marginBottom: 12 }}
            />

            {/* Reference images */}
            <div style={{ marginBottom: 12 }}>
              <Text
                type="secondary"
                style={{ fontSize: 12, marginBottom: 6, display: "block" }}
              >
                参考图
              </Text>
              <Space size={8} wrap>
                {referenceImages.map((url, idx) => (
                  <div
                    key={idx}
                    style={{
                      position: "relative",
                      width: 60,
                      height: 60,
                      borderRadius: 4,
                      overflow: "hidden",
                      border: "1px solid #333",
                    }}
                  >
                    {isRenderableImage(url) ? (
                      <img
                        src={url}
                        alt={`ref-${idx}`}
                        style={{
                          width: 60,
                          height: 60,
                          objectFit: "cover",
                        }}
                      />
                    ) : (
                      <div
                        style={{
                          width: 60,
                          height: 60,
                          display: "flex",
                          alignItems: "center",
                          justifyContent: "center",
                          background: "#1a1a1a",
                        }}
                      >
                        <PictureOutlined style={{ fontSize: 20, color: "#666" }} />
                      </div>
                    )}
                    <Button
                      type="text"
                      size="small"
                      danger
                      icon={<DeleteOutlined />}
                      onClick={() =>
                        setReferenceImages((prev) =>
                          prev.filter((_, i) => i !== idx),
                        )
                      }
                      style={{
                        position: "absolute",
                        top: 0,
                        right: 0,
                        width: 18,
                        height: 18,
                        padding: 0,
                        minWidth: 18,
                        borderRadius: "0 4px 0 4px",
                        background: "rgba(0,0,0,0.6)",
                      }}
                    />
                  </div>
                ))}
                {/* Add placeholder */}
                <div
                  onClick={() => openRefPicker("reference")}
                  style={{
                    width: 60,
                    height: 60,
                    borderRadius: 4,
                    border: "1px dashed #444",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    cursor: "pointer",
                    background: "#1a1a1a",
                  }}
                >
                  <PlusOutlined style={{ fontSize: 20, color: "#666" }} />
                </div>
              </Space>
            </div>

            {/* Controls row */}
            <Row
              justify="space-between"
              align="middle"
              style={{ marginBottom: 12 }}
            >
              <Col>
                <Checkbox
                  checked={saveToAssets}
                  onChange={(e) => setSaveToAssets(e.target.checked)}
                >
                  保存到 AI 图片资产
                </Checkbox>
              </Col>
              <Col>
                <Space>
                  <Button
                    onClick={() => { setPrompt(""); setReferenceImages([]); setGeneratedPreview(null); setSaveToAssets(true); }}
                    disabled={isGenerating}
                  >
                    重置
                  </Button>
                  <Button
                    type="primary"
                    icon={<RobotOutlined />}
                    onClick={handleGenerate}
                    loading={isGenerating}
                  >
                    生成
                  </Button>
                </Space>
              </Col>
            </Row>

            {/* Generated result */}
            {generatedPreview && (
              <div style={{ marginTop: 8 }}>
                <Text
                  type="secondary"
                  style={{ fontSize: 12, marginBottom: 6, display: "block" }}
                >
                  生成结果
                </Text>
                <div
                  style={{
                    background: "#1a1a1a",
                    borderRadius: 6,
                    padding: 8,
                    textAlign: "center",
                  }}
                >
                  <Image
                    src={generatedPreview}
                    alt="generated"
                    style={{ maxHeight: 240, objectFit: "contain" }}
                  />
                  {!saveToAssets && (
                    <Button
                      size="small"
                      type="link"
                      onClick={() => {
                        // Re-generate with save flag
                        setSaveToAssets(true);
                        setMessage("下次生成将自动保存到 AI 资产。");
                      }}
                      style={{ marginTop: 8 }}
                    >
                      保存到 AI 资产
                    </Button>
                  )}
                </div>
              </div>
            )}
          </Card>
        </Col>

        {/* Right Card: Image Description */}
        <Col xs={24} md={10}>
          <Card
            title={
              <Space>
                <FileImageOutlined /> 图片描述
              </Space>
            }
            extra={
              <Text type="secondary" style={{ fontSize: 11 }}>
                需配置多模态模型（如 GPT-4o）
              </Text>
            }
          >
            <Space.Compact style={{ width: "100%", marginBottom: 12 }}>
              <Input
                value={imageUrl}
                onChange={(e) => setImageUrl(e.target.value)}
                placeholder="图片 URL"
                disabled={isGenerating}
              />
              <Button
                icon={<PictureOutlined />}
                onClick={() => openRefPicker("describe")}
              >
                从资产选择
              </Button>
            </Space.Compact>
            <PromptPicker
              promptType="image"
              module="image_studio"
              label="分析指令（可选，不选则用默认）"
              value={describePrompt}
              onChange={setDescribePrompt}
              disabled={isDescribing}
            />
            <Button
              onClick={handleDescribeImage}
              loading={isDescribing}
              block
              style={{ marginBottom: 12 }}
            >
              生成描述
            </Button>
            {description && (
              <Paragraph
                style={{
                  background: "#262626",
                  padding: 12,
                  borderRadius: 6,
                  fontSize: 13,
                  margin: 0,
                }}
              >
                {description}
              </Paragraph>
            )}
          </Card>
        </Col>
    </Row>
  );

  return (
    <div>
      <PageHeader
        eyebrow="XHS Image Studio"
        title="图片工坊"
        description="上传与分类管理素材，按文件夹 / 产品 / 场景 / 标签组织；AI 生图作为辅助功能保留。"
        action={
          <Button
            icon={<ReloadOutlined />}
            onClick={() => { void loadAssets(); }}
            loading={isLoading}
          >
            刷新资产
          </Button>
        }
      />

      {error && (
        <Alert
          type="error"
          message={error}
          showIcon
          closable
          onClose={() => setError(null)}
          style={{ marginBottom: 16 }}
        />
      )}
      {message && (
        <Alert
          type="success"
          message={message}
          showIcon
          closable
          onClose={() => setMessage(null)}
          style={{ marginBottom: 16 }}
        />
      )}

      <Tabs
        defaultActiveKey="asset_library"
        items={[
          {
            key: "asset_library",
            label: (
              <Space>
                <FolderOutlined /> 素材库
              </Space>
            ),
            children: <AssetLibraryPanel />,
          },
          {
            key: "ai_generate",
            label: (
              <Space>
                <RobotOutlined /> AI 生图 / 图片描述
              </Space>
            ),
            children: aiToolsRow,
          },
          {
            key: "ai_assets",
            label: (
              <Space>
                <StarOutlined /> AI 图片资产
              </Space>
            ),
            children: (
              <>
                {isLoading ? (
                  <div style={{ textAlign: "center", padding: 48 }}>
                    <Spin tip="正在加载 AI 图片资产..." />
                  </div>
                ) : assets.length === 0 ? (
                  <Empty
                    image={Empty.PRESENTED_IMAGE_SIMPLE}
                    description="暂无 AI 图片资产。"
                    style={{ padding: 32 }}
                  />
                ) : (
                  <Row gutter={[12, 12]}>
                    {assets.map((asset) => (
                      <Col xs={12} sm={8} md={6} key={asset.id}>
                        <Card
                          size="small"
                          hoverable
                          styles={{ body: { padding: 8 } }}
                        >
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
                            }}
                          >
                            {isRenderableImage(asset.file_path) ? (
                              <Image
                                alt={asset.prompt}
                                src={asset.file_path}
                                style={{
                                  maxHeight: 120,
                                  objectFit: "contain",
                                }}
                              />
                            ) : (
                              <PictureOutlined
                                style={{ fontSize: 28, color: "#555" }}
                              />
                            )}
                          </div>
                          <Text
                            strong
                            ellipsis
                            style={{ fontSize: 12, display: "block" }}
                          >
                            {asset.prompt}
                          </Text>
                          <div style={{ marginTop: 4 }}>
                            <Tag
                              style={{
                                fontSize: 10,
                                padding: "0 4px",
                                margin: 0,
                              }}
                            >
                              {asset.model_name || "image model"}
                            </Tag>
                            <Text
                              type="secondary"
                              style={{ fontSize: 10, marginLeft: 4 }}
                            >
                              {formatShanghaiTime(asset.created_at)}
                            </Text>
                          </div>
                          <Button
                            type="text" danger size="small" icon={<DeleteOutlined />}
                            onClick={async () => {
                              try {
                                await deleteGeneratedImageAsset(asset.id);
                                setAssets((prev) => prev.filter((a) => a.id !== asset.id));
                              } catch { /* global interceptor shows error */ }
                            }}
                            style={{ width: "100%", marginTop: 4 }}
                          >
                            删除
                          </Button>
                        </Card>
                      </Col>
                    ))}
                  </Row>
                )}
                {aiTotal > 0 ? (
                  <Pagination
                    style={{ marginTop: 16, textAlign: "right" }}
                    current={aiPage}
                    pageSize={aiPageSize}
                    total={aiTotal}
                    showSizeChanger
                    pageSizeOptions={[20, 40, 80, 100]}
                    showTotal={(total, range) => `第 ${range[0]}-${range[1]} 张，共 ${total} 张`}
                    onChange={(page, size) => { void loadAssets(page, size); }}
                  />
                ) : null}
              </>
            ),
          },
          {
            key: "user_images",
            label: (
              <Space>
                <PictureOutlined /> 普通图片资产
              </Space>
            ),
            children: (
              <>
                <div style={{ marginBottom: 16 }}>
                  <Upload
                    accept="image/*"
                    multiple
                    showUploadList={false}
                    beforeUpload={(file, fileList) => {
                      if (fileList[0]?.uid === file.uid) setUploadFailedCount(0);
                      void handleUploadFile(file);
                      return false;
                    }}
                  >
                    <Button icon={<UploadOutlined />} loading={uploadingCount > 0}>
                      {uploadingCount > 0 ? `正在上传 ${uploadingCount} 张` : "批量上传图片"}
                    </Button>
                  </Upload>
                  {uploadingCount === 0 && uploadFailedCount > 0 && (
                    <Text type="danger" style={{ marginLeft: 12 }}>
                      本次有 {uploadFailedCount} 张上传失败，可重新选择失败图片。
                    </Text>
                  )}
                </div>
                {userImages.length === 0 ? (
                  <Empty
                    image={Empty.PRESENTED_IMAGE_SIMPLE}
                    description="暂无普通图片资产。上传图片后将显示在这里。"
                    style={{ padding: 32 }}
                  />
                ) : (
                  <Row gutter={[12, 12]}>
                    {userImages.map((img) => (
                      <Col xs={12} sm={8} md={6} key={img.file_name}>
                        <Card
                          size="small"
                          hoverable
                          styles={{ body: { padding: 8 } }}
                        >
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
                            }}
                          >
                            <Image
                              alt={img.file_name}
                              src={img.url}
                              style={{
                                maxHeight: 120,
                                objectFit: "contain",
                              }}
                            />
                          </div>
                          <Text
                            strong
                            ellipsis
                            style={{ fontSize: 12, display: "block" }}
                          >
                            {img.file_name}
                          </Text>
                          <Text type="secondary" style={{ fontSize: 10 }}>
                            {(img.size / 1024).toFixed(1)} KB
                          </Text>
                          <Button
                            type="text" danger size="small" icon={<DeleteOutlined />}
                            onClick={async () => {
                              try {
                                await deleteUserImage(img.file_name);
                                setUserImages((prev) => prev.filter((i) => i.file_name !== img.file_name));
                              } catch { /* global interceptor shows error */ }
                            }}
                            style={{ width: "100%", marginTop: 4 }}
                          >
                            删除
                          </Button>
                        </Card>
                      </Col>
                    ))}
                  </Row>
                )}
              </>
            ),
          },
        ]}
      />

      {/* ---- Reference Image Picker Modal ---- */}
      <Modal
        title="选择图片"
        open={refPickerOpen}
        onCancel={() => setRefPickerOpen(false)}
        footer={null}
        width={640}
        destroyOnClose
      >
        <Tabs
          defaultActiveKey="user_images"
          items={[
            {
              key: "user_images",
              label: (
                <Space>
                  <PictureOutlined /> 普通图片资产
                </Space>
              ),
              children:
                userImages.length === 0 ? (
                  <Empty
                    image={Empty.PRESENTED_IMAGE_SIMPLE}
                    description="暂无普通图片资产。"
                    style={{ padding: 24 }}
                  />
                ) : (
                  <Row gutter={[8, 8]}>
                    {userImages.map((img) => (
                      <Col span={6} key={img.file_name}>
                        <div
                          onClick={() => handlePickerSelect(img.url)}
                          style={{
                            cursor: "pointer",
                            borderRadius: 4,
                            overflow: "hidden",
                            border: "1px solid #333",
                            height: 80,
                            display: "flex",
                            alignItems: "center",
                            justifyContent: "center",
                            background: "#1a1a1a",
                          }}
                        >
                          <img
                            src={img.url}
                            alt={img.file_name}
                            style={{
                              maxHeight: 80,
                              maxWidth: "100%",
                              objectFit: "contain",
                            }}
                          />
                        </div>
                      </Col>
                    ))}
                  </Row>
                ),
            },
            {
              key: "ai_assets",
              label: (
                <Space>
                  <StarOutlined /> AI 图片资产
                </Space>
              ),
              children:
                assets.length === 0 ? (
                  <Empty
                    image={Empty.PRESENTED_IMAGE_SIMPLE}
                    description="暂无 AI 图片资产。"
                    style={{ padding: 24 }}
                  />
                ) : (
                  <Row gutter={[8, 8]}>
                    {assets.map((asset) => (
                      <Col span={6} key={asset.id}>
                        <div
                          onClick={() => handlePickerSelect(asset.file_path)}
                          style={{
                            cursor: "pointer",
                            borderRadius: 4,
                            overflow: "hidden",
                            border: "1px solid #333",
                            height: 80,
                            display: "flex",
                            alignItems: "center",
                            justifyContent: "center",
                            background: "#1a1a1a",
                          }}
                        >
                          {isRenderableImage(asset.file_path) ? (
                            <img
                              src={asset.file_path}
                              alt={asset.prompt}
                              style={{
                                maxHeight: 80,
                                maxWidth: "100%",
                                objectFit: "contain",
                              }}
                            />
                          ) : (
                            <PictureOutlined
                              style={{ fontSize: 24, color: "#555" }}
                            />
                          )}
                        </div>
                      </Col>
                    ))}
                  </Row>
                ),
            },
            {
              key: "url",
              label: (
                <Space>
                  <LinkOutlined /> URL
                </Space>
              ),
              children: (
                <Space.Compact style={{ width: "100%" }}>
                  <Input
                    value={pickerUrlInput}
                    onChange={(e) => setPickerUrlInput(e.target.value)}
                    placeholder="输入图片 URL"
                    onPressEnter={handlePickerUrlAdd}
                  />
                  <Button type="primary" onClick={handlePickerUrlAdd}>
                    添加
                  </Button>
                </Space.Compact>
              ),
            },
          ]}
        />
      </Modal>
      {composer.node}
    </div>
  );
}
