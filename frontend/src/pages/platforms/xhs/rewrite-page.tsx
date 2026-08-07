import {
  DeleteOutlined,
  EditOutlined,
  ExperimentOutlined,
  EyeOutlined,
  FileImageOutlined,
  FileTextOutlined,
  HighlightOutlined,
  InboxOutlined,
  LinkOutlined,
  PictureOutlined,
  PlayCircleOutlined,
  PlusCircleOutlined,
  PlusOutlined,
  ReloadOutlined,
  RobotOutlined,
  SaveOutlined,
  SendOutlined,
  TagsOutlined,
  VideoCameraOutlined,
} from "@ant-design/icons";
import { DndContext, closestCenter, PointerSensor, useSensor, useSensors } from "@dnd-kit/core";
import type { DragEndEvent } from "@dnd-kit/core";
import { SortableContext, useSortable, horizontalListSortingStrategy, arrayMove } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import {
  Alert,
  Badge,
  Button,
  Card,
  Checkbox,
  Col,
  Collapse,
  Divider,
  Empty,
  Form,
  Image,
  Input,
  List,
  message as antMessage,
  Modal,
  Popconfirm,
  Row,
  Segmented,
  Select,
  Space,
  Spin,
  Tabs,
  Tag,
  Tooltip,
  Typography,
  Upload,
} from "antd";
import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";

import { PageHeader } from "../../../components/layout/app-shell";
import {
  DraftAssetOrderPanel,
  OrderSaveIndicator,
} from "../../../components/drafts/draft-asset-order-panel";
import type { OrderSaveStatus } from "../../../components/drafts/draft-asset-order-panel";
import { AssetLibraryPicker } from "../../../components/assets/asset-library-picker";
import { DraftAssetPicker } from "../../../components/drafts/draft-asset-picker";
import { EMPTY_PROMPT_PICK, PromptPicker } from "../../../components/prompts/prompt-picker";
import type { PromptPickerValue } from "../../../components/prompts/prompt-picker";
import { usePromptComposer } from "../../../components/prompts/use-prompt-composer";
import {
  addDraftAsset,
  deleteDraft,
  deleteDraftAsset,
  fetchDraftAssets,
  fetchDrafts,
  fetchAccounts,
  fetchGeneratedImageAssets,
  fetchSavedNote,
  fetchUserImages,
  generateImageWithAi,
  generateNoteWithAi,
  generateTagOptions,
  generateTitleOptions,
  reorderDraftAssets,
  rewriteDraftWithAi,
  sendDraftToPublish,
  setDraftAssetCover,
  updateDraft,
  updateDraftAsset,
  uploadAssetFile,
} from "../../../lib/api";
import { formatShanghaiTime } from "../../../lib/time";
import type { Draft, DraftAsset, GeneratedImageAsset, MediaAsset, PlatformAccount, SavedNote, UserImageFile } from "../../../types";

const { Text, Paragraph } = Typography;
const { TextArea } = Input;

type AiContentMode = "rewrite" | "generate";

function getNoteUrl(note: SavedNote): string {
  const raw = note.raw_json ?? {};
  for (const key of ["note_url", "url", "share_url"]) {
    const v = raw[key];
    if (typeof v === "string" && v.startsWith("http")) return v;
  }
  const data = (raw.data && typeof raw.data === "object") ? raw.data as Record<string, unknown> : {};
  const items = Array.isArray(data.items) ? data.items : [];
  const item = (items[0] && typeof items[0] === "object") ? items[0] as Record<string, unknown> : {};
  const card = (item.note_card && typeof item.note_card === "object") ? item.note_card as Record<string, unknown> : {};
  for (const obj of [card, item]) {
    const xsec = obj.xsec_token;
    if (typeof xsec === "string" && xsec) {
      const src = (typeof obj.xsec_source === "string" ? obj.xsec_source : "") || "pc_feed";
      return `https://www.xiaohongshu.com/explore/${note.note_id}?xsec_token=${xsec}&xsec_source=${src}`;
    }
    for (const k of ["note_url", "url", "share_url"]) {
      const v = obj[k];
      if (typeof v === "string" && v.startsWith("http")) return v;
    }
  }
  return `https://www.xiaohongshu.com/explore/${note.note_id}`;
}

function formatDraftTime(value: string): string {
  return formatShanghaiTime(value);
}

const segmentedOptions = [
  {
    value: "rewrite" as const,
    label: "改写已有草稿",
    icon: <EditOutlined />,
  },
  {
    value: "generate" as const,
    label: "生成新草稿",
    icon: <PlusCircleOutlined />,
  },
];

/* ── helpers ───────────────────────────────────────────────────────── */

function isValidUrl(s: string): boolean {
  return s.startsWith("http://") || s.startsWith("https://");
}

function getAssetTypeFromUrl(url: string): "image" | "video" {
  const lower = url.toLowerCase();
  if (/\.(mp4|mov|avi|mkv|flv|wmv)/.test(lower)) return "video";
  return "image";
}

/* ── sortable image thumbnail ─────────────────────────────────────── */

function SortableRewriteImage({ asset, onEdit, onRemove, onView }: { asset: DraftAsset; onEdit: () => void; onRemove: () => void; onView: () => void }) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({ id: asset.id });
  const style: React.CSSProperties = {
    position: "relative",
    display: "inline-block",
    padding: 2,
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.5 : 1,
    cursor: "grab",
  };
  return (
    <div ref={setNodeRef} style={style} {...attributes} {...listeners}>
      <img
        src={asset.url || asset.local_path}
        width={60}
        height={60}
        style={{ objectFit: "cover", borderRadius: 4, display: "block", pointerEvents: "none" }}
        referrerPolicy="no-referrer"
      />
      <Button
        type="text" size="small" icon={<EditOutlined />}
        onClick={(e) => { e.stopPropagation(); onEdit(); }}
        onPointerDown={(e) => e.stopPropagation()}
        style={{
          position: "absolute", top: -6, left: -6, fontSize: 10,
          width: 18, height: 18, minWidth: 18, padding: 0,
          background: "rgba(0,0,0,.6)", borderRadius: "50%", color: "#1668dc",
        }}
      />
      <Popconfirm title="移除此图片？" onConfirm={onRemove}>
        <Button
          type="text" danger size="small" icon={<DeleteOutlined />}
          onClick={(e) => e.stopPropagation()}
          onPointerDown={(e) => e.stopPropagation()}
          style={{
            position: "absolute", top: -6, right: -6, fontSize: 10,
            width: 18, height: 18, minWidth: 18, padding: 0,
            background: "rgba(0,0,0,.6)", borderRadius: "50%",
          }}
        />
      </Popconfirm>
      <Button
        type="text" size="small" icon={<EyeOutlined />}
        onClick={(e) => { e.stopPropagation(); onView(); }}
        onPointerDown={(e) => e.stopPropagation()}
        style={{
          position: "absolute", bottom: -6, left: -6, fontSize: 10,
          width: 18, height: 18, minWidth: 18, padding: 0,
          background: "rgba(0,0,0,.6)", borderRadius: "50%", color: "#fff",
        }}
      />
    </div>
  );
}

