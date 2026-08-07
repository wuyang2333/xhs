import axios from "axios";

import type { AssetFolder } from "../../types";

/** 文件夹树节点：在 AssetFolder 之上补出子节点与完整路径。 */
export type FolderNode = {
  folder: AssetFolder;
  children: FolderNode[];
  /** 形如「坐浴盆/卫生间场景」，用于展示与路径上传。 */
  path: string;
  depth: number;
};

/** 取 HTTP 状态码；非 axios 错误或无响应时返回 undefined。 */
export function errorStatus(error: unknown): number | undefined {
  return axios.isAxiosError(error) ? error.response?.status : undefined;
}

/** 后端返回的 detail 文本，用于兜底提示。 */
export function errorDetail(error: unknown): string | undefined {
  if (!axios.isAxiosError(error)) return undefined;
  const detail = (error.response?.data as { detail?: unknown } | undefined)?.detail;
  return typeof detail === "string" ? detail : undefined;
}

/**
 * 把扁平的文件夹列表按 parent_id 组装成树。
 * 父节点缺失（被删或跨用户）的孤儿节点提升到根，避免整棵子树消失。
 */
export function buildFolderTree(folders: AssetFolder[]): FolderNode[] {
  const nodes = new Map<number, FolderNode>();
  for (const folder of folders) {
    nodes.set(folder.id, { folder, children: [], path: folder.name, depth: 0 });
  }

  const roots: FolderNode[] = [];
  for (const node of nodes.values()) {
    const parentId = node.folder.parent_id;
    const parent = parentId === null ? undefined : nodes.get(parentId);
    if (parent) {
      parent.children.push(node);
    } else {
      roots.push(node);
    }
  }

  const applyPath = (node: FolderNode, prefix: string, depth: number): void => {
    node.path = prefix ? `${prefix}/${node.folder.name}` : node.folder.name;
    node.depth = depth;
    node.children.sort((a, b) => a.folder.name.localeCompare(b.folder.name, "zh-Hans-CN"));
    for (const child of node.children) applyPath(child, node.path, depth + 1);
  };

  roots.sort((a, b) => a.folder.name.localeCompare(b.folder.name, "zh-Hans-CN"));
  for (const root of roots) applyPath(root, "", 0);
  return roots;
}

/** 深度优先展开成列表，顺序即树的显示顺序。 */
export function flattenFolderTree(roots: FolderNode[]): FolderNode[] {
  const result: FolderNode[] = [];
  const walk = (node: FolderNode): void => {
    result.push(node);
    for (const child of node.children) walk(child);
  };
  for (const root of roots) walk(root);
  return result;
}

/** 某文件夹自身 + 所有后代的 id，用于「移动到」时排除非法目标。 */
export function subtreeIds(node: FolderNode): number[] {
  const ids: number[] = [];
  const walk = (current: FolderNode): void => {
    ids.push(current.folder.id);
    for (const child of current.children) walk(child);
  };
  walk(node);
  return ids;
}

/** TreeSelect 用的文件夹选项，value 是 folder id。 */
export type FolderSelectOption = {
  value: number;
  title: string;
  children: FolderSelectOption[];
};

/** 组装 TreeSelect 选项；excluded 里的 id 连同其子树一并剔除（移动时排除自身子树）。 */
export function buildFolderSelectOptions(
  roots: FolderNode[],
  excluded: Set<number> = new Set(),
): FolderSelectOption[] {
  const toOption = (node: FolderNode): FolderSelectOption | null => {
    if (excluded.has(node.folder.id)) return null;
    const children = node.children
      .map(toOption)
      .filter((item): item is FolderSelectOption => item !== null);
    return { value: node.folder.id, title: node.folder.name, children };
  };
  return roots.map(toOption).filter((item): item is FolderSelectOption => item !== null);
}

export function formatFileSize(bytes: number): string {
  if (bytes <= 0) return "未知大小";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

/** 素材缩略图地址：优先 url，回退 local_path。 */
export function assetThumbUrl(asset: { url: string; local_path: string }): string {
  return asset.url || asset.local_path;
}

/**
 * 把「当前可见分页内的新顺序」投影回全量 id 列表。
 *
 * 后端 PUT /assets/reorder 要求提交该用户**全部**素材的完整集合，少一个就 400。
 * 而界面上拖拽只发生在当前筛选＋分页的子集里，所以做槽位替换：
 * 可见素材在全量顺序中占据的那些下标保持不动，只把落在这些下标上的 id 换成拖后的顺序。
 * 未参与拖拽的素材位置分毫不动。
 *
 * 任一前置条件不成立（数量不匹配、有 id 不在全量列表里）时返回 null，调用方应放弃提交。
 */
export function projectReorderOntoGlobal(
  globalIds: number[],
  visibleIdsBefore: number[],
  visibleIdsAfter: number[],
): number[] | null {
  if (visibleIdsBefore.length !== visibleIdsAfter.length) return null;
  const before = new Set(visibleIdsBefore);
  if (before.size !== visibleIdsBefore.length) return null;
  if (new Set(visibleIdsAfter).size !== visibleIdsAfter.length) return null;
  for (const id of visibleIdsAfter) {
    if (!before.has(id)) return null;
  }

  const slots: number[] = [];
  globalIds.forEach((id, index) => {
    if (before.has(id)) slots.push(index);
  });
  if (slots.length !== visibleIdsBefore.length) return null;

  const result = [...globalIds];
  slots.forEach((slot, index) => {
    const replacement = visibleIdsAfter[index];
    if (replacement !== undefined) result[slot] = replacement;
  });
  return result;
}

export function isRenderableImageSrc(value: string): boolean {
  return (
    value.startsWith("http://") ||
    value.startsWith("https://") ||
    value.startsWith("data:image/") ||
    value.startsWith("/api/")
  );
}
