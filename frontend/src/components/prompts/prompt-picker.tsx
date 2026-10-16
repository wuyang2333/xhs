/**
 * 内联指令选择器。
 * 选中指令即完全按它执行。匹配条件仅筛选、排序候选，不会拼进提示词。
 */
import { FilterOutlined, StarFilled, StarOutlined } from "@ant-design/icons";
import { Button, Input, Select, Space, Tag, Tooltip, Typography } from "antd";
import { useEffect, useMemo, useState } from "react";

import { fetchAssetScenes, fetchProducts, fetchPromptRecommendations } from "../../lib/api";
import type { PromptTemplateItem } from "../../lib/api";
import {
  clearDefaultPrompt, readDefaultPrompt, readFavorites, readPromptMatchContext, readRecents,
  saveDefaultPrompt, savePromptMatchContext, toggleFavorite,
} from "./prompt-store";
import type { PromptMatchContext, PromptModule } from "./prompt-store";

const { Text } = Typography;
const TYPE_LABELS: Record<string, string> = {
  image: "生图", note: "生文", title: "标题", rewrite: "改写", auto_ops: "自动运营",
};
const ALL_PROMPT_TYPES = ["image", "note", "title", "rewrite", "auto_ops"];

type MatchTaxonomy = {
  products: Array<{ id: number; name: string }>;
  scenes: Array<{ id: number; name: string }>;
};
let taxonomyRequest: Promise<MatchTaxonomy> | null = null;

function fetchMatchTaxonomy(): Promise<MatchTaxonomy> {
  if (taxonomyRequest === null) {
    taxonomyRequest = Promise.all([fetchProducts(), fetchAssetScenes()])
      .then(([products, scenes]) => ({ products, scenes }))
      .catch((error: unknown) => { taxonomyRequest = null; throw error; });
  }
  return taxonomyRequest;
}

export type PromptPickerValue = { content: string; promptId: number | null; version: number | null };
export const EMPTY_PROMPT_PICK: PromptPickerValue = { content: "", promptId: null, version: null };

function normalizedBackendReasons(reasons: string[] | undefined): string[] {
  const labels: Record<string, string> = {
    account: "账号专属", account_match: "账号专属", product: "产品匹配", product_match: "产品匹配",
    scene: "场景匹配", scene_match: "场景匹配", keyword: "关键词匹配",
    scene_keyword: "关键词匹配", global: "全局通用",
  };
  return (reasons ?? []).map((reason) => labels[reason] ?? reason);
}

function matchReasons(
  item: PromptTemplateItem,
  context: PromptMatchContext & { accountId?: number },
  defaultPromptId: number | null,
): string[] {
  const reasons: string[] = [];
  if (item.id === defaultPromptId) reasons.push("默认指令");
  if (context.accountId && item.platform_account_id === context.accountId) reasons.push("账号专属");
  if (context.productId !== null && item.product_id === context.productId) reasons.push("产品匹配");
  if (context.sceneId !== null && item.scene_id === context.sceneId) reasons.push("场景匹配");
  if (context.scene.trim() && item.scenes.some((value) => (
    value.includes(context.scene.trim()) || context.scene.trim().includes(value)
  ))) reasons.push("关键词匹配");
  reasons.push(...normalizedBackendReasons(item.match_reasons));
  if (item.platform_account_id == null && item.product_id == null && item.scene_id == null) reasons.push("全局通用");
  return [...new Set(reasons)];
}

function exactMatchCount(item: PromptTemplateItem, context: PromptMatchContext & { accountId?: number }): number {
  let count = 0;
  if (context.accountId && item.platform_account_id === context.accountId) count += 1;
  if (context.productId !== null && item.product_id === context.productId) count += 1;
  if (context.sceneId !== null && item.scene_id === context.sceneId) count += 1;
  if (context.scene.trim() && item.scenes.some((value) => (
    value.includes(context.scene.trim()) || context.scene.trim().includes(value)
  ))) count += 1;
  return count;
}

