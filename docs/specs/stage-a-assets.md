# 阶段 A：素材与草稿

> **状态：后端已完成，前端未开始**（2026-08-05）
> 参考基线：迁移 head `f41a0e9d2c73` / 145 passed（阶段 A 专项 7 passed）
> 上游：[00-总纲.md](00-总纲.md) 第六、七节 + 第十三节阶段 A

后端部分照已实现的行为记录，不是设计稿。前端部分是待做清单。本文件可随时修改。

## 范围

图片工坊主流程从「API 生图」调整为「批量上传 + 分类管理 + 草稿选图与排序」。API 生图降级为辅助功能保留。

图片顺序的唯一权威来源是草稿工坊的 `sort_order`。

### 不做什么

- 不做外部同步（Obsidian / NAS / 网盘）——首批来源未定，属总纲第十六节待决策项
- 不做多租户 `tenant_id`——属阶段 D
- 不动 API 生图现有逻辑，只是不再作为主流程入口
- 不做视频素材的文件夹管理，本阶段只覆盖图片
- 不重构 `media_asset_service.py` 的分层问题（BUG-04，收尾后再定）

## 数据模型（已实施）

迁移 `f41a0e9d2c73_add_media_asset_domain.py`，新增 6 张表：

- `asset_folders` —— 支持嵌套（`parent_id` 自引用）、封面（`cover_asset_id`）、同级同名唯一约束
- `products` —— 用户内名称唯一
- `asset_scenes` —— 用户内名称唯一
- `asset_tags` —— 用户内名称唯一，带 `color`
- `media_assets` —— 主文件夹 + 产品 + 场景 + `sort_order` + `last_used_at`
- `media_asset_tags` —— 多标签关联（一图一主文件夹、多标签）

既有表扩展：

- `draft_assets` 增 `media_asset_id`、`is_cover`；`(draft_id, media_asset_id)` 唯一防重复；部分唯一索引保证每草稿至多一个封面
- `publish_assets` 增 `sort_order`、`source_draft_asset_id`；迁移含历史数据回填

`downgrade()` 完整可回滚。

## 接口（已实施）

`backend/app/api/assets.py`，前缀 `/api/assets`，21 个端点：

- 文件夹 CRUD（`DELETE` 对非空文件夹返回冲突）
- 产品 / 场景 / 标签 各 CRUD
- 素材列表（筛选）、上传、`PATCH /batch` 批量改属性、`PUT /reorder`、单个删除

## 前端（未实施）

已完成：`types/index.ts:413-441` 五个类型定义。

待实施：

1. `api.ts` 补齐 21 个接口对应的客户端函数（当前 `AssetFolder`/`AssetScene`/`AssetTag`/`MediaAsset` 为悬空 import）
2. 图片工坊：文件夹树、批量上传到指定文件夹、批量移动、设封面、文件夹内拖拽排序、按产品/场景/标签筛选、搜索、最近上传/最近使用
3. 草稿工坊：底部选图器（按产品/场景/文件夹/标签筛选、一键全选文件夹、防重复）、已选序号、拖拽排序、设为封面、删除、替换、「保存中/已保存」状态
4. 送发布中心前必须等待顺序保存完成

## 后端待补的小缺口（2026-08-05 讨论新增）

**上传时自动建文件夹。** 现在 `POST /api/assets` 要求 `folder_id` 指向已存在的文件夹（`_optional_owned` 会校验）。用户希望上传时文件夹不存在就自动建出来，按「产品 / 场景」的层级。改动不大。

**图片顺序确认按每篇草稿独立。** 用户明确选此模型，现有 `draft_assets.sort_order` 已是如此，无需改动。已记入 [DECISIONS.md](../DECISIONS.md)。

**不做外部同步。** Obsidian / NAS / 网盘均不接，只做上传。总纲第六节提到的「外部同步」在本阶段不实施。

## 验收

后端（已验证，测试文件 `tests/backend/test_stage_a_assets.py`）：

| 验收标准 | 测试 | 状态 |
|---|---|---|
| 空库可从零建出阶段 A schema | `test_alembic_upgrade_from_empty_sqlite_creates_stage_a_schema` | ✅ |
| 素材与文件夹跨用户隔离 | `test_media_assets_and_folders_are_isolated_between_users` | ✅ |
| 非空文件夹删除返回冲突 | `test_nonempty_folder_delete_returns_conflict` | ✅ |
| 同一素材不能重复加入同一草稿 | `test_same_media_asset_cannot_be_added_to_same_draft_twice` | ✅ |
| reorder 必须是完整集合 | `test_draft_asset_reorder_validates_exact_complete_set` | ✅ |
| 设封面后草稿内恰好一个封面 | `test_setting_cover_leaves_exactly_one_cover_in_draft` | ✅ |
| 送发布保留顺序与 source id | `test_send_draft_to_publish_preserves_asset_order_and_source_ids` | ✅ |

前端（未实施）：

| 验收标准 | 测试 | 状态 |
|---|---|---|
| `npx tsc --noEmit` 退出码 0 | 门禁 | ✅ A-01 已修 |
| 图片工坊可建/改名/移动/删空文件夹，支持嵌套 | 待补 | ❌ |
| 可批量上传到指定文件夹、批量移动 | 待补 | ❌ |
| 草稿选图可按产品/场景/文件夹/标签筛选，防重复 | 待补 | ❌ |
| 已选图片显示序号，拖拽可改序 | 待补 | ❌ |
| 顺序保存完成前不可送发布中心 | 待补 | ❌ |

全链路（未实施）：

| 验收标准 | 测试 | 状态 |
|---|---|---|
| 草稿 / 发布中心 / 手机三处图片数量与顺序完全一致 | 待补 | ❌ |
| 数量或顺序不一致时中止，不得只带一张图继续发布 | 待补 | ❌ |

最后两条按总纲第七节，验证发生在真实发布路径上——测试必须走 mock，见 [CLAUDE.md 红线](../../CLAUDE.md#红线不可逆操作)。

## 顺序链路（总纲第七节）

1. 图片工坊选择素材
2. 草稿工坊拖拽排序
3. 保存 `sort_order`
4. 发布中心按相同顺序复制素材
5. 传到手机时按相同顺序编号
6. 小红书按后台顺序点击每张图右上角选择圆圈
7. 发布前验证任务图片数与手机实际选中数一致

第 4 步已由 `test_send_draft_to_publish_preserves_asset_order_and_source_ids` 覆盖。5 到 7 属阶段 C 设备助手范围。
