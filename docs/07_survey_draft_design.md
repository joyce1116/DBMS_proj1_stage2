# 第一阶段：问卷草稿与题目配置设计记录

接续第一个里程碑。本轮新增问卷草稿、本人问卷列表/详情、元数据修改、题目添加/替换/删除/排序以及题目**配置**合法性校验。未实现发布、关闭、填写链接、实际填写、回答校验、跳转、提交、统计或前端页面；第二阶段没有开展。

## 保留和扩展了什么

原有 `users`、`sessions` 的 schema、密码处理、登录 token 和认证接口不变。新路由直接复用 `get_current_user`，没有另做账号系统。初始化只新增 `surveys` 集合和其索引，不清库，不改已有用户/会话的验证器。

新增 `survey_routes.py`、`survey_service.py`、`survey_repository.py` 延续现有路由/业务/数据分层；`survey_models.py` 负责请求响应及跨字段规则，`survey_schema.py` 负责 MongoDB 文档基本约束。应用版本更新为 0.2.0。初始化可重复运行，之后仍在本工程基础上开发。

## MongoDB 文档

| 字段 | BSON 类型 | 说明 |
| --- | --- | --- |
| `_id` | ObjectId | 问卷稳定标识，API 输出为 `id` 字符串 |
| `owner_id` | ObjectId | 服务端从当前登录用户取得，不接受请求伪造 |
| `title`、`description` | string | 标题 1–200 字符、去首尾空白；说明最多 5000 字符 |
| `allow_anonymous`、`allow_repeat` | bool | 默认 false；本轮保存设置，实际填写行为尚未执行 |
| `deadline` | date 或 null | 请求须有时区且在未来，入库转换 UTC/毫秒；null 表示未设置 |
| `status` | string | 当前仅 `draft` |
| `revision` | int | 初始 1，每次成功修改加 1；用于检测编辑冲突 |
| `schema_version` | int | 当前为 1，表示结构版本；与 revision 的用途不同 |
| `questions` | array | 最多 100 道，数组顺序就是题序 |
| `created_at`、`updated_at` | date | UTC/毫秒 |

索引 `ix_surveys_owner_created`：`owner_id:1, created_at:-1, _id:-1`，支持按创建者筛选及稳定的新到旧列表。此集合无需 TTL；不能因为截止就删除问卷。

题目、选项和约束通常随一份问卷一起读取/修改，数量在当前设计内有上限，因此嵌入同一文档。编辑整份题目数组可以用单文档原子更新，避免草稿拆成多个集合后产生半更新。回答未来将按实际需求单独设计，不能不断追加到问卷中形成无限数组。本轮没有提前创建回答集合。

每题具有服务端生成的 `q_<32位十六进制>` ID；选项具有 `opt_<32位十六进制>` ID。改题干、选项文字或题序时可以保留 ID；删除的 ID 不复用。后续跳转、回答和统计应引用这些 ID，避免拿数组下标或显示文字当身份。

MongoDB validator 检查必需字段、BSON 类型、题型各自结构、基本长度和范围、禁止多余字段。跨字段上下界、标签重复、整数区间等由应用校验；MongoDB schema 并未声称可以完成所有业务逻辑，也不提供外键。绕过应用直接写库不受完整业务规则保障。

## 三类题目与四个 API 变体

原文三类为单选、多选、填空。填空在 API 中拆为文本和数字，因此 type 有四种，未额外发明新的作业题型。所有题目都有 `title`（去首尾空白后 1–500 字符）和 `required`（默认 false）。请求字段严格检查类型，禁止多余字段。

| type | 专有字段与配置规则 |
| --- | --- |
| `single_choice` | `options` 2–50 项，每项 label 去首尾空白后 1–200 字符 |
| `multiple_choice` | 同上；`min_selections` 默认 0；`max_selections` 默认 null，代表选项数；满足 0 ≤ min ≤ 有效 max ≤ 选项数。min=max 可表达恰好选几个；必答题的有效 max 至少为 1，避免无法回答 |
| `text` | `min_length` 默认 0；`max_length` 默认 1000、范围 1–10000；min ≤ max |
| `number` | `min_value`、`max_value` 默认 null，给出时 min ≤ max；`integer_only` 默认 false，若 true 则区间内至少有一个整数 |

