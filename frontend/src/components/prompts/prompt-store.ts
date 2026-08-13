/**
 * 指令选择的本地偏好存储。
 *
 * 设计取向：**选一条指令就完全按它执行，不做分层叠加。**
 * 用户明确要求「必须按照我的指令库的指令生成，或者我填写都可以」——
 * 所以最终发给模型的文本只有两个来源：库里选中那条的正文，或用户自己填的。
 * 不再把基础/平台/账号/产品/场景几层拼起来（那是需求总纲第八节的原设想，已按用户意见调整）。
 *
 * 收藏 / 最近使用 / 默认指令只落在 localStorage，不占后端字段。
 */

export type PromptModule = "draft_studio" | "image_studio" | "auto_ops";

const FAVORITES_KEY = "xhs.prompt.favorites";
const RECENTS_KEY = "xhs.prompt.recents";
const DEFAULTS_KEY = "xhs.prompt.defaults";
const RECENT_LIMIT = 8;

function readJson<T>(key: string, fallback: T): T {
  try {
    const raw = window.localStorage.getItem(key);
    if (raw === null) return fallback;
    return JSON.parse(raw) as T;
  } catch {
    return fallback;
  }
}

function writeJson(key: string, value: unknown): void {
  try {
    window.localStorage.setItem(key, JSON.stringify(value));
  } catch {
    // 隐私模式下写入会抛错，本地偏好丢失不影响主流程。
  }
}

export function readFavorites(): number[] {
  const value = readJson<number[]>(FAVORITES_KEY, []);
  return Array.isArray(value) ? value.filter((id) => typeof id === "number") : [];
}

export function toggleFavorite(promptId: number): number[] {
  const current = readFavorites();
  const next = current.includes(promptId) ? current.filter((id) => id !== promptId) : [...current, promptId];
  writeJson(FAVORITES_KEY, next);
  return next;
}

/** 最近使用按「模块 + 指令类型」分开记，生图和改写互不干扰。 */
function recentsKey(module: PromptModule, promptType: string): string {
  return `${module}:${promptType}`;
}

function readAllRecents(): Record<string, number[]> {
  const value = readJson<Record<string, number[]>>(RECENTS_KEY, {});
  return value !== null && typeof value === "object" && !Array.isArray(value) ? value : {};
}

export function readRecents(module: PromptModule, promptType: string): number[] {
  const list = readAllRecents()[recentsKey(module, promptType)];
  return Array.isArray(list) ? list.filter((id) => typeof id === "number") : [];
}

export function pushRecents(module: PromptModule, promptType: string, promptIds: number[]): number[] {
  if (promptIds.length === 0) return readRecents(module, promptType);
  const map = readAllRecents();
  const key = recentsKey(module, promptType);
  const previous = Array.isArray(map[key]) ? map[key] : [];
  const next = [...promptIds, ...previous.filter((id) => !promptIds.includes(id))].slice(0, RECENT_LIMIT);
  map[key] = next;
  writeJson(RECENTS_KEY, map);
  return next;
}

/** 默认指令按「模块 + 指令类型」记一条，下次打开自动预选。 */
function defaultsKey(module: PromptModule, promptType: string): string {
  return `${module}:${promptType}`;
}

function readAllDefaults(): Record<string, number> {
  const value = readJson<Record<string, number>>(DEFAULTS_KEY, {});
  return value !== null && typeof value === "object" && !Array.isArray(value) ? value : {};
}

export function readDefaultPrompt(module: PromptModule, promptType: string): number | null {
  const value = readAllDefaults()[defaultsKey(module, promptType)];
  return typeof value === "number" ? value : null;
}

export function saveDefaultPrompt(module: PromptModule, promptType: string, promptId: number): void {
  const map = readAllDefaults();
  map[defaultsKey(module, promptType)] = promptId;
  writeJson(DEFAULTS_KEY, map);
}

export function clearDefaultPrompt(module: PromptModule, promptType: string): void {
  const map = readAllDefaults();
  delete map[defaultsKey(module, promptType)];
  writeJson(DEFAULTS_KEY, map);
}
