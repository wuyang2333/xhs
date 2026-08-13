/**
 * 内联指令选择器——直接放在 AI 表单里，不用弹窗。
 *
 * 设计取向：用户要「生图或生文的时候能直接选择指令库」，所以选择器必须在表单上
 * 一眼可见。此前做成点「生成」后才弹的弹窗，用户两次反馈「没看到」；而且当输入框
 * 为空时点生成会被前置校验拦下，弹窗压根不出现——想从库里选反而必须先手打一段。
 *
 * 选中一条即**完全按它执行**，不与任何默认人设叠加（由后端 system_prompt_override 保证）。
 */
import { StarFilled, StarOutlined } from "@ant-design/icons";
import { Button, Select, Space, Tooltip, Typography } from "antd";
import { useEffect, useMemo, useState } from "react";

import { fetchPromptTemplates } from "../../lib/api";
import type { PromptTemplateItem } from "../../lib/api";
import {
  clearDefaultPrompt, readDefaultPrompt, readFavorites, saveDefaultPrompt, toggleFavorite,
} from "./prompt-store";
import type { PromptModule } from "./prompt-store";

const { Text } = Typography;

const TYPE_LABELS: Record<string, string> = {
  image: "生图", note: "生文", title: "标题", rewrite: "改写", auto_ops: "自动运营",
};

export type PromptPickerValue = {
  /** 选中指令的正文；未选则为空串 */
  content: string;
  promptId: number | null;
  version: number | null;
};

export const EMPTY_PROMPT_PICK: PromptPickerValue = { content: "", promptId: null, version: null };

export function PromptPicker(props: {
  promptType: string;
  module: PromptModule;
  accountId?: number;
  value: PromptPickerValue;
  onChange: (value: PromptPickerValue) => void;
  /** 标签文字，默认按类型自动生成 */
  label?: string;
  disabled?: boolean;
}) {
  const { promptType, module, accountId, value, onChange, label, disabled } = props;
  const [templates, setTemplates] = useState<PromptTemplateItem[]>([]);
  const [favorites, setFavorites] = useState<number[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const typeLabel = TYPE_LABELS[promptType] ?? promptType;

  useEffect(() => {
    setFavorites(readFavorites());
    setIsLoading(true);
    void (async () => {
      try {
        // auto_ops 不按类型筛——用户要求指令库里的所有指令都能选
        const params: Parameters<typeof fetchPromptTemplates>[0] = { status: "active", account_id: accountId };
        if (promptType !== "auto_ops") {
          params.prompt_type = promptType;
        }
        const { items } = await fetchPromptTemplates(params);
        setTemplates(items);
        // 有默认指令时自动选上，省掉每次重选
        const preferred = readDefaultPrompt(module, promptType);
        if (preferred !== null && value.promptId === null) {
          const hit = items.find((item) => item.id === preferred);
          if (hit) onChange({ content: hit.content, promptId: hit.id, version: hit.current_version });
        }
      } catch {
        setTemplates([]);
      } finally {
        setIsLoading(false);
      }
    })();
    // value/onChange 不入依赖：只在类型或账号变化时重新拉取，避免选中后立即重置
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [promptType, module, accountId]);

  const selected = useMemo(
    () => templates.find((item) => item.id === value.promptId) ?? null,
    [templates, value.promptId],
  );

  const isDefault = selected !== null && readDefaultPrompt(module, promptType) === selected.id;

  return (
    <div style={{ marginBottom: 12 }}>
      <Text style={{ display: "block", marginBottom: 4, fontSize: 13 }}>
        {label ?? `${typeLabel}指令（可选）`}
      </Text>
      <Space.Compact style={{ width: "100%" }}>
        <Select
          style={{ width: "100%" }}
          placeholder={
            isLoading ? "读取中…"
              : templates.length === 0 ? `指令库还没有「${typeLabel}」类型的指令，可到指令中心新增`
                : "选一条指令，将完全按它执行；不选则用下方内容"
          }
          loading={isLoading}
          disabled={disabled}
          value={value.promptId ?? undefined}
          onChange={(id: number | undefined) => {
            if (id === undefined) {
              onChange(EMPTY_PROMPT_PICK);
              return;
            }
            const hit = templates.find((item) => item.id === id);
            onChange(hit
              ? { content: hit.content, promptId: hit.id, version: hit.current_version }
              : EMPTY_PROMPT_PICK);
          }}
          allowClear
          showSearch
          optionFilterProp="label"
          options={templates.map((item) => ({
            value: item.id,
            label: `${favorites.includes(item.id) ? "★ " : ""}${item.name}（v${item.current_version}${item.is_official ? "·官方" : ""}）`,
          }))}
        />
        {selected ? (
          <>
            <Tooltip title={favorites.includes(selected.id) ? "取消收藏" : "收藏"}>
              <Button
                disabled={disabled}
                icon={favorites.includes(selected.id)
                  ? <StarFilled style={{ color: "#faad14" }} />
                  : <StarOutlined />}
                onClick={() => setFavorites(toggleFavorite(selected.id))}
              />
            </Tooltip>
            <Tooltip title={isDefault ? "取消默认" : "设为默认，下次自动选中"}>
              <Button
                disabled={disabled}
                onClick={() => {
                  if (isDefault) clearDefaultPrompt(module, promptType);
                  else saveDefaultPrompt(module, promptType, selected.id);
                  setFavorites(readFavorites());
                }}
              >
                {isDefault ? "默认✓" : "设默认"}
              </Button>
            </Tooltip>
          </>
        ) : null}
      </Space.Compact>
      {selected ? (
        <Text
          type="secondary"
          style={{ display: "block", marginTop: 4, fontSize: 12, whiteSpace: "pre-wrap" }}
        >
          将按此指令执行：{selected.content}
        </Text>
      ) : null}
    </div>
  );
}
