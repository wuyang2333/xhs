# XHS_ALL_IN_ONE 开发规则

小红书全链路运营平台。本文件是常驻规则，每个会话自动加载。项目当前状态见 [docs/STATE.md](docs/STATE.md)。

## 跑迁移必须带 DATABASE_URL

`backend/alembic.ini` 里写死的路径是 `./backend/app/storage/spider_xhs.db`，**跟应用实际使用的 `./data/spider_xhs.db` 不是同一个库**。

直接 `alembic upgrade head` 会在旁边新建一个空库并把所有迁移跑进去，真实库分毫未动——**不报错、看起来还成功了**。

正确做法：

```bash
DATABASE_URL="sqlite:///./data/spider_xhs.db" ./.venv-py312/bin/python -m alembic -c backend/alembic.ini upgrade head
```

跑完必须核对 `alembic_version` 和实际表结构，不能只看命令输出。

## 红线（不可逆操作）

1. **测试绝不触发真实发布。** 发布路径的测试一律走 mock。误发一篇真实笔记无法撤回。
2. **数据库迁移前必须备份。** `data/spider_xhs.db` 含真实数据（238 笔记 / 48 草稿 / 35 发布任务）。备份用 `data/backups/`，恢复脚本 `scripts/restore_backup.py`。
3. **迁移与发布路径不交给智能体自主执行。** 必须人工确认后由主会话执行。
4. **`apis/` 和 `xhs_utils/` 是底层 SDK，禁止直接修改。** 上层通过 `backend/app/adapters/xhs/` 中转。

## 环境

根目录有三个 venv，**只有 `.venv-py312` 能用**。`.venv` 是 Python 3.9，跑测试会崩在收集阶段（PEP 604 的 `int | None` 注解 Pydantic 在 3.9 上无法求值），那是解释器问题不是代码 bug。机器上没有裸 `python`。

```bash
./.venv-py312/bin/python -m pytest tests -q
```

## 手机 UI 自动化：长列表必须滚动查找

`uiautomator` 只导出**已渲染**的节点。所以在长列表里找东西时，只 dump 一次就判定「找不到」是错的——目标可能就在屏幕外一点。

2026-08-06 实测踩过：相册 CodexXHS 明明在下面，脚本报 `Cannot find the dedicated CodexXHS image album`。

规则：凡在可滚动列表中查找（相册、商品、账号），一律用 `scroll_to_find_node()`，不要用裸 `find_node()`。滚动坐标要从容器 bounds 算，不写死像素。

**遇到「明明看得见却说找不到」，先怀疑这个。**

## 收尾门禁

单子标记完成前，四项全过：

- [ ] `./.venv-py312/bin/python -m pytest tests -q` 全绿
- [ ] `cd frontend && npx tsc --noEmit` 退出码 0
- [ ] 无悬空 import、无重复声明、无孤儿文件
- [ ] `docs/STATE.md` 已更新

第二项是硬项。历史上 TS2440 流出过一次，起因就是收尾没跑构建。

## 开发流程

三步：

```
讨论 → 登记 → 派单
```

- **讨论**：聊清楚要做什么。产出是结论 + 还没定的问题清单，不是契约。
- **登记**：写下来。坏的东西记进 `STATE.md` 已知问题；定下来的事记进 `DECISIONS.md`。
- **派单**：拆成单子去做。一张单 = 一个会话能完成。做完前过一遍收尾门禁。

规格文件（`specs/`）随时可改，不冻结、不记版本。它的作用是记录「这个阶段要做什么、做到哪了」，不是合同。

## 派单复杂度分档

| 档 | 特征 | 执行方式 |
|---|---|---|
| S | 单文件、机械改动、结果一眼可验 | 主会话直接做 |
| M | 单层内多文件，要做什么已写清楚 | 派 sonnet |
| L | 跨层或需设计判断 | 派 opus |
| XL | 跨层且含不可逆操作 | **拆单，不派** |

XL 不是最高档，是拒绝信号。评估维度：跨几层、有无不可逆操作、要做什么是否已写清到无需临场判断。

**并行派单暂不可用**——worktree 隔离依赖 git，本项目尚未 `git init`，多智能体同时改同一目录会互相覆盖。现阶段串行。

## 派出去的单要回来核对

派给智能体的单，回来后由主会话对着单子的验收项核一遍再收。不是走流程，是因为智能体经常报告「做完了」但漏掉一两项。

## 问题分流

发现 bug 按性质分三个去处：

- **现在就修** → 建派单，`STATE.md` 已知问题挂一行索引，修完删行（历史留在派单文件里）
- **暂不修 / 需拍板** → 留在 `STATE.md`；明确接受的缺陷写进 `DECISIONS.md` 说明理由
- **暴露了缺失的规则** → 回到本文件加门禁，挡住整类问题而非单个实例

如果 bug 让某份规格的验收项从成立变成不成立，回到那份规格把对应行标 ❌。修复单收尾要带回归测试。

**纪律：做 A 单时发现 B 的 bug，不要停下来修。** 在 `STATE.md` 加一行，继续做 A。横向展开会让两件事都停在半空。

## 测试约定

测试名把不变量编进名字，沿用现有风格：

```
test_setting_cover_leaves_exactly_one_cover_in_draft
test_draft_asset_reorder_validates_exact_complete_set
test_media_assets_and_folders_are_isolated_between_users
```

前端目前零测试，最低要求是 `tsc --noEmit` 纳入门禁。

## 文档结构

```
CLAUDE.md              # 本文件，常驻规则
docs/
├── STATE.md           # 当前状态 + 索引 + 已知问题
├── DECISIONS.md       # 决策日志，追加式，倒序
├── specs/             # 各阶段要做什么、做到哪了，可随时改
└── tickets/           # 派单，扁平，一单一文件
```

规格与讨论稿不分目录，同一文件靠文件头 `状态:` 一行说明进展。规格可随时修改。