export function PromptPicker(props: {
  promptType: string;
  module: PromptModule;
  accountId?: number;
  /** 页面有明确上下文时可传入；未传时用户可在匹配条件中选择。 */
  productId?: number | null;
  sceneId?: number | null;
  scene?: string;
  value: PromptPickerValue;
  onChange: (value: PromptPickerValue) => void;
  label?: string;
  disabled?: boolean;
}) {
  const { promptType, module, accountId, productId, sceneId, scene, value, onChange, label, disabled } = props;
  const savedContext = useMemo(() => readPromptMatchContext(module, promptType), [module, promptType]);
  const [localContext, setLocalContext] = useState<PromptMatchContext>(savedContext);
  const [debouncedScene, setDebouncedScene] = useState(scene ?? savedContext.scene);
  const [templates, setTemplates] = useState<PromptTemplateItem[]>([]);
  const [taxonomy, setTaxonomy] = useState<MatchTaxonomy>({ products: [], scenes: [] });
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [favorites, setFavorites] = useState<number[]>([]);
  const [defaultPromptId, setDefaultPromptId] = useState<number | null>(null);
  const [recents, setRecents] = useState<number[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [isTaxonomyLoading, setIsTaxonomyLoading] = useState(false);
  const typeLabel = TYPE_LABELS[promptType] ?? promptType;
  const context: PromptMatchContext = {
    productId: productId === undefined ? localContext.productId : productId,
    sceneId: sceneId === undefined ? localContext.sceneId : sceneId,
    scene: scene === undefined ? localContext.scene : scene,
  };
  const activeMatchCount = Number(context.productId !== null)
    + Number(context.sceneId !== null) + Number(Boolean(context.scene.trim()));

  useEffect(() => {
    const next = readPromptMatchContext(module, promptType);
    setLocalContext(next);
    setDebouncedScene(scene ?? next.scene);
    setFavorites(readFavorites());
    setDefaultPromptId(readDefaultPrompt(module, promptType));
    setRecents(readRecents(module, promptType));
  }, [module, promptType, scene]);

  useEffect(() => {
    const timer = window.setTimeout(() => setDebouncedScene(context.scene.trim()), 300);
    return () => window.clearTimeout(timer);
  }, [context.scene]);

  useEffect(() => {
    if (!filtersOpen) return;
    let active = true;
    setIsTaxonomyLoading(true);
    void fetchMatchTaxonomy()
      .then((result) => { if (active) setTaxonomy(result); })
      .catch(() => { if (active) setTaxonomy({ products: [], scenes: [] }); })
      .finally(() => { if (active) setIsTaxonomyLoading(false); });
    return () => { active = false; };
  }, [filtersOpen]);

  useEffect(() => {
    let active = true;
    setIsLoading(true);
    void (async () => {
      try {
        // 自动运营沿用“全部类型都能选”的原行为，其余入口只请求当前类型。
        const types = promptType === "auto_ops" ? ALL_PROMPT_TYPES : [promptType];
        const results = await Promise.all(types.map((targetType) => fetchPromptRecommendations({
          prompt_type: targetType,
          account_id: accountId,
          product_id: context.productId ?? undefined,
          scene_id: context.sceneId ?? undefined,
          scene: debouncedScene || undefined,
        })));
        if (!active) return;
        const byId = new Map<number, PromptTemplateItem>();
        for (const result of results) for (const item of result.candidates) byId.set(item.id, item);
        const items = [...byId.values()];
        setTemplates(items);
        // 账号适用范围变化后，旧账号专属指令可能已不再可用；此时同步清空，
        // 避免界面看似未选中、实际却继续提交旧指令正文。
        if (value.promptId !== null && !items.some((item) => item.id === value.promptId)) {
          onChange(EMPTY_PROMPT_PICK);
          return;
        }
        const preferred = readDefaultPrompt(module, promptType);
        if (preferred !== null && value.promptId === null) {
          const hit = items.find((item) => item.id === preferred);
          if (hit) onChange({ content: hit.content, promptId: hit.id, version: hit.current_version });
        }
      } catch {
        if (active) setTemplates([]);
      } finally {
        if (active) setIsLoading(false);
      }
    })();
    return () => { active = false; };
    // value/onChange 不入依赖，避免选中后立即重复请求。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [promptType, module, accountId, context.productId, context.sceneId, debouncedScene]);

  const sortedTemplates = useMemo(() => {
    const recentRank = new Map(recents.map((id, index) => [id, index]));
    const matchContext = { ...context, accountId };
    return [...templates].sort((a, b) => {
      const byDefault = Number(b.id === defaultPromptId) - Number(a.id === defaultPromptId);
      if (byDefault !== 0) return byDefault;
      const byExact = exactMatchCount(b, matchContext) - exactMatchCount(a, matchContext);
      if (byExact !== 0) return byExact;
      const byPriority = b.priority - a.priority;
      if (byPriority !== 0) return byPriority;
      const aRecent = recentRank.get(a.id) ?? Number.MAX_SAFE_INTEGER;
      const bRecent = recentRank.get(b.id) ?? Number.MAX_SAFE_INTEGER;
      if (aRecent !== bRecent) return aRecent - bRecent;
      const byBackendScore = (b.match_score ?? 0) - (a.match_score ?? 0);
      if (byBackendScore !== 0) return byBackendScore;
      return b.updated_at.localeCompare(a.updated_at);
    });
  }, [templates, recents, defaultPromptId, context, accountId]);

  const selected = useMemo(
    () => templates.find((item) => item.id === value.promptId) ?? null,
    [templates, value.promptId],
  );
  const isDefault = selected !== null && defaultPromptId === selected.id;

  function updateLocalContext(patch: Partial<PromptMatchContext>): void {
    const next = { ...localContext, ...patch };
    setLocalContext(next);
    savePromptMatchContext(module, promptType, next);
  }

  return (
    <div style={{ marginBottom: 12 }}>
      <Space style={{ width: "100%", justifyContent: "space-between", marginBottom: 4 }}>
        <Text style={{ fontSize: 13 }}>{label ?? `${typeLabel}指令（可选）`}</Text>
        <Button type="link" size="small" icon={<FilterOutlined />} disabled={disabled}
          onClick={() => setFiltersOpen((open) => !open)} style={{ height: "auto", padding: 0, fontSize: 12 }}>
          匹配条件{activeMatchCount > 0 ? `（${activeMatchCount}）` : "（可选）"}
        </Button>
      </Space>
      {filtersOpen ? (
        <div style={{ marginBottom: 8, padding: 8, borderRadius: 6, background: "rgba(255,255,255,.035)" }}>
          <Space wrap size={[6, 6]} style={{ width: "100%" }}>
            <Select allowClear showSearch optionFilterProp="label" loading={isTaxonomyLoading}
              disabled={disabled || productId !== undefined} value={context.productId ?? undefined}
              onChange={(next?: number) => updateLocalContext({ productId: next ?? null })}
              placeholder="产品（全部）" options={taxonomy.products.map((item) => ({ value: item.id, label: item.name }))}
              style={{ minWidth: 150, flex: 1 }} />
            <Select allowClear showSearch optionFilterProp="label" loading={isTaxonomyLoading}
              disabled={disabled || sceneId !== undefined} value={context.sceneId ?? undefined}
              onChange={(next?: number) => updateLocalContext({ sceneId: next ?? null })}
              placeholder="内容场景分类（全部）" options={taxonomy.scenes.map((item) => ({ value: item.id, label: item.name }))}
              style={{ minWidth: 180, flex: 1 }} />
            <Input allowClear disabled={disabled || scene !== undefined} value={context.scene}
              onChange={(event) => updateLocalContext({ scene: event.target.value })}
              placeholder="匹配关键词，如：宿舍好物" style={{ minWidth: 190, flex: 1 }} />
          </Space>
          <Text type="secondary" style={{ display: "block", marginTop: 5, fontSize: 11 }}>
            仅用于推荐和排序，不会写入生成内容。
          </Text>
        </div>
      ) : null}
      <Space.Compact style={{ width: "100%" }}>
        <Select style={{ width: "100%" }}
          placeholder={isLoading ? "正在匹配指令…"
            : sortedTemplates.length === 0 ? `没有符合条件的「${typeLabel}」指令，可调整匹配条件或到指令中心新增`
              : "选择指令；匹配度高的排在前面"}
          loading={isLoading} disabled={disabled} value={value.promptId ?? undefined}
          onChange={(id: number | undefined) => {
            if (id === undefined) { onChange(EMPTY_PROMPT_PICK); return; }
            const hit = templates.find((item) => item.id === id);
            onChange(hit ? { content: hit.content, promptId: hit.id, version: hit.current_version } : EMPTY_PROMPT_PICK);
          }}
          allowClear showSearch
          filterOption={(input, option) => String(option?.searchText ?? "").toLowerCase().includes(input.toLowerCase())}
          options={sortedTemplates.map((item) => {
            const reasons = matchReasons(item, { ...context, accountId }, defaultPromptId);
            return {
              value: item.id,
              searchText: `${item.name} ${TYPE_LABELS[item.prompt_type] ?? item.prompt_type} ${reasons.join(" ")}`,
              label: <Space size={4} wrap>
                <span>{favorites.includes(item.id) ? "★ " : ""}{item.name}（v{item.current_version}）</span>
                {promptType === "auto_ops" ? <Tag>{TYPE_LABELS[item.prompt_type] ?? item.prompt_type}</Tag> : null}
                {reasons.map((reason) => <Tag key={reason}
                  color={reason === "默认指令" ? "gold" : reason === "全局通用" ? undefined : "blue"}>{reason}</Tag>)}
              </Space>,
            };
          })} />
        {selected ? <>
          <Tooltip title={favorites.includes(selected.id) ? "取消收藏" : "收藏"}>
            <Button disabled={disabled} icon={favorites.includes(selected.id)
              ? <StarFilled style={{ color: "#faad14" }} /> : <StarOutlined />}
              onClick={() => setFavorites(toggleFavorite(selected.id))} />
          </Tooltip>
          <Tooltip title={isDefault ? "取消默认" : "设为默认，下次自动选中"}>
            <Button disabled={disabled} onClick={() => {
              if (isDefault) { clearDefaultPrompt(module, promptType); setDefaultPromptId(null); }
              else { saveDefaultPrompt(module, promptType, selected.id); setDefaultPromptId(selected.id); }
            }}>{isDefault ? "默认✓" : "设默认"}</Button>
          </Tooltip>
        </> : null}
      </Space.Compact>
      {selected ? <>
        <Space size={[4, 4]} wrap style={{ marginTop: 5 }}>
          {matchReasons(selected, { ...context, accountId }, defaultPromptId).map((reason) => <Tag key={reason}
            color={reason === "默认指令" ? "gold" : reason === "全局通用" ? undefined : "blue"}>{reason}</Tag>)}
        </Space>
        <Text type="secondary" style={{ display: "block", marginTop: 4, fontSize: 12, whiteSpace: "pre-wrap" }}>
          将按此指令执行：{selected.content}
        </Text>
      </> : null}
    </div>
  );
}
