/**
 * 指令使用记录的统一入口。
 *
 * PromptPicker 组件负责选指令，这里只负责记录使用（调后端 API + 更新最近使用列表）。
 * 此前还有一个弹窗模式（PromptComposerModal），B-03 改成内联选择器后已废弃。
 */
import { useCallback } from "react";

import { recordPromptUsage } from "../../lib/api";
import { pushRecents } from "./prompt-store";
import type { PromptModule } from "./prompt-store";

export function usePromptComposer(): {
  recordUsages: (
    usages: Array<{ promptId: number; version: number }>,
    context: {
      module: PromptModule; promptType?: string; accountId?: number;
      targetType?: string; targetId?: number | null;
    },
  ) => Promise<void>;
} {
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
      outcome: { composed_version: usage.version },
    }).catch(() => undefined)));
  }, []);

  return { recordUsages };
}
