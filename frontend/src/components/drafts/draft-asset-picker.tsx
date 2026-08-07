import { useCallback, useMemo } from "react";

import type { DraftAsset, MediaAsset } from "../../types";
import { AssetLibraryPicker } from "../assets/asset-library-picker";

export type DraftAssetPickerProps = {
  open: boolean;
  /** 草稿现有素材，用于在界面层面禁用已加入的项。 */
  draftAssets: DraftAsset[];
  confirming: boolean;
  onClose: () => void;
  onConfirm: (assets: MediaAsset[]) => void;
};

/**
 * 草稿工坊选图器。筛选与选择逻辑复用 `AssetLibraryPicker`，
 * 这里只负责「哪些素材已在当前草稿里」这一层判断。
 */
export function DraftAssetPicker({
  open,
  draftAssets,
  confirming,
  onClose,
  onConfirm,
}: DraftAssetPickerProps) {
  /**
   * 已在草稿里的素材必须在界面层面就禁用。
   * 后端 POST /drafts/{id}/assets 除 media_asset_id 外，还会按 url / local_path 判重并返回 409，
   * 所以三者都要纳入判断，否则用户仍会撞到错误。
   */
  const takenKeys = useMemo(() => {
    const mediaIds = new Set<number>();
    const urls = new Set<string>();
    const paths = new Set<string>();
    for (const asset of draftAssets) {
      if (asset.media_asset_id !== null) mediaIds.add(asset.media_asset_id);
      if (asset.url) urls.add(asset.url);
      if (asset.local_path) paths.add(asset.local_path);
    }
    return { mediaIds, urls, paths };
  }, [draftAssets]);

  const isTaken = useCallback(
    (asset: MediaAsset): boolean =>
      takenKeys.mediaIds.has(asset.id) ||
      (asset.url !== "" && takenKeys.urls.has(asset.url)) ||
      (asset.local_path !== "" && takenKeys.paths.has(asset.local_path)),
    [takenKeys],
  );

  return (
    <AssetLibraryPicker
      open={open}
      confirming={confirming}
      onClose={onClose}
      onConfirm={onConfirm}
      title="从素材库选图"
      confirmLabel="加入草稿"
      isTaken={isTaken}
      takenHint="已在当前草稿中"
      takenBadge="已加入"
    />
  );
}
