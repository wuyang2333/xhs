/**
 * 页面接入指令选择器的统一入口。
 *
 * 用法：`const composer = usePromptComposer();`
 * 执行前先 `const picked = await composer.request({...})`，
 * 用户取消返回 null；确认后拿到的 `text` 就是要发给模型的完整指令。
 * 弹窗节点由 `composer.node` 挂到页面上。
 */
import { useCallback, useRef, useState } from "react";

import { recordPromptUsage } from "../../lib/api";
import { PromptComposerModal } from "./prompt-composer-modal";
import type { PromptComposerRequest, PromptComposerResult } from "./prompt-composer-modal";
import { pushRecents } from "./prompt-store";
import type { PromptModule } from "./prompt-store";

export function usePromptComposer(): {
  node: React.ReactElement;
  request: (payload: PromptComposerRequest) => Promise<PromptComposerResult | null>;
  recordUsages: (
    usages: Array<{ promptId: number; version: number }>,
    context: {
      module: PromptModule; promptType?: string; accountId?: number;
      targetType?: string; targetId?: number | null;
    },
  ) => Promise<void>;
} {
  const [request, setRequest] = useState<PromptComposerRequest | null>(null);
  const resolverRef = useRef<((value: PromptComposerResult | null) => void) | null>(null);

  const settle = useCallback((value: PromptComposerResult | null) => {
    const resolve = resolverRef.current;
    resolverRef.current = null;
    setRequest(null);
    if (resolve) resolve(value);
  }, []);

  const open = useCallback((payload: PromptComposerRequest) => {
    // 上一次还没结束就再开，先把旧的当取消结掉，避免 promise 悬着。
    if (resolverRef.current) settle(null);
    setRequest(payload);
    return new Promise<PromptComposerResult | null>((resolve) => { resolverRef.current = resolve; });
  }, [settle]);

  const recordUsages = useCallback(async (
    usages: Array<{ promptId: number; version: number }>,
    context: {
      module: PromptModule; promptType?: string; accountId?: number;
      targetType?: string; targetId?: number | null;
    },
  ) => {
    if (usages.length === 0) return;
    pushRecents(context.module, context.promptType ?? "", usages.map((usage) => usage.promptId));
    await Promise.all(usages.map((usage) => recordPromptUsage({
      prompt_template_id: usage.promptId,
      platform_account_id: context.accountId ?? null,
      module: context.module,
      target_type: context.targetType ?? "",
      target_id: context.targetId ?? null,
      // 后端按 current_version 落库；这里同时留下合成时的版本，便于比对。
      outcome: { composed_version: usage.version },
    }).catch(() => undefined)));
  }, []);

  const node = (
    <PromptComposerModal
      open={request !== null}
      request={request}
      onCancel={() => settle(null)}
      onConfirm={(result) => settle(result)}
    />
  );

  return { node, request: open, recordUsages };
}