export function XhsDraftsPage() {
  const [activeMode, setActiveMode] = useState<AiContentMode>("rewrite");
  const [drafts, setDrafts] = useState<Draft[]>([]);
  const [accounts, setAccounts] = useState<PlatformAccount[]>([]);
  const [accountFilter, setAccountFilter] = useState<number | undefined>();
  const [selectedDraftId, setSelectedDraftId] = useState<number | null>(null);
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [instruction, setInstruction] = useState("保留事实，增强小红书种草感，语气自然。");
  const [topic, setTopic] = useState("");
  const [reference, setReference] = useState("");
  const [generateImages, setGenerateImages] = useState(true);
  const [imageCount, setImageCount] = useState(1);
  const [imagePrompt, setImagePrompt] = useState("");
  const [generateAssetPickerOpen, setGenerateAssetPickerOpen] = useState(false);
  const [selectedGenerateAssetUrls, setSelectedGenerateAssetUrls] = useState<string[]>([]);
  /** 生成表单里已挂载的历史图片，按 url / local_path 比对，避免重复挂同一张。 */
  const isGenerateAssetSelected = useCallback(
    (asset: MediaAsset): boolean =>
      (asset.url !== "" && selectedGenerateAssetUrls.includes(asset.url)) ||
      (asset.local_path !== "" && selectedGenerateAssetUrls.includes(asset.local_path)),
    [selectedGenerateAssetUrls],
  );
  const [titleOptions, setTitleOptions] = useState<string[]>([]);
  const [tagOptions, setTagOptions] = useState<string[]>([]);
  const [systemPrompt, setSystemPrompt] = useState("你是小红书内容创作助手，擅长写吸引人的标题和正文。");
  const [sourceNote, setSourceNote] = useState<SavedNote | null>(null);
  const [sourceAssets, setSourceAssets] = useState<DraftAsset[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isSaving, setIsSaving] = useState(false);
  const [isRewriting, setIsRewriting] = useState(false);
  const [isPolishingTitles, setIsPolishingTitles] = useState(false);
  const [isPolishingTags, setIsPolishingTags] = useState(false);
  const [isGenerating, setIsGenerating] = useState(false);
  const [isRegeneratingImage, setIsRegeneratingImage] = useState(false);
  const [isSendingPublish, setIsSendingPublish] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [rewritePreview, setRewritePreview] = useState<string | null>(null);
  const [optimizeModalOpen, setOptimizeModalOpen] = useState(false);
  const [optimizeAssetId, setOptimizeAssetId] = useState<number | null>(null);
  const [optimizePrompt, setOptimizePrompt] = useState("");
  const [optimizeRefImages, setOptimizeRefImages] = useState<string[]>([]);
  const [optimizeResult, setOptimizeResult] = useState<string | null>(null);
  const [isOptimizing, setIsOptimizing] = useState(false);
  const [previewImage, setPreviewImage] = useState<string | null>(null);
  const [uploadModalOpen, setUploadModalOpen] = useState(false);
  const [assetUrl, setAssetUrl] = useState("");
  const [draftTags, setDraftTags] = useState<{id: string; name: string}[]>([]);
  const [newTagInput, setNewTagInput] = useState("");
  const [isAddingTag, setIsAddingTag] = useState(false);
  const [userImages, setUserImages] = useState<UserImageFile[]>([]);
  const [aiAssets, setAiAssets] = useState<GeneratedImageAsset[]>([]);
  const [selectedAssetUrls, setSelectedAssetUrls] = useState<string[]>([]);
  const [refPickerOpen, setRefPickerOpen] = useState(false);
  const [libraryPickerOpen, setLibraryPickerOpen] = useState(false);
  const [isAddingFromLibrary, setIsAddingFromLibrary] = useState(false);
  const [orderStatus, setOrderStatus] = useState<OrderSaveStatus>("idle");
  /** 在途的 reorder 请求数。落库未完成或失败时必须挡住「送发布」（总纲第七节）。 */
  const inFlightReorders = useRef(0);
  const [replaceAssetId, setReplaceAssetId] = useState<number | null>(null);
  const [replaceUrlInput, setReplaceUrlInput] = useState("");
  const [isReplacing, setIsReplacing] = useState(false);
  const [refPickerUrlInput, setRefPickerUrlInput] = useState("");
  const composer = usePromptComposer();
  const [notePromptPick, setNotePromptPick] = useState<PromptPickerValue>(EMPTY_PROMPT_PICK);
  const [rewritePromptPick, setRewritePromptPick] = useState<PromptPickerValue>(EMPTY_PROMPT_PICK);
  const [titlePromptPick, setTitlePromptPick] = useState<PromptPickerValue>(EMPTY_PROMPT_PICK);
  const [draftImagePromptPick, setDraftImagePromptPick] = useState<PromptPickerValue>(EMPTY_PROMPT_PICK);

  const selectedDraft = drafts.find((draft) => draft.id === selectedDraftId) ?? null;
  const imageAssets = sourceAssets.filter((a) => a.asset_type === "image");
  const hasImageAssets = imageAssets.length > 0;
  const hasVideoAssets = sourceAssets.some((a) => a.asset_type === "video");

  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 5 } }));

  /**
   * 把新的图片顺序落库。
   *
   * 后端 PUT /drafts/{id}/assets/reorder 要求提交该草稿的**完整**素材集合，
   * 少一个或多一个都返回 400（test_draft_asset_reorder_validates_exact_complete_set）。
   * 拖拽只发生在图片上，所以要把未参与拖拽的视频素材按原相对顺序接在后面一起提交。
   */
  function persistImageOrder(orderedImageIds: number[]) {
    const draftId = selectedDraft?.id;
    const nextAssets = (() => {
      const byId = new Map(sourceAssets.map((a) => [a.id, a]));
      const images = orderedImageIds
        .map((id) => byId.get(id))
        .filter((a): a is DraftAsset => a !== undefined);
      const others = sourceAssets.filter((a) => a.asset_type !== "image");
      return [...images, ...others];
    })();
    if (nextAssets.length !== sourceAssets.length) return;

    setSourceAssets(nextAssets.map((asset, index) => ({ ...asset, sort_order: index })));

    // 还没落库的草稿（含负 id 占位素材）不提交，否则集合不完整必然 400。
    if (!draftId || nextAssets.some((a) => a.id <= 0)) return;

    inFlightReorders.current += 1;
    setOrderStatus("saving");
    void reorderDraftAssets(draftId, nextAssets.map((a) => a.id))
      .then(() => {
        inFlightReorders.current -= 1;
        if (inFlightReorders.current === 0) setOrderStatus("saved");
      })
      .catch(() => {
        inFlightReorders.current -= 1;
        // 失败即视为顺序未落库，状态停在 error 并继续挡住送发布。
        setOrderStatus("error");
        antMessage.error("图片顺序保存失败，请重新拖动一次再送发布。");
      });
  }

  function handleDragEnd(event: DragEndEvent) {
    const { active, over } = event;
    if (!over || active.id === over.id) return;
    const images = sourceAssets.filter((a) => a.asset_type === "image");
    const ids = images.map((a) => a.id);
    const oldIdx = ids.indexOf(Number(active.id));
    const newIdx = ids.indexOf(Number(over.id));
    if (oldIdx < 0 || newIdx < 0) return;
    persistImageOrder(arrayMove(ids, oldIdx, newIdx));
  }

  function handleRemoveAsset(assetId: number) {
    const draftId = selectedDraft?.id;
    setSourceAssets((prev) => prev.filter((a) => a.id !== assetId));
    if (draftId && assetId > 0) {
      // 删除失败会让界面与库里的素材集合不一致，后续 reorder 必然 400。
      // 所以失败时把真实集合拉回来，而不是静默吞掉。
      void deleteDraftAsset(draftId, assetId).catch(() => {
        antMessage.error("移除图片失败，已重新载入草稿素材。");
        void fetchDraftAssets(draftId)
          .then((result) => setSourceAssets(result.items))
          .catch(() => {});
      });
    }
  }

  function handleFileUpload(file: File): false {
    const draftId = selectedDraft?.id;
    (async () => {
      try {
        const uploaded = await uploadAssetFile(file);
        if (draftId) {
          const saved = await addDraftAsset(draftId, {
            asset_type: uploaded.asset_type,
            url: uploaded.download_url,
            local_path: uploaded.file_name,
          });
          setSourceAssets((prev) => [...prev, saved]);
        } else {
          setSourceAssets((prev) => [...prev, {
            id: -(Date.now()),
            draft_id: 0,
            asset_type: uploaded.asset_type,
            url: uploaded.download_url,
            local_path: uploaded.file_name,
            media_asset_id: null,
            sort_order: prev.length,
            is_cover: false,
          }]);
        }
        antMessage.success("文件上传成功");
      } catch {
        antMessage.error("文件上传失败");
      }
    })();
    return false;
  }

  function handleUrlAdd() {
    const trimmed = assetUrl.trim();
    if (!trimmed) { antMessage.warning("请输入素材链接。"); return; }
    if (!isValidUrl(trimmed)) { antMessage.error("链接格式不正确，请输入 http:// 或 https:// 开头的链接。"); return; }
    const assetType = getAssetTypeFromUrl(trimmed);
    const draftId = selectedDraft?.id;
    if (draftId) {
      (async () => {
        try {
          const saved = await addDraftAsset(draftId, { asset_type: assetType, url: trimmed });
          setSourceAssets((prev) => [...prev, saved]);
          setAssetUrl("");
          setUploadModalOpen(false);
        } catch { antMessage.error("添加素材失败"); }
      })();
    } else {
      setSourceAssets((prev) => [...prev, {
        id: -(Date.now()),
        draft_id: 0,
        asset_type: assetType,
        url: trimmed,
        local_path: "",
        media_asset_id: null,
        sort_order: prev.length,
        is_cover: false,
      }]);
      setAssetUrl("");
      setUploadModalOpen(false);
    }
  }

  /** 从素材库批量加入草稿。串行提交，保证 sort_order 与用户勾选顺序一致。 */
  async function handleAddFromLibrary(assets: MediaAsset[]) {
    const draftId = selectedDraft?.id;
    if (!draftId) {
      antMessage.warning("请先选择或保存草稿，再从素材库选图。");
      return;
    }
    setIsAddingFromLibrary(true);
    let added = 0;
    let duplicated = 0;
    try {
      for (const asset of assets) {
        try {
          const saved = await addDraftAsset(draftId, { media_asset_id: asset.id });
          setSourceAssets((prev) => [...prev, saved]);
          added += 1;
        } catch (err: unknown) {
          const status = (err as { response?: { status?: number } })?.response?.status;
          if (status === 409) duplicated += 1;
          else throw err;
        }
      }
      if (added > 0) antMessage.success(`已加入 ${added} 张图片到草稿。`);
      if (duplicated > 0) antMessage.warning(`${duplicated} 张已在草稿中，已跳过。`);
      setLibraryPickerOpen(false);
    } catch {
      antMessage.error("加入草稿失败，请重试。");
    } finally {
      setIsAddingFromLibrary(false);
    }
  }

  /** 设封面。后端保证每草稿至多一个封面，本地状态同步清掉其他封面标记。 */
  async function handleSetCover(asset: DraftAsset) {
    const draftId = selectedDraft?.id;
    if (!draftId || asset.id <= 0) return;
    try {
      await setDraftAssetCover(draftId, asset.id);
      setSourceAssets((prev) => prev.map((item) => ({ ...item, is_cover: item.id === asset.id })));
      antMessage.success("已设为封面。");
    } catch {
      antMessage.error("设置封面失败。");
    }
  }

  async function handleConfirmReplace() {
    const draftId = selectedDraft?.id;
    const trimmed = replaceUrlInput.trim();
    if (!draftId || replaceAssetId === null) return;
    if (!isValidUrl(trimmed)) {
      antMessage.error("链接格式不正确，请输入 http:// 或 https:// 开头的链接。");
      return;
    }
    setIsReplacing(true);
    try {
      const saved = await updateDraftAsset(draftId, replaceAssetId, { url: trimmed, local_path: "" });
      setSourceAssets((prev) => prev.map((item) => (item.id === saved.id ? saved : item)));
      antMessage.success("已替换该图片。");
      setReplaceAssetId(null);
      setReplaceUrlInput("");
    } catch (err: unknown) {
      const status = (err as { response?: { status?: number } })?.response?.status;
      antMessage.error(status === 409 ? "该图片已在草稿中，不能重复。" : "替换失败，请重试。");
    } finally {
      setIsReplacing(false);
    }
  }

  function toggleAssetUrl(url: string) {
    setSelectedAssetUrls(prev => prev.includes(url) ? prev.filter(u => u !== url) : [...prev, url]);
  }

  function handleAddSelectedAssets() {
    const draftId = selectedDraft?.id;
    (async () => {
      for (const url of selectedAssetUrls) {
        if (draftId) {
          try {
            const saved = await addDraftAsset(draftId, { asset_type: "image", url });
            setSourceAssets(prev => [...prev, saved]);
          } catch { /* ignore individual failures */ }
        } else {
          setSourceAssets(prev => [...prev, {
            id: -(Date.now() + Math.random()),
            draft_id: 0,
            asset_type: "image",
            url,
            local_path: "",
            media_asset_id: null,
            sort_order: prev.length,
            is_cover: false,
          }]);
        }
      }
      setSelectedAssetUrls([]);
      setUploadModalOpen(false);
      antMessage.success(`已添加 ${selectedAssetUrls.length} 个图片`);
    })();
  }

  function clearStatus() {
    setMessage(null);
    setError(null);
  }

  function selectDraft(draft: Draft, mode: AiContentMode = "rewrite") {
    setActiveMode(mode);
    setSelectedDraftId(draft.id);
    setTitle(draft.title);
    setBody(draft.body);
    setDraftTags(
      Array.isArray(draft.tags)
        ? draft.tags.map((t) => ({ id: t.id || "", name: t.name || "" }))
        : [],
    );
    clearStatus();
  }

  async function loadDrafts() {
    setIsLoading(true);
    setError(null);
    try {
      const result = await fetchDrafts("xhs", accountFilter);
      setDrafts(result.items);
      const current = selectedDraftId
        ? result.items.find((draft) => draft.id === selectedDraftId)
        : result.items[0];
      if (current) {
        setSelectedDraftId(current.id);
        setTitle(current.title);
        setBody(current.body);
        setDraftTags(
          Array.isArray(current.tags)
            ? current.tags.map((t) => ({ id: t.id || "", name: t.name || "" }))
            : [],
        );
      } else {
        setSelectedDraftId(null);
        setTitle("");
        setBody("");
        setDraftTags([]);
      }
    } catch {
      setError("草稿列表加载失败。");
    } finally {
      setIsLoading(false);
    }
  }

  async function handleDeleteDraft(draftId: number) {
    try {
      await deleteDraft(draftId);
      setDrafts((prev) => prev.filter((d) => d.id !== draftId));
      if (selectedDraftId === draftId) {
        setSelectedDraftId(null);
        setTitle("");
        setBody("");
      }
      setMessage("草稿已删除。");
    } catch {
      setError("草稿删除失败。");
    }
  }

  function upsertDraft(draft: Draft, mode: AiContentMode = activeMode) {
    setDrafts((current) => {
      const exists = current.some((item) => item.id === draft.id);
      return exists ? current.map((item) => (item.id === draft.id ? draft : item)) : [draft, ...current];
    });
    selectDraft(draft, mode);
  }

  async function handleSave() {
    if (!selectedDraft) {
      setError("请先选择或生成一个草稿。");
      return;
    }
    setIsSaving(true);
    clearStatus();
    try {
      const updated = await updateDraft(selectedDraft.id, { title, body, tags: draftTags });
      upsertDraft(updated);
      setMessage(`草稿 #${updated.id} 已保存。`);
    } catch {
      setError("草稿保存失败。");
    } finally {
      setIsSaving(false);
    }
  }

  async function handleGenerateNote() {
    if (!topic.trim()) {
      setError("请先填写选题。");
      return;
    }
    setIsGenerating(true);
    clearStatus();
    try {
      const draft = await generateNoteWithAi({
        platform: "xhs",
        platform_account_id: accountFilter,
        topic: topic.trim(),
        reference,
        // 选了指令就整体按它执行；未选则留空，由后端用默认人设。
        system_prompt_override: notePromptPick.content || undefined,
        instruction,
        generate_images: generateImages,
        image_count: imageCount,
        image_prompt: imagePrompt.trim(),
        existing_image_urls: selectedGenerateAssetUrls,
      });
      if (notePromptPick.promptId !== null && notePromptPick.version !== null) {
        await composer.recordUsages(
          [{ promptId: notePromptPick.promptId, version: notePromptPick.version }],
          { module: "draft_studio", promptType: "note", accountId: accountFilter, targetType: "draft", targetId: draft.id },
        );
      }
      upsertDraft(draft, "generate");
      setMessage(
        generateImages
          ? `已一键生成草稿 #${draft.id} 和 ${imageCount} 张新配图，新图已保存到图片工坊${selectedGenerateAssetUrls.length > 0 ? `，另挂载 ${selectedGenerateAssetUrls.length} 张历史图片` : ""}。`
          : selectedGenerateAssetUrls.length > 0
            ? `已生成草稿 #${draft.id}，并挂载 ${selectedGenerateAssetUrls.length} 张历史图片。`
            : `已生成草稿 #${draft.id}，可继续编辑或送入发布中心。`,
      );
    } catch (err: unknown) {
      const detail = (err as { response?: { data?: { detail?: string } } })
        ?.response?.data?.detail;
      setError(detail || "AI 生成失败，请确认默认文本模型和图片模型均可用。");
    } finally {
      setIsGenerating(false);
    }
  }

  async function handleRegenerateImage() {
    if (!selectedDraft) {
      antMessage.warning("请先选择一个草稿");
      return;
    }
    const fallbackPrompt = imagePrompt.trim()
      || `为小红书图文笔记生成一张高质量竖版配图。标题：${title}。正文要点：${body.slice(0, 500)}。画面真实自然、主体清晰、适合小红书发布，不要水印。`;
    setIsRegeneratingImage(true);
    try {
      const result = await generateImageWithAi({
        // 选了配图指令就按它，未选则用兜底描述
        prompt: draftImagePromptPick.content
          ? [draftImagePromptPick.content, imagePrompt.trim()].filter(Boolean).join("\n")
          : fallbackPrompt,
        save_to_assets: true,
      });
      if (draftImagePromptPick.promptId !== null && draftImagePromptPick.version !== null) {
        await composer.recordUsages(
          [{ promptId: draftImagePromptPick.promptId, version: draftImagePromptPick.version }],
          { module: "draft_studio", promptType: "image", accountId: accountFilter, targetType: "draft_image", targetId: selectedDraft.id },
        );
      }
      const saved = await addDraftAsset(selectedDraft.id, {
        asset_type: "image",
        url: result.url,
      });
      setSourceAssets((prev) => [...prev, saved]);
      antMessage.success("已重新生成并追加一张图片，同时保存到图片工坊");
    } catch {
      antMessage.error("图片生成失败，请检查默认图片模型");
    } finally {
      setIsRegeneratingImage(false);
    }
  }

  async function handleRewrite() {
    if (!selectedDraft) {
      setError("请先选择一个草稿。");
      return;
    }
    setIsRewriting(true);
    clearStatus();
    setRewritePreview(null);
    try {
      const saved = await updateDraft(selectedDraft.id, { title, body });
      const rewritten = await rewriteDraftWithAi({
        draft_id: saved.id,
        system_prompt_override: rewritePromptPick.content || undefined,
        instruction,
      });
      if (rewritePromptPick.promptId !== null && rewritePromptPick.version !== null) {
        await composer.recordUsages(
          [{ promptId: rewritePromptPick.promptId, version: rewritePromptPick.version }],
          { module: "draft_studio", promptType: "rewrite", accountId: accountFilter, targetType: "draft", targetId: saved.id },
        );
      }
      setRewritePreview(rewritten.body);
      setMessage("AI 改写完成，请在下方对比后决定是否采用。");
    } catch (err: unknown) {
      const d = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      setError(d || "AI 改写失败，请确认已配置默认文本模型。");
    } finally {
      setIsRewriting(false);
    }
  }

  function applyRewrite() {
    if (rewritePreview !== null) {
      setBody(rewritePreview);
      setRewritePreview(null);
      setMessage("已采用 AI 改写内容。");
    }
  }

  function discardRewrite() {
    setRewritePreview(null);
    setMessage("已放弃 AI 改写内容。");
  }

  async function handleGenerateTitles() {
    if (!body.trim()) {
      setError("请先填写正文。");
      return;
    }
    setIsPolishingTitles(true);
    clearStatus();
    try {
      const result = await generateTitleOptions({
        title, body, count: 5,
        system_prompt_override: titlePromptPick.content || undefined,
      });
      if (titlePromptPick.promptId !== null && titlePromptPick.version !== null) {
        await composer.recordUsages(
          [{ promptId: titlePromptPick.promptId, version: titlePromptPick.version }],
          {
            module: "draft_studio", promptType: "title", accountId: accountFilter,
            targetType: "draft_title", targetId: selectedDraft?.id ?? null,
          },
        );
      }
      setTitleOptions(result.items);
      setMessage("标题候选已生成。");
    } catch (err: unknown) {
      const d = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      setError(d || "标题润色失败，请确认已配置默认文本模型。");
    } finally {
      setIsPolishingTitles(false);
    }
  }

  async function handleGenerateTags() {
    if (!body.trim()) {
      setError("请先填写正文。");
      return;
    }
    setIsPolishingTags(true);
    clearStatus();
    try {
      const result = await generateTagOptions({ title, body, count: 8 });
      setTagOptions(result.items);
      setMessage("标签候选已生成。");
    } catch (err: unknown) {
      const d = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      setError(d || "标签润色失败，请确认已配置默认文本模型。");
    } finally {
      setIsPolishingTags(false);
    }
  }

  async function handleSendToPublish() {
    if (!selectedDraft) {
      setError("请先选择一个草稿。");
      return;
    }
    // 总纲第七节硬要求：顺序没落库不许送发布，否则发布中心会按旧顺序复制素材。
    if (orderStatus === "saving") {
      setError("图片顺序正在保存，请等保存完成后再送发布中心。");
      return;
    }
    if (orderStatus === "error") {
      setError("图片顺序保存失败，请重新拖动一次确认顺序后再送发布中心。");
      return;
    }
    setIsSendingPublish(true);
    clearStatus();
    try {
      const saved = await updateDraft(selectedDraft.id, { title, body });
      const job = await sendDraftToPublish(saved.id, {
        platform_account_id: accountFilter ?? selectedDraft.platform_account_id ?? undefined,
        publish_mode: "immediate",
      });
      upsertDraft(saved);
      setMessage(`已送入发布中心，发布任务 #${job.id}。请前往发布中心选择账号并发布。`);
    } catch (err: unknown) {
      const d = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      setError(d || "送发布中心失败。");
    } finally {
      setIsSendingPublish(false);
    }
  }

  useEffect(() => {
    void loadDrafts();
  }, [accountFilter]);
  useEffect(() => {
    void fetchAccounts("xhs").then(setAccounts).catch(() => setAccounts([]));
  }, []);

  useEffect(() => {
    if (!selectedDraft) {
      setSourceNote(null);
      setSourceAssets([]);
      return;
    }
    let cancelled = false;
    // 换草稿时重置顺序状态，否则上一篇的 saved/error 会误挡或误放当前这篇。
    inFlightReorders.current = 0;
    setOrderStatus("idle");
    (async () => {
      // Load assets from draft (not source note)
      try {
        const assetsResult = await fetchDraftAssets(selectedDraft.id);
        if (!cancelled) setSourceAssets(assetsResult.items);
      } catch {
        if (!cancelled) setSourceAssets([]);
      }

      // Load source note info for display only
      if (selectedDraft.source_note_id) {
        try {
          const note = await fetchSavedNote(selectedDraft.source_note_id, true);
          if (!cancelled) setSourceNote(note);
        } catch {
          if (!cancelled) setSourceNote(null);
        }
      } else {
        if (!cancelled) setSourceNote(null);
      }
    })();
    return () => { cancelled = true; };
  }, [selectedDraft?.id, selectedDraft?.source_note_id]);

  useEffect(() => {
    if (uploadModalOpen) {
      void fetchUserImages().then(r => setUserImages(r.items)).catch(() => {});
      void fetchGeneratedImageAssets().then(r => setAiAssets(r.items)).catch(() => {});
      setSelectedAssetUrls([]);
    }
  }, [uploadModalOpen]);

  useEffect(() => {
    if (generateAssetPickerOpen) {
      void fetchUserImages().then((result) => setUserImages(result.items)).catch(() => {});
      void fetchGeneratedImageAssets().then((result) => setAiAssets(result.items)).catch(() => {});
    }
  }, [generateAssetPickerOpen]);

  useEffect(() => {
    if (refPickerOpen) {
      void fetchUserImages().then((r) => setUserImages(r.items)).catch(() => {});
      void fetchGeneratedImageAssets().then((r) => setAiAssets(r.items)).catch(() => {});
    }
  }, [refPickerOpen]);

  // ---- Rewrite mode: 3-column layout ----
  function renderRewriteMode() {
    return (
      <Row gutter={16}>
        {/* Col 1: Draft queue */}
        <Col xs={24} lg={6}>
          <Card
            title={
              <Space>
                <Text strong>草稿队列</Text>
                <Badge count={drafts.length} style={{ backgroundColor: "#1668dc" }} />
              </Space>
            }
            styles={{ body: { padding: 0 } }}
          >
            {isLoading ? (
              <div style={{ textAlign: "center", padding: 32 }}>
                <Spin />
                <Paragraph type="secondary" style={{ marginTop: 12 }}>正在加载草稿...</Paragraph>
              </div>
            ) : drafts.length === 0 ? (
              <Empty
                image={<FileTextOutlined style={{ fontSize: 40, color: "#8c8c8c" }} />}
                imageStyle={{ height: 56 }}
                description={
                  <div>
                    <Paragraph type="secondary">还没有可编辑草稿。</Paragraph>
                    <Link to="/platforms/xhs/library">
                      <Button size="small">去内容库选择笔记</Button>
                    </Link>
                  </div>
                }
                style={{ padding: 24 }}
              />
            ) : (
              <List
                dataSource={drafts}
                renderItem={(draft) => {
                  const isActive = draft.id === selectedDraftId;
                  return (
                    <List.Item
                      onClick={() => selectDraft(draft)}
                      style={{
                        cursor: "pointer",
                        padding: "10px 16px",
                        background: isActive ? "#1668dc10" : "transparent",
                        borderLeft: isActive ? "2px solid #1668dc" : "2px solid transparent",
                        transition: "all 0.15s",
                        margin: 0,
                      }}
                    >
                      <div style={{ minWidth: 0, width: "100%", display: "flex", alignItems: "center", gap: 4 }}>
                        <div style={{ flex: 1, minWidth: 0 }}>
                          <Text
                            ellipsis
                            style={{
                              display: "block",
                              fontSize: 13,
                              fontWeight: isActive ? 600 : 400,
                            }}
                          >
                            {draft.title || "未命名草稿"}
                          </Text>
                          <Text type="secondary" style={{ fontSize: 11 }}>
                            {formatDraftTime(draft.created_at)}
                          </Text>
                        </div>
                        <Popconfirm title="删除此草稿？" onConfirm={(e) => { e?.stopPropagation(); void handleDeleteDraft(draft.id); }} onCancel={(e) => e?.stopPropagation()}>
                          <Button type="text" danger size="small" icon={<DeleteOutlined />} onClick={(e) => e.stopPropagation()} style={{ flexShrink: 0 }} />
                        </Popconfirm>
                      </div>
                    </List.Item>
                  );
                }}
                style={{ maxHeight: "calc(100vh - 380px)", overflowY: "auto" }}
              />
            )}
          </Card>
        </Col>

        {/* Col 2: Main editor */}
        <Col xs={24} lg={12}>
          {sourceNote && (
            <Card
              title="草稿内容"
              size="small"
              style={{ marginBottom: 16 }}
              extra={
                <a
                  href={getNoteUrl(sourceNote)}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  <Button type="link" size="small" icon={<LinkOutlined />}>查看原文</Button>
                </a>
              }
            >
              <Text strong style={{ display: "block", marginBottom: 4 }}>{sourceNote.title}</Text>
              <Paragraph
                ellipsis={{ rows: 3, expandable: true, symbol: "展开" }}
                type="secondary"
                style={{ marginBottom: 8, fontSize: 13 }}
              >
                {sourceNote.content}
              </Paragraph>

              {hasImageAssets && (
                <div style={{ marginBottom: 8 }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      <FileImageOutlined /> 图片素材 ({imageAssets.length}) — 拖拽调整顺序
                    </Text>
                  </div>
                  <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={handleDragEnd}>
                    <SortableContext items={imageAssets.map((a) => a.id)} strategy={horizontalListSortingStrategy}>
                      <div style={{ display: "flex", flexWrap: "wrap", gap: 12 }}>
                        {imageAssets.map((asset) => (
                          <SortableRewriteImage
                            key={asset.id}
                            asset={asset}
                            onEdit={() => { setOptimizeAssetId(asset.id); setOptimizeModalOpen(true); }}
                            onRemove={() => handleRemoveAsset(asset.id)}
                            onView={() => setPreviewImage(asset.url || asset.local_path)}
                          />
                        ))}
                        <div
                          onClick={() => setUploadModalOpen(true)}
                          style={{
                            width: 60, height: 60, borderRadius: 4, border: "1px dashed #434343",
                            display: "flex", alignItems: "center", justifyContent: "center",
                            cursor: "pointer", background: "rgba(255,255,255,0.04)",
                          }}
                        >
                          <PlusOutlined style={{ fontSize: 20, color: "#8c8c8c" }} />
                        </div>
                      </div>
                    </SortableContext>
                  </DndContext>
                </div>
              )}

              {hasVideoAssets && (
                <div style={{ marginBottom: 8 }}>
                  {sourceAssets.filter((a) => a.asset_type === "video").map((asset) => (
                    <div key={asset.id} style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
                      <Button
                        type="link" size="small" icon={<PlayCircleOutlined />}
                        href={asset.url || sourceNote.video_url} target="_blank" rel="noopener noreferrer"
                      >
                        查看视频
                      </Button>
                      <Button type="text" size="small" icon={<EditOutlined />}
                        onClick={() => { setOptimizeAssetId(asset.id); setOptimizeModalOpen(true); }}
                        style={{ color: "#1668dc" }}
                      />
                    </div>
                  ))}
                </div>
              )}

              <Space size={4}>
                <Tag color={hasVideoAssets ? "purple" : "blue"}>
                  {hasVideoAssets ? "视频" : "图文"}
                </Tag>
                {hasImageAssets && (
                  <>
                    <PictureOutlined style={{ color: "#8c8c8c", fontSize: 12 }} />
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      {sourceAssets.filter((a) => a.asset_type === "image").length} 张图片
                    </Text>
                  </>
                )}
              </Space>
            </Card>
          )}
          <Card title="编辑器">
            <Form layout="vertical">
              <Form.Item label="标题" style={{ marginBottom: 16 }}>
                <Input
                  value={title}
                  onChange={(e) => setTitle(e.target.value)}
                  placeholder="生成或选择一个标题"
                  size="large"
                />
              </Form.Item>
              <Form.Item label="正文" style={{ marginBottom: 16 }}>
                <TextArea
                  value={body}
                  onChange={(e) => setBody(e.target.value)}
                  placeholder="正文生成后可在这里继续编辑。"
                  rows={16}
                  style={{ fontFamily: "monospace" }}
                />
              </Form.Item>
              <Form.Item
                label={
                  <Space>
                    <PictureOutlined />
                    <span>图片素材（{imageAssets.length}）</span>
                    <Text type="secondary" style={{ fontSize: 12 }}>序号即发布顺序，拖拽可改序</Text>
                    <OrderSaveIndicator status={orderStatus} />
                  </Space>
                }
                style={{ marginBottom: 16 }}
              >
                <Space style={{ marginBottom: 12 }} wrap>
                  <Button
                    type="primary"
                    icon={<FileImageOutlined />}
                    onClick={() => setLibraryPickerOpen(true)}
                    disabled={!selectedDraft}
                  >
                    从素材库选图
                  </Button>
                  <Button
                    icon={<RobotOutlined />}
                    onClick={() => void handleRegenerateImage()}
                    loading={isRegeneratingImage}
                    disabled={!selectedDraft}
                  >
                    重新生成图片
                  </Button>
                  <Button
                    icon={<PictureOutlined />}
                    onClick={() => setUploadModalOpen(true)}
                    disabled={!selectedDraft}
                  >
                    上传 / 链接添加
                  </Button>
                </Space>
                <DraftAssetOrderPanel
                  assets={imageAssets}
                  status={orderStatus}
                  disabled={!selectedDraft}
                  onReorder={persistImageOrder}
                  onSetCover={(asset) => void handleSetCover(asset)}
                  onRemove={(asset) => handleRemoveAsset(asset.id)}
                  onReplace={(asset) => {
                    setReplaceAssetId(asset.id);
                    setReplaceUrlInput(asset.url);
                  }}
                  onView={(src) => setPreviewImage(src)}
                />
              </Form.Item>
              <Form.Item label="标签" style={{ marginBottom: 16 }}>
                <Space size={[4, 8]} wrap>
                  {draftTags.map((tag) => (
                    <Tag
                      key={tag.id || tag.name}
                      closable
                      onClose={() => setDraftTags((prev) => prev.filter((t) => t.name !== tag.name))}
                      color="blue"
                    >
                      #{tag.name}
                    </Tag>
                  ))}
                  {isAddingTag ? (
                    <Input
                      size="small"
                      style={{ width: 120 }}
                      value={newTagInput}
                      onChange={(e) => setNewTagInput(e.target.value)}
                      onPressEnter={() => {
                        const name = newTagInput.trim();
                        if (name && !draftTags.some((t) => t.name === name)) {
                          setDraftTags((prev) => [...prev, { id: "", name }]);
                        }
                        setNewTagInput("");
                        setIsAddingTag(false);
                      }}
                      onBlur={() => {
                        const name = newTagInput.trim();
                        if (name && !draftTags.some((t) => t.name === name)) {
                          setDraftTags((prev) => [...prev, { id: "", name }]);
                        }
                        setNewTagInput("");
                        setIsAddingTag(false);
                      }}
                      autoFocus
                      placeholder="输入标签"
                    />
                  ) : (
                    <Tag
                      style={{ cursor: "pointer", borderStyle: "dashed" }}
                      onClick={() => setIsAddingTag(true)}
                    >
                      + 添加标签
                    </Tag>
                  )}
                </Space>
              </Form.Item>
              <Button
                type="primary"
                icon={<SaveOutlined />}
                onClick={handleSave}
                loading={isSaving}
                disabled={!selectedDraft}
              >
                保存
              </Button>
            </Form>
          </Card>
        </Col>

        {/* Col 3: AI assistant */}
        <Col xs={24} lg={6}>
          <div style={{ position: "sticky", top: 16, maxHeight: "calc(100vh - 120px)", overflowY: "auto" }}>
          <Card title="AI 助手" size="small">
            <Collapse
              size="small"
              style={{ marginBottom: 16 }}
              items={[
                {
                  key: "system-prompt",
                  label: "系统提示词 (可选)",
                  children: (
                    <TextArea
                      rows={4}
                      value={systemPrompt}
                      onChange={(e) => setSystemPrompt(e.target.value)}
                      placeholder="你是小红书内容创作助手，擅长写吸引人的标题和正文。"
                    />
                  ),
                },
              ]}
            />

            <div style={{ marginBottom: 16 }}>
              <PromptPicker
                promptType="rewrite"
                module="draft_studio"
                accountId={accountFilter}
                label="改写指令（可选，选了就完全按它执行）"
                value={rewritePromptPick}
                onChange={setRewritePromptPick}
                disabled={isRewriting}
              />
              <Text type="secondary" style={{ fontSize: 12, display: "block", marginBottom: 6 }}>
                {rewritePromptPick.promptId !== null ? "补充说明（可留空）" : "改写要求"}
              </Text>
              <Input
                value={instruction}
                onChange={(e) => setInstruction(e.target.value)}
                placeholder="保留事实，增强种草感"
                style={{ marginBottom: 8 }}
              />
              <Button
                onClick={handleRewrite}
                loading={isRewriting}
                disabled={!selectedDraft}
                block
                type="primary"
                icon={<ExperimentOutlined />}
              >
                AI 改写正文
              </Button>
            </div>
            <div style={{ marginBottom: 16 }}>
              <PromptPicker
                promptType="title"
                module="draft_studio"
                accountId={accountFilter}
                label="标题指令（可选）"
                value={titlePromptPick}
                onChange={setTitlePromptPick}
                disabled={isPolishingTitles}
              />
            </div>

            {rewritePreview !== null && (
              <Card size="small" title="改写结果" style={{ marginBottom: 16, background: "#1a2332", borderColor: "#1668dc40" }}>
                <Paragraph style={{ whiteSpace: "pre-wrap", color: "rgba(255,255,255,.75)", fontSize: 13, maxHeight: 240, overflow: "auto" }}>{rewritePreview}</Paragraph>
                <Space style={{ marginTop: 8 }}>
                  <Button type="primary" size="small" onClick={applyRewrite}>采用</Button>
                  <Button size="small" onClick={discardRewrite}>放弃</Button>
                </Space>
              </Card>
            )}

            <Divider style={{ margin: "12px 0" }} />

            <Space direction="vertical" style={{ width: "100%" }} size={8}>
              <Button onClick={handleGenerateTitles} loading={isPolishingTitles} block icon={<EditOutlined />}>润色标题</Button>
              <Button onClick={handleGenerateTags} loading={isPolishingTags} block icon={<TagsOutlined />}>润色标签</Button>
            </Space>

            <Divider />

            {titleOptions.length > 0 && (
              <div style={{ marginBottom: 16 }}>
                <Text type="secondary" style={{ fontSize: 12, display: "block", marginBottom: 8 }}>
                  标题建议（点击选用）
                </Text>
                <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                  {titleOptions.map((option) => (
                    <div
                      key={option}
                      onClick={() => setTitle(option)}
                      style={{
                        cursor: "pointer",
                        padding: "4px 8px",
                        borderRadius: 4,
                        background: "rgba(22, 104, 220, 0.08)",
                        border: "1px solid #1668dc30",
                        fontSize: 12,
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        whiteSpace: "nowrap",
                      }}
                      title={option}
                    >
                      {option}
                    </div>
                  ))}
                </div>
              </div>
            )}

            {tagOptions.length > 0 && (
              <div>
                <Text type="secondary" style={{ fontSize: 12, display: "block", marginBottom: 8 }}>
                  标签建议（点击添加）
                </Text>
                <Space size={[6, 6]} wrap>
                  {tagOptions.map((tag) => (
                    <Tag
                      key={tag}
                      color="geekblue"
                      onClick={() => {
                        if (!draftTags.some((t) => t.name === tag)) {
                          setDraftTags((prev) => [...prev, { id: "", name: tag }]);
                        }
                      }}
                      style={{ cursor: "pointer" }}
                    >
                      #{tag}
                    </Tag>
                  ))}
                </Space>
              </div>
            )}

            <Divider style={{ margin: "12px 0" }} />

            <div>
              <Tooltip
                title={
                  orderStatus === "saving"
                    ? "图片顺序正在保存，保存完成后才能送发布"
                    : orderStatus === "error"
                      ? "图片顺序保存失败，请重新拖动一次确认顺序"
                      : ""
                }
              >
                <Button
                  onClick={handleSendToPublish}
                  loading={isSendingPublish}
                  disabled={!selectedDraft || orderStatus === "saving" || orderStatus === "error"}
                  block
                  type="primary"
                  icon={<SendOutlined />}
                >
                  送入发布中心
                </Button>
              </Tooltip>
              {(orderStatus === "saving" || orderStatus === "error") && (
                <div style={{ marginTop: 6, textAlign: "center" }}>
                  <OrderSaveIndicator status={orderStatus} />
                </div>
              )}
            </div>
          </Card>
          </div>
        </Col>

        {/* Optimize modal */}
        <Modal
          title="素材优化"
          open={optimizeModalOpen}
          onCancel={() => { setOptimizeModalOpen(false); setOptimizeAssetId(null); setOptimizePrompt(""); setOptimizeRefImages([]); setOptimizeResult(null); }}
          footer={null}
          width={600}
        >
          {(() => {
            const asset = sourceAssets.find((a) => a.id === optimizeAssetId);
            if (!asset) return <Text type="secondary">素材不存在</Text>;
            const isImage = asset.asset_type === "image";
            return (
              <div>
                <Row gutter={16} style={{ marginBottom: 16 }}>
                  <Col span={12}>
                    <div style={{ padding: 8, background: "rgba(255,255,255,0.04)", borderRadius: 8, textAlign: "center" }}>
                      <Text type="secondary" style={{ fontSize: 11, display: "block", marginBottom: 4 }}>当前素材</Text>
                      {isImage ? (
                        <Image
                          src={asset.url || asset.local_path}
                          width={180}
                          height={180}
                          style={{ objectFit: "contain", borderRadius: 6 }}
                          referrerPolicy="no-referrer"
                          preview={{ mask: <EyeOutlined style={{ fontSize: 16 }} /> }}
                        />
                      ) : (
                        <div style={{ height: 180, display: "flex", alignItems: "center", justifyContent: "center" }}>
                          <Space direction="vertical" align="center"><PlayCircleOutlined style={{ fontSize: 32, color: "#1668dc" }} /><Text style={{ fontSize: 12 }}>视频</Text></Space>
                        </div>
                      )}
                    </div>
                  </Col>
                  <Col span={12}>
                    <div style={{ padding: 8, background: "rgba(255,255,255,0.04)", borderRadius: 8, textAlign: "center" }}>
                      <Text type="secondary" style={{ fontSize: 11, display: "block", marginBottom: 4 }}>优化后预览</Text>
                      {optimizeResult ? (
                        <Image
                          src={optimizeResult}
                          width={180}
                          height={180}
                          style={{ objectFit: "contain", borderRadius: 6 }}
                          referrerPolicy="no-referrer"
                          preview={{ mask: <EyeOutlined style={{ fontSize: 16 }} /> }}
                        />
                      ) : (
                        <div style={{ width: 180, height: 180, margin: "0 auto", border: "1px dashed #434343", borderRadius: 6, display: "flex", alignItems: "center", justifyContent: "center" }}>
                          <Text type="secondary" style={{ fontSize: 12 }}>{isOptimizing ? "生成中..." : "等待生成"}</Text>
                        </div>
                      )}
                    </div>
                  </Col>
                </Row>

                {isImage ? (
                  <div>
                    <div style={{ marginBottom: 8 }}>
                      <Text type="secondary" style={{ fontSize: 12, display: "block", marginBottom: 4 }}>额外参考图（可选）</Text>
                      <Space size={8} wrap>
                        {optimizeRefImages.map((url, idx) => (
                          <div key={idx} style={{ position: "relative", width: 40, height: 40, borderRadius: 4, overflow: "hidden", border: "1px solid #333" }}>
                            <img src={url} style={{ width: "100%", height: "100%", objectFit: "cover" }} referrerPolicy="no-referrer" />
                            <Button type="text" danger size="small" icon={<DeleteOutlined />}
                              onClick={() => setOptimizeRefImages((prev) => prev.filter((_, i) => i !== idx))}
                              style={{ position: "absolute", top: -4, right: -4, fontSize: 8, width: 14, height: 14, minWidth: 14, padding: 0, background: "rgba(0,0,0,.6)", borderRadius: "50%" }}
                            />
                          </div>
                        ))}
                        <div
                          onMouseDown={(e) => e.preventDefault()}
                          onClick={() => setRefPickerOpen(true)}
                          style={{ width: 40, height: 40, borderRadius: 4, border: "1px dashed #434343", display: "flex", alignItems: "center", justifyContent: "center", cursor: "pointer", background: "rgba(255,255,255,0.04)" }}
                        >
                          <PlusOutlined style={{ fontSize: 14, color: "#8c8c8c" }} />
                        </div>
                      </Space>
                    </div>
                    <Input.TextArea
                      value={optimizePrompt}
                      onChange={(e) => setOptimizePrompt(e.target.value)}
                      placeholder="描述你想要的润色效果，如：提升画面清晰度和色彩饱和度，增加小红书风格滤镜..."
                      rows={3}
                      style={{ marginBottom: 12 }}
                      disabled={isOptimizing}
                    />
                    <Space direction="vertical" style={{ width: "100%" }} size={8}>
                      <Button
                        block
                        type="primary"
                        icon={<HighlightOutlined />}
                        loading={isOptimizing}
                        onClick={async () => {
                          if (!optimizePrompt.trim()) { antMessage.warning("请输入润色提示词"); return; }
                          setIsOptimizing(true); setOptimizeResult(null);
                          try {
                            const refImages = [asset.url || asset.local_path, ...optimizeRefImages];
                            const result = await generateImageWithAi({
                              prompt: `基于提供的第一张原图进行润色修改。要求：${optimizePrompt.trim()}`,
                              reference_images: refImages,
                              save_to_assets: false,
                            });
                            setOptimizeResult(result.url);
                            antMessage.success("图片润色完成");
                          } catch {
                            antMessage.error("图片润色失败，请确认已配置图片生成模型");
                          } finally { setIsOptimizing(false); }
                        }}
                      >
                        图片润色
                      </Button>
                      {optimizeResult && (
                        <Button
                          block
                          onClick={() => {
                            const draftId = selectedDraft?.id;
                            if (draftId && optimizeAssetId && optimizeAssetId > 0) {
                              void updateDraftAsset(draftId, optimizeAssetId, { url: optimizeResult!, local_path: "" }).then((saved) => {
                                setSourceAssets((prev) => prev.map((a) => a.id === optimizeAssetId ? saved : a));
                              }).catch(() => {
                                setSourceAssets((prev) => prev.map((a) => a.id === optimizeAssetId ? { ...a, url: optimizeResult!, local_path: "" } : a));
                              });
                            } else {
                              setSourceAssets((prev) => prev.map((a) => a.id === optimizeAssetId ? { ...a, url: optimizeResult!, local_path: "" } : a));
                            }
                            setOptimizeModalOpen(false); setOptimizeAssetId(null); setOptimizePrompt(""); setOptimizeRefImages([]); setOptimizeResult(null);
                            antMessage.success("已替换为润色后的图片");
                          }}
                        >
                          替换当前素材
                        </Button>
                      )}
                    </Space>
                  </div>
                ) : (
                  <Button block icon={<VideoCameraOutlined />} disabled>
                    视频润色（即将上线）
                  </Button>
                )}
              </div>
            );
          })()}
        </Modal>

        {/* Reference image picker modal */}
        <Modal title="选择参考图" open={refPickerOpen} onCancel={() => { setRefPickerOpen(false); setRefPickerUrlInput(""); }} footer={null} width={560}>
          <Tabs size="small" items={[
            {
              key: "user",
              label: "普通图片资产",
              children: userImages.length === 0 ? <Text type="secondary">暂无</Text> : (
                <div style={{ display: "flex", flexWrap: "wrap", gap: 8, maxHeight: 200, overflowY: "auto" }}>
                  {userImages.map((img) => (
                    <div key={img.file_name} onClick={() => { setOptimizeRefImages((prev) => [...prev, img.url]); setRefPickerOpen(false); }}
                      style={{ width: 60, height: 60, borderRadius: 4, overflow: "hidden", cursor: "pointer", border: "2px solid transparent" }}>
                      <img src={img.url} style={{ width: "100%", height: "100%", objectFit: "cover" }} referrerPolicy="no-referrer" />
                    </div>
                  ))}
                </div>
              ),
            },
            {
              key: "ai",
              label: "AI 图片资产",
              children: aiAssets.length === 0 ? <Text type="secondary">暂无</Text> : (
                <div style={{ display: "flex", flexWrap: "wrap", gap: 8, maxHeight: 200, overflowY: "auto" }}>
                  {aiAssets.filter((a) => Boolean(a.file_path)).map((asset) => (
                    <div key={asset.id} onClick={() => { setOptimizeRefImages((prev) => [...prev, asset.file_path]); setRefPickerOpen(false); }}
                      style={{ width: 60, height: 60, borderRadius: 4, overflow: "hidden", cursor: "pointer", border: "2px solid transparent" }}>
                      <img src={asset.file_path} style={{ width: "100%", height: "100%", objectFit: "cover" }} referrerPolicy="no-referrer" />
                    </div>
                  ))}
                </div>
              ),
            },
            {
              key: "url",
              label: "URL",
              children: (
                <Space.Compact style={{ width: "100%" }}>
                  <Input placeholder="粘贴图片 URL" value={refPickerUrlInput} onChange={(e) => setRefPickerUrlInput(e.target.value)} />
                  <Button type="primary" onClick={() => {
                    const v = refPickerUrlInput.trim();
                    if (v && (v.startsWith("http://") || v.startsWith("https://") || v.startsWith("/api/"))) {
                      setOptimizeRefImages((prev) => [...prev, v]);
                      setRefPickerUrlInput("");
                      setRefPickerOpen(false);
                    }
                  }}>添加</Button>
                </Space.Compact>
              ),
            },
          ]} />
        </Modal>

        {/* Upload modal */}
        <DraftAssetPicker
          open={libraryPickerOpen}
          draftAssets={sourceAssets}
          confirming={isAddingFromLibrary}
          onClose={() => setLibraryPickerOpen(false)}
          onConfirm={(assets) => void handleAddFromLibrary(assets)}
        />

        <Modal
          title="替换图片"
          open={replaceAssetId !== null}
          onCancel={() => { setReplaceAssetId(null); setReplaceUrlInput(""); }}
          onOk={() => void handleConfirmReplace()}
          okText="替换"
          cancelText="取消"
          confirmLoading={isReplacing}
          width={480}
          destroyOnHidden
        >
          <Space direction="vertical" size={8} style={{ width: "100%" }}>
            <Text type="secondary" style={{ fontSize: 12 }}>
              替换只改这一张图片的地址，不改变它在草稿里的顺序与封面状态。
            </Text>
            <Input
              placeholder="粘贴新的图片链接（http:// 或 https://）"
              value={replaceUrlInput}
              onChange={(event) => setReplaceUrlInput(event.target.value)}
            />
          </Space>
        </Modal>

        <Modal title="上传素材" open={uploadModalOpen} onCancel={() => setUploadModalOpen(false)} footer={null} width={480}>
          <Upload.Dragger
            accept=".jpg,.jpeg,.png,.gif,.webp,.mp4,.mov,.avi,.mkv"
            multiple
            showUploadList={false}
            beforeUpload={(file) => handleFileUpload(file as File)}
            style={{ marginBottom: 12 }}
          >
            <p className="ant-upload-drag-icon">
              <InboxOutlined />
            </p>
            <p className="ant-upload-text" style={{ fontSize: 13 }}>
              点击或拖拽文件上传
            </p>
            <p className="ant-upload-hint" style={{ fontSize: 12 }}>
              支持 JPG / PNG / GIF / WEBP / MP4 / MOV
            </p>
          </Upload.Dragger>
          <div style={{ marginTop: 12 }}>
            <Text type="secondary" style={{ fontSize: 12, marginBottom: 4, display: "block" }}>或粘贴链接</Text>
            <Space.Compact style={{ width: "100%" }}>
              <Input
                placeholder="粘贴图片或视频链接"
                prefix={<LinkOutlined />}
                value={assetUrl}
                onChange={(e) => setAssetUrl(e.target.value)}
                onPressEnter={handleUrlAdd}
              />
              <Button
                type="primary"
                icon={<PlusOutlined />}
                onClick={handleUrlAdd}
              >
                添加
              </Button>
            </Space.Compact>
          </div>

          <Divider style={{ margin: "12px 0" }}>或从图片资产选择</Divider>

          <Tabs size="small" items={[
            {
              key: "user",
              label: "普通图片资产",
              children: userImages.length === 0 ? (
                <Text type="secondary" style={{ fontSize: 12 }}>暂无普通图片资产，请先在图片工坊上传</Text>
              ) : (
                <div style={{ display: "flex", flexWrap: "wrap", gap: 8, maxHeight: 200, overflowY: "auto" }}>
                  {userImages.map((img) => (
                    <div key={img.file_name} onClick={() => toggleAssetUrl(img.url)}
                      style={{
                        width: 60, height: 60, borderRadius: 4, overflow: "hidden", cursor: "pointer",
                        border: selectedAssetUrls.includes(img.url) ? "2px solid #1668dc" : "2px solid transparent",
                      }}>
                      <img src={img.url} style={{ width: "100%", height: "100%", objectFit: "cover" }} referrerPolicy="no-referrer" />
                    </div>
                  ))}
                </div>
              ),
            },
            {
              key: "ai",
              label: "AI 图片资产",
              children: aiAssets.length === 0 ? (
                <Text type="secondary" style={{ fontSize: 12 }}>暂无 AI 图片资产，请先在图片工坊生成</Text>
              ) : (
                <div style={{ display: "flex", flexWrap: "wrap", gap: 8, maxHeight: 200, overflowY: "auto" }}>
                  {aiAssets.filter((asset) => Boolean(asset.file_path)).map((asset) => (
                    <div key={asset.id} onClick={() => toggleAssetUrl(asset.file_path)}
                      style={{
                        width: 60, height: 60, borderRadius: 4, overflow: "hidden", cursor: "pointer",
                        border: selectedAssetUrls.includes(asset.file_path) ? "2px solid #1668dc" : "2px solid transparent",
                      }}>
                      <img src={asset.file_path} style={{ width: "100%", height: "100%", objectFit: "cover" }} referrerPolicy="no-referrer" />
                    </div>
                  ))}
                </div>
              ),
            },
          ]} />

          {selectedAssetUrls.length > 0 && (
            <Button type="primary" block style={{ marginTop: 12 }} onClick={handleAddSelectedAssets}>
              添加选中的 {selectedAssetUrls.length} 个图片
            </Button>
          )}
        </Modal>

        {/* Image preview */}
        {previewImage && (
          <Image
            src={previewImage}
            style={{ display: "none" }}
            preview={{ visible: true, onVisibleChange: (v) => { if (!v) setPreviewImage(null); } }}
          />
        )}
      </Row>
    );
  }

  // ---- Generate mode: centered card ----
  function renderGenerateMode() {
    return (
      <div style={{ maxWidth: 640, margin: "0 auto" }}>
        <Card title="AI 笔记生成">
          <Form layout="vertical">
            <Form.Item label="选题" required>
              <Input
                value={topic}
                onChange={(e) => setTopic(e.target.value)}
                placeholder="例如：通勤低卡早餐怎么搭配"
              />
            </Form.Item>
            <Form.Item label="参考材料">
              <TextArea
                value={reference}
                onChange={(e) => setReference(e.target.value)}
                placeholder="竞品笔记、卖点、评论洞察或人群信息"
                rows={6}
              />
            </Form.Item>
            <PromptPicker
              promptType="note"
              module="draft_studio"
              accountId={accountFilter}
              label="生文指令（可选，选了就完全按它执行）"
              value={notePromptPick}
              onChange={setNotePromptPick}
              disabled={isGenerating}
            />
            <Form.Item
              label="AI 指令"
              extra={
                <Text type="secondary" style={{ fontSize: 12 }}>
                  {notePromptPick.promptId !== null
                    ? "已选指令，这里作为本次补充说明（可留空）"
                    : "未选指令时，按这里的要求生成"}
                </Text>
              }
            >
              <Input
                value={instruction}
                onChange={(e) => setInstruction(e.target.value)}
                placeholder="保留事实，增强小红书种草感，语气自然。"
              />
            </Form.Item>
            <PromptPicker
              promptType="image"
              module="draft_studio"
              accountId={accountFilter}
              label="配图指令（可选）"
              value={draftImagePromptPick}
              onChange={setDraftImagePromptPick}
              disabled={isGenerating}
            />
            <Form.Item label="复用图片工坊素材（可选）">
              <Space direction="vertical" style={{ width: "100%" }} size={8}>
                <Button
                  icon={<PictureOutlined />}
                  onClick={() => setGenerateAssetPickerOpen(true)}
                >
                  从图片工坊选择
                </Button>
                {selectedGenerateAssetUrls.length > 0 && (
                  <Space size={8} wrap>
                    {selectedGenerateAssetUrls.map((url) => (
                      <div key={url} style={{ position: "relative" }}>
                        <img
                          src={url}
                          width={56}
                          height={56}
                          style={{ objectFit: "cover", borderRadius: 6 }}
                          referrerPolicy="no-referrer"
                        />
                        <Button
                          type="text"
                          danger
                          size="small"
                          icon={<DeleteOutlined />}
                          onClick={() =>
                            setSelectedGenerateAssetUrls((current) =>
                              current.filter((item) => item !== url),
                            )
                          }
                          style={{
                            position: "absolute",
                            right: -7,
                            top: -7,
                            width: 20,
                            minWidth: 20,
                            height: 20,
                            padding: 0,
                            borderRadius: "50%",
                            background: "rgba(0,0,0,.75)",
                          }}
                        />
                      </div>
                    ))}
                  </Space>
                )}
              </Space>
            </Form.Item>
            <Form.Item style={{ marginBottom: generateImages ? 12 : 20 }}>
              <Checkbox
                checked={generateImages}
                onChange={(event) => setGenerateImages(event.target.checked)}
              >
                同时生成图片，并保存到图片工坊及当前草稿
              </Checkbox>
            </Form.Item>
            {generateImages && (
              <Row gutter={12}>
                <Col xs={24} sm={7}>
                  <Form.Item label="图片数量">
                    <Select
                      value={imageCount}
                      onChange={setImageCount}
                      options={[
                        { value: 1, label: "1 张" },
                        { value: 2, label: "2 张" },
                        { value: 3, label: "3 张" },
                      ]}
                    />
                  </Form.Item>
                </Col>
                <Col xs={24} sm={17}>
                  <Form.Item label="图片要求（可选）">
                    <Input
                      value={imagePrompt}
                      onChange={(event) => setImagePrompt(event.target.value)}
                      placeholder="不填写时，将根据选题、标题和正文自动生成配图"
                    />
                  </Form.Item>
                </Col>
              </Row>
            )}
            <Button
              type="primary"
              icon={<RobotOutlined />}
              onClick={handleGenerateNote}
              loading={isGenerating}
              block
            >
              {generateImages
                ? "一键生成笔记＋图片"
                : selectedGenerateAssetUrls.length > 0
                  ? "生成笔记并挂载所选图片"
                  : "生成草稿"}
            </Button>
          </Form>
        </Card>
        <AssetLibraryPicker
          open={generateAssetPickerOpen}
          confirming={false}
          onClose={() => setGenerateAssetPickerOpen(false)}
          onConfirm={(picked) => {
            // 只累加尚未选过的，重复点开弹窗不会把同一张图挂两次。
            setSelectedGenerateAssetUrls((current) => {
              const next = [...current];
              for (const asset of picked) {
                const url = asset.url || asset.local_path;
                if (url && !next.includes(url)) next.push(url);
              }
              return next;
            });
            setGenerateAssetPickerOpen(false);
          }}
          title="从素材库选择历史图片"
          confirmLabel="使用所选图片"
          isTaken={isGenerateAssetSelected}
          takenHint="已选择"
          takenBadge="已选"
        />
      </div>
    );
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <PageHeader
        eyebrow="XHS Drafts"
        title="草稿工坊"
        description="管理草稿、编辑内容和素材、AI 改写润色，生成结果送入发布中心。"
        action={
          <Button icon={<ReloadOutlined />} onClick={loadDrafts} loading={isLoading}>
            刷新草稿
          </Button>
        }
      />
      <Select allowClear value={accountFilter} onChange={setAccountFilter} placeholder="选择账号查看或生成草稿" style={{ width: 300 }} options={accounts.filter((item) => ["creator", "android"].includes(item.sub_type || "")).map((item) => ({ value: item.id, label: item.nickname || `#${item.id}` }))} />

      <Segmented
        value={activeMode}
        onChange={(val) => setActiveMode(val as AiContentMode)}
        options={segmentedOptions}
        block
        style={{ marginBottom: 8 }}
      />

      {error && (
        <Alert type="error" message={error} showIcon closable onClose={() => setError(null)} />
      )}
      {message && (
        <Alert type="success" message={message} showIcon closable onClose={() => setMessage(null)} />
      )}

      {activeMode === "rewrite" ? renderRewriteMode() : renderGenerateMode()}
      {composer.node}
    </div>
  );
}