选择题的标签在 NFKC + casefold 后不能重复；客户端给出的选项 ID 也不能重复。创建题目时不允许指定选项 ID，服务端统一生成；替换题目时只允许带该题已有的选项 ID，未带 ID 的选项视作新增。题目 ID 在替换时保持不变，路径中传入题目 ID，请求体不另传顶层 id。

数字边界只接受有限 int/float，拒绝 bool、字符串数值、NaN、Infinity 和绝对值大于 10^15 的边界，避免 BSON 溢出和无意义数值。整数题不能配置为 0.1–0.9，因为该区间没有整数。文本长度当前按 Python Unicode 码点计数，不等于字节数或所有用户感知字符数。

以上选项下限、数量上限、长度和数值范围都是本轮工程约定，原文未逐条规定。必答、多选下限等目前仅是配置；“跳过题是否必答”“非必答未作答时如何处理下限”等需在填写模块明确后实现，不能把配置测试当作回答校验通过。

## API 与状态码

以下均要求 `Authorization: Bearer <token>`。未登录 401；非本人、问卷不存在或格式错误的问卷 ID 都返回 404。正文非法配置返回 422；过时版本返回 409。成功响应不含 MongoDB 内部 `_id` 或 schema_version。

| 方法及路径 | 输入 | 成功结果 |
| --- | --- | --- |
| `POST /api/surveys` | title，及可选 description/allow_anonymous/allow_repeat/deadline | 201，完整问卷，revision=1，空题目列表 |
| `GET /api/surveys?offset=0&limit=20` | offset 0–2147483647，limit 1–100 | 200，items/total/offset/limit；items 仅含摘要及 question_count，不含 questions |
| `GET /api/surveys/{id}` | 问卷 ID | 200，完整问卷 |
| `PATCH /api/surveys/{id}` | expected_revision + 至少一个元数据字段 | 200，更新后的完整问卷；可用 deadline:null 清除截止配置，其他字段不接受 null |
| `POST /api/surveys/{id}/questions?expected_revision=n` | 一道题的完整配置 | 201，更新后的完整问卷及生成的题目/选项 ID |
| `PUT /api/surveys/{id}/questions/{qid}?expected_revision=n` | 一道题的完整新配置 | 200，保留 qid，返回完整问卷 |
| `DELETE /api/surveys/{id}/questions/{qid}?expected_revision=n` | 无正文 | 200，删除后的完整问卷 |
| `PUT /api/surveys/{id}/question-order?expected_revision=n` | `{"question_ids":[...]}` | 200，重新排序后的完整问卷 |

排序必须包含当前全部题目且各一次；不能混入其他题目的 ID，也不能靠省略 ID 删除题目。成功修改都递增 revision，即使新旧元数据值相同。

新增/修改/删除/排序题目返回完整问卷，调用者据此取下一次 expected_revision 和 ID。问卷元数据 PATCH 不是整个文档替换，不能接受 owner_id、questions、status、revision 等越权字段。

## 权限与并发

每次读取都用 `{_id, owner_id}` 查库；列表也只按当前用户 owner_id 查询。修改先获取本人草稿与当前 revision，然后原子更新条件仍同时包含 `{_id, owner_id, status:'draft', revision:expected_revision}`，并 `$inc: {revision:1}`。不会只在读时检查权限、写时按一个 ID 无条件覆盖。

两个请求携带同一版本时，最多一个成功，另一个返回 409。客户端应重新读取并决定如何合并，不能自动重试覆盖。当前列表的 total 与 items 是两次查询，不承诺多人同时创建时有事务快照一致性。

## 后续接口的边界

截止时间、匿名和重复填写设置已经可以保存，但当前不接受回答，因此没有声称完成这些策略。草稿还不能发布或对外填写，只有创建者可读。后续需要继续定义发布后的编辑规则、历史回答如何关联题目版本、分享标识以及跳转和提交规则，再在当前模型上扩展。
