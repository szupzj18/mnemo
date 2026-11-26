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
| MCP 工具 `list_undistilled_sessions`、`get_digest`、`list_cards`、`save_card`、`mark_distilled` | `mnemo/mcp_server.py`、`mnemo/kb.py` | 已实现 |
| `mnemo setup` 为 Codex、agy 链接技能 | `mnemo/setup.py` | 已实现 |
| 排除记录过提炼的会话（子 agent） | `mnemo/kb.py` | 已实现 |
| 测试 | `tests/test_kb.py`（20 个） | 通过 |

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

## 第三轮试跑（2026-11-26，按修订后的规则从空知识库重跑）

- 范围：前两轮的同一批 10 个会话，按开始时间从早到晚逐个提炼。
- 结果：23 张卡片（fact 14、gotcha 4、howto 3、decision 2），1 张被取代，`kb ls` 全部合法，没有密钥。

| 指标 | 第二轮（旧规则） | 第三轮（新规则） |
|---|---|---|
| 卡片数 | 19 | 23 |
| `verified` | 全部是 2026-11-26（提炼当天） | 10-20 到 11-26 共 9 个日期，取自证实它的轮次 |
| 正文追加“复核”记录 | 5 张 | 0 张 |
| 取代 | 0 | 1（旧会话说 SSH 要密码，后来的会话显示已能免密登录） |
| `preference` 卡片 | 1 | 0 |

- `related` 在 4 个会话里给出了需要更新的卡片，取代就是靠它发现的（得分最高的卡片和会话共享 `batchmode`、`permission denied`）。仍有杂讯：只靠 `code`、`claude` 两个常见词重合的卡片也会被列出。
- 网关会话的 `related` 为空：已有卡片和会话只共享一个关键词（网关的 `host:port` 地址），没达到两个词的下限。
- 日期：素材原来只给 UTC 的 `ts`，agent 有时换算成本地日期，有时不换算。现在每轮增加 `date`（本机日期），卡片日期统一按它填写。

## agy 与 Codex 通过 MCP 提炼（2026-11-26）

- 两边都只用 mnemo 的 MCP 工具，不执行 shell 命令，在知识库目录里以非交互方式运行，各提炼一个会话。
- agy（`agy -p`）：第一次运行时，写入工具在 headless 模式下被自动拒绝。用 `--settings` 只对本次会话放行 7 个 `mcp(mnemo/<工具名>)` 后，调用 16 次，新建 1 张卡片、更新 2 张（追加来源，`verified` 从 11-16 推到 11-17），并记了台账。只写 `mcp(mnemo)` 不起作用。
- Codex（`codex exec`）：对 docker 会话增量提炼（第 604–667 行），调用 12 次，更新 2 张卡片，没有新建，台账 `upto` 推进到 667。两个写入工具调用前都触发了本机配置的 `PermissionRequest` hook，由 hook 批准。没有这个 hook 时的行为未验证。
- 两次产出的卡片都没有凭据，`list_cards` 全部合法。
- 发现的问题：前几轮试跑的子 agent 会话出现在待提炼列表里（`cwd` 不是知识库目录），已改为排除记录过提炼的会话。

## 待办
- `preference` 卡片在第三轮没有出现：用户的纠正（“确认备份过然后删了吧”）被写成了 howto。需要在 `distill.md` 里说明偏好和操作步骤的区别。
- `related` 的常见词杂讯（`code`、`claude`）：考虑按会话语料而不是卡片语料计算词频。
- 长会话素材：合并或跳过没有工具调用、没有回答的小轮次。
- 第二轮试跑：在已有卡片上再提炼一批会话，检验更新、取代和 `related`。
- Codex 的 `exec()` / `js()` 工具调用在索引里没有参数，素材看不到命令（codex adapter 的问题）。
- 回答里的 `<oai-mem-citation>`、`:codex-file-citation{…}` 标记进入了素材。

- 根据本机试跑结果调整 `distill.md` 和素材截断参数。
- 确认效果后：加 `kb_cards`（schema v5）、`include_superseded`。
- 提炼的触发方式：手动、`agent-patrol` 每晚复盘，或 SessionEnd hook。
- README、`docs/cli.md`、`docs/agent-tools.md` 在开关默认打开前补充。
