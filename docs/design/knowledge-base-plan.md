# 知识库 MVP 执行方案

设计见 [knowledge-base.md](knowledge-base.md)。

## MVP 范围（分支 `kb-mvp`）

| 部分 | 文件 | 状态 |
|---|---|---|
| 开关 `MNEMO_KB` / `mnemo node --kb` | `mnemo/kb.py`、`mnemo/cli.py` | 已实现 |
| 卡片解析、状态推算、校验 | `mnemo/kb.py` | 已实现 |
| `kb` source，卡片进入 `messages` | `mnemo/sources/kb.py` | 已实现 |
| 搜索时卡片优先（本机查询 + RRF 合并后） | `mnemo/search.py`、`mnemo/remote.py` | 已实现 |
| CLI、MCP、dashboard 默认 kinds 加入 `knowledge` | 三处入口 | 已实现 |
| `mnemo digest pending/show/done`、`mnemo kb ls [--stale]` | `mnemo/cli.py` | 已实现 |
| 提炼流程 | `integrations/skills/mnemo/distill.md` | 已实现 |
| 测试 | `tests/test_kb.py`（14 个） | 通过 |

## 与设计的差异

- 没有 `kb_cards` 表，schema 仍为 v4。卡片头部在返回结果时从卡片文件读取，开关关闭时索引完全不变。
- `include_superseded` 未实现，superseded 卡片总是被过滤。
- 多个 `cwd:` 作用域只用第一个参与 `cwd` 过滤。
- `digest` 只处理本机会话；远程设备的会话需要在那台设备上跑。
- 来源引用里的 `host` 是 `node.json` 的设备名，`get_context` 的 `host` 参数是本机注册的远程名。两者不一致时 agent 需要自己对应。

## 本机试跑（2026-11-26）

- 范围：`digest pending --since 14d` 列出的 6 个会话（codex 3、agy 2、claude 1），由一个子 agent 只按 `distill.md` 提炼。
- 结果：8 张卡片，`kb ls` 全部合法；2 个会话判定为无可提炼内容（一次性清理、读书问答）。卡片里没有密钥，命令用 `$KEY` 占位。
- 搜索：「NAS 地址」「WebRTC 规则」开启时卡片排第 1；关闭时结果与改动前一致。
- 试跑中的调整：素材从“全部工具调用取最后 40 组 + 全会话最后一条回答”改为按轮次组织（584 行的会话原来丢掉 101 组调用和前 4 个话题的结论）；每轮加 `ts`；`distill.md` 补充引用行号、`--cards` 格式、快照日期、凭据位置的写法。
- 未覆盖：知识库为空，更新、取代、`related` 的路径没有走到。

## 第二轮试跑（2026-11-26）

- 范围：4 个和已有卡片有交集的会话（codex 网关配置、codex ssh Mac mini、codex「ping」1684 行、当前 claude 会话 961 行），在 8 张卡片上提炼。
- 结果：卡片 8 → 19 张；新增 11 张，更新 5 张（追加来源），取代 0 张，`kb ls` 全部合法。git diff 检查没有密钥。
- 网关会话：较早的会话与已有卡片不矛盾（它查的是另一个端口，用途不同），新建了一张卡片。取代路径仍未走到。
- 发现的问题和调整：
  - `verified` 被设成提炼当天，证据却是 11-12 的。改为“证实它的轮次的日期，只往后推”，更早的会话与卡片矛盾时不改卡片。
  - 更新时正文末尾追加“复核”记录。改为正文只写当前结论，历史由 `sources` 保留。
  - `related` 每次列出整台设备的全部卡片，没有参考价值。改为按关键词重合度排序（见设计文档“提炼素材”）。
  - 多词查询按 AND 匹配，「iCloud 删除 NAS 备份 校验」零命中。`distill.md` 改为用一两个有区分度的词搜索。
  - 长会话素材约 48 KB（约 1.5 万 token），55 轮里有很多“ping”、空回答之类的小轮次。

## 待办
- 修正两轮试跑卡片的 `verified`（目前都是 2026-11-26），或清空知识库按新规则重跑。
- 构造一个晚于卡片的矛盾会话，验证取代路径。
- 长会话素材：合并或跳过没有工具调用、没有回答的小轮次。
- 第二轮试跑：在已有卡片上再提炼一批会话，检验更新、取代和 `related`。
- Codex 的 `exec()` / `js()` 工具调用在索引里没有参数，素材看不到命令（codex adapter 的问题）。
- 回答里的 `<oai-mem-citation>`、`:codex-file-citation{…}` 标记进入了素材。

- 根据本机试跑结果调整 `distill.md` 和素材截断参数。
- 确认效果后：加 `kb_cards`（schema v5）、`include_superseded`、MCP 的 `digest` 工具。
- 提炼的触发方式：手动、`agent-patrol` 每晚复盘，或 SessionEnd hook。
- README、`docs/cli.md`、`docs/agent-tools.md` 在开关默认打开前补充。
