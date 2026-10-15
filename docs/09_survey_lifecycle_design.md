# 第一阶段里程碑 3：发布、关闭、截止与登录分享

在原账号、草稿和题目配置代码上扩展。本轮只增加生命周期和只读分享，未实现网页、填写答案、回答校验、跳转、提交、统计；第二阶段仍未开展。

## 状态与发布后的编辑

持久状态按 `draft → published → closed` 变化。草稿可编辑，至少有一道合法题目且尚未截止才可发布。发布时重验题目配置、题目与选项 ID 唯一性，防止绕过 API 写库的部分不合理配置进入发布状态。

发布后冻结题目、题序和全部元数据，包括匿名/重复设置及截止时间；只允许关闭。当前不支持重开、撤回草稿或重新发布。这是本轮为保持发布内容稳定作出的工程选择，原文未明确规定这些限制。后续填写将引用固定的问卷 ID、题目 ID 与发布版本；跳转配置应在草稿中完成，不能随意改已发布内容。

问卷 `status` 不因时间流逝而自动改写。创建者响应额外计算 `availability` 与 `is_open`：

| 持久 status | 时间条件 | availability | is_open |
| --- | --- | --- | --- |
| draft | 任意 | draft | false |
| published | 无截止时间，或当前 UTC 时间严格早于截止 | open | true |
| published | 当前 UTC 时间大于或等于截止 | expired | false |
| closed | 任意 | closed | false |

所有日期保持 UTC/毫秒。到达截止的同一时刻即视为过期；不等 TTL、定时任务或下一次修改。检查时不修改问卷、删除数据或递增 revision。已过期但持久状态仍为 published 的问卷可以手动关闭。

`is_open` 只代表生命周期处于开放状态，当前没有接受答案的接口，不能据此声称已经能提交问卷。

## 本轮新增 API

| 方法/路径 | 输入与认证 | 成功 | 主要失败 |
| --- | --- | --- | --- |
| `POST /api/surveys/{id}/publish` | 创建者 Bearer；JSON `{"expected_revision":n}` | 200，完整管理详情，status=published，revision+1 | 无题/已截止/配置非法 422；版本或状态冲突 409；非本人或未知 ID 404 |
| `POST /api/surveys/{id}/close` | 同上 | 200，status=closed，revision+1，保留分享标识和已发布内容 | 草稿关闭、重复关闭、版本冲突 409；非本人或未知 ID 404 |
| `GET /survey/{share_id}` | 任意有效登录用户的 Bearer | 200，只读 JSON，`Cache-Control: no-store` | 未登录 401；未知或格式错误 404；关闭或过期 410，不返回题目 |

状态变更请求仅接受严格整数且 ≥1 的 expected_revision，不接受客户端伪造 status/share_id/owner_id。发布、关闭和草稿编辑最终更新都匹配 `_id + owner_id + revision + 原状态`，成功原子递增版本。发布和编辑同时携带旧版本时，只有一个可以成功；发布后原草稿编辑接口继续保留，但合法的编辑请求会返回 409。

管理详情和列表新增 `share_id`、`share_url`、`published_at`、`closed_at`、`availability`、`is_open`。草稿前四项均为 null；发布后 `share_url` 是相对路径 `/survey/<share_id>`，例如本地完整地址需前缀 `http://127.0.0.1:8000`。

分享接口使用独立响应模型，仅含：

```text
id, title, description, allow_anonymous, allow_repeat,
deadline, revision, questions
```

不含 owner_id、share_id、share_url、status、schema_version、管理时间或账号信息。保留 revision 是为了后续回答引用发布版本，它不是认证凭据。分享访问不授予编辑权；接收者仍无法用管理 API 查看或修改该问卷，也不会在“我的问卷”列表中获得它。

原文要求填写者先登录，因此 `allow_anonymous=true` 也不允许免登录访问。匿名回答身份如何保存及展示仍待填写模块实现。这里返回 JSON，不是填写网页，也没有自动跳转和提交功能。

## 分享标识和索引

发布时使用 `secrets.token_urlsafe(24)` 生成 32 位随机 URL 安全标识。草稿没有分享标识。关闭后保留原标识，让已有链接稳定地返回不可用状态，而不是指向另一份问卷。

MongoDB 建立 `uq_surveys_share_id` 唯一索引，partial filter 为 `share_id` 的 BSON 类型是 string。不能对包含多个 null 的草稿直接建普通唯一索引，否则多个草稿会冲突。真实重复键错误若来自 share_id 索引，重新生成标识，最多尝试 3 次；耗尽时保留原草稿并通过既有数据库错误处理返回 503。其他索引错误不盲目重试。

surveys 没有 TTL 索引，过期与关闭都保留数据。会话原有 TTL 索引不受影响。

## 为什么需要显式 schema v2 迁移

里程碑 2 的 surveys validator 只允许 draft、schema_version=1，并禁止额外字段。直接改代码后启动，既有集合不能保存新发布信息，因此保留精确 `LEGACY_SURVEY_SCHEMA`，新增 `SURVEY_SCHEMA` v2，并使用独立脚本迁移。

v2 保留原字段和内嵌题目结构，新增必需字段：

| 字段 | 类型 | 草稿值 |
| --- | --- | --- |
| share_id | string 或 null | null |
| published_at | date 或 null | null |
| closed_at | date 或 null | null |
| schema_version | int | 2 |

validator 同时检查状态与这些字段的组合：draft 三项为 null；published 有分享标识、发布时间且关闭时间为空；closed 必须有分享标识及发布、关闭时间。题目/选项 schema 没有重写。

## 迁移流程、备份和恢复边界

迁移入口是 `python -m scripts.migrate_surveys_v2`；`--dry-run` 完全只读。本脚本是本次具体的离线迁移，不是通用迁移框架。

1. 停止所有使用该数据库的应用进程，MongoDB 保持运行。只接受精确已知 v1/v2 validator 或本迁移拥有的过渡状态；未知 schema/损坏文档在迁移写入前拒绝。
2. 在 `schema_migrations` 中建立 `_id=surveys_v1_to_v2` 的 in_progress 记录，绑定随机独立备份集合名和原文档数量。
3. 将所有 v1 原文档完整保存到该 `surveys_v1_backup_<uuid>` 集合。第一次快照不覆盖，检查原文档数量与内容一致后才能转换。
4. 将 validator 临时放宽为只接受已知 v1 或 v2。仅更新 v1 的 schema_version 和三个新增 null 字段，保留问卷/创建者/题目/选项 ID、revision、创建及更新时间和其他配置。
5. 验证所有文档均满足 v2、与原快照对应，收紧为 v2 strict/error validator，记录 completed 和数量。正常初始化再确保新唯一索引存在。

如果在备份、转换或最后 collMod 过程中中断，保留进度记录和备份，应用继续停止；重新执行同一脚本完成迁移，不清库、不删除进度或“重新生成”问卷。无对应进度记录的过渡验证器会被拒绝，不能擅自认领别的迁移。

完成后再次执行返回 already_v2，不覆盖备份，也允许已迁移草稿后来正常编辑或发布。新数据库尚无 surveys 时返回 absent，再由初始化创建 v2；原本就是 v2 的库无需虚构迁移记录。

本机 standalone MongoDB 上，collMod 和批量文档更新不是一个跨操作事务。本实现以离线前提、备份与可续跑状态保障过程，不声称支持在线迁移或自动回滚。备份应保留；恢复原应用版本需要另行审核恢复方案，不能直接运行旧代码或随意改回 validator。

本轮开发库已迁移两份原草稿，备份集合与逐字段核对结果记录在 `10_survey_lifecycle_tests.md`。用户、会话集合及认证代码未被迁移。
