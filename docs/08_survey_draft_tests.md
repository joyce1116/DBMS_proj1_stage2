# 第一阶段：草稿与题目配置测试实录

本文保留里程碑 2 的历史结果。当前已在其上增加生命周期及显式迁移，全量 127 项通过，见 [`10_survey_lifecycle_tests.md`](10_survey_lifecycle_tests.md)。

本轮承接原有账号工程，验证草稿问卷和题目配置。没有测试或宣称完成真实填写、回答校验、跳转、提交、统计及页面。

## 执行环境和结论

沿用 Windows PowerShell、Python 3.13.9、MongoDB 6.0.29、FastAPI 0.143.1、PyMongo 4.19.0 及原 `requirements.txt`，没有新增依赖。MongoDB 监听 `127.0.0.1:27018`，数据目录为 `.runtime/mongo-data`。

接入 surveys 后，先执行原有账号测试：

```powershell
.\.venv\Scripts\python.exe -X utf8 -m scripts.init_db
.\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_security.py tests/test_auth_integration.py -q
```

实际：初始化输出 `(users, sessions, surveys)`；**37 passed, 1 warning in 7.18s**。原账号/会话验证器及测试文件未更换，也没有清库。

本轮最终全量命令：

```powershell
.\.venv\Scripts\python.exe -X utf8 -m pytest -q
.\.venv\Scripts\python.exe -m pip check
```

实际结果：

```text
........................................................................ [ 86%]
...........                                                              [100%]
83 passed, 1 warning in 13.47s
No broken requirements found.
```

总计 83 项：原有 37 项 + 新增 46 项。按执行方式为 36 项离线校验 + 47 项真实 MongoDB 集成测试。全部执行，没有跳过；warning 仍是原有 Starlette TestClient 将弃用 HTTPX 的提示，本轮没有新增告警，也未屏蔽它。

新增文件：`tests/test_survey_validation.py`（28 项）和 `tests/test_surveys_integration.py`（18 项）。参数化和同一用例内的多个断言按 pytest 的用例数统计，不把每个断言再重复计数。

## 自动化测试步骤与输入/输出

集成测试沿用 `tests/conftest.py`：每例新建随机 `survey_test_<uuid>` 库，使用实际应用启动、实际 HTTP 处理和真实 MongoDB，结束时仅删除该例创建的库。测试不需要另开 Uvicorn，但必须有 MongoDB；连接失败为 fail，不会 skip。离线配置测试不连数据库。

| 场景 | 输入与操作 | 预期 | 实际结果 |
| --- | --- | --- | --- |
| 新建草稿和持久化 | 注册登录，创建带首尾空格标题及 +08:00 未来截止时间的问卷，读取数据库和详情 | 201；标题 strip；owner_id 为当前用户 ObjectId；空题目、draft、revision=1；UTC/毫秒持久化；读回一致 | 通过 |
| 本人列表及分页 | Alice 建 3 份、Bob 建 1 份；设同一创建时间验证 ID 排序，查询 limit/offset | Alice 只见自己的 3 份，total 正确，列表不含题目明细，排序稳定；非法参数 422 | 通过；另断言 2147483648 及超 64 位 offset 均 422 |
| 元数据修改 | 修改标题、说明、匿名和重复设置及截止时间，再设 deadline:null | 200；revision 从 1→2→3；创建时间不变；可清除 deadline | 通过；仅验证配置保存，未执行填写策略 |
| 非法问卷请求 | 空标题、伪造 owner/status、布尔传字符串、PATCH 缺版本/无变更/非法 null/直接改 questions | 422；库内原文档、数量和版本不变 | 通过 |
| 全部接口未授权 | 不带 token 调用新建、列表、详情、PATCH、题目增改删和排序 | 401 + WWW-Authenticate: Bearer | 通过 |
| 非本人/不存在/非法 ID | Bob 对 Alice 的 ID、新 ObjectId、非法字符串调用六种读取/变更操作 | 全部 404，错误结构一致；问卷不变 | 三个参数化用例通过 |
| 四种题目定义 | 依次添加单选、多选恰选 2、文本长度 1–20、整数数字区间 | 201；顺序正确；每次 revision+1；题目 q_ 和选项 opt_ ID 生成且稳定；数据库内容一致 | 通过 |
| 编辑保留 ID | 单选改为多选，保留一个旧选项 ID、移除另一个并新增选项，再删题 | PUT 200 保留 qid/复用 optid，新选项新 ID；DELETE 200、计数为 0、revision+1 | 通过 |
| 外来选项 ID 和非法题目 | 新建/编辑中放入非该题 optid；配置上下界反向、Unicode 等价重名、字符串数字 | 422；原文档不变 | 通过 |
| 排序 | 传缺失/重复/外来 qid，再传完整逆序 | 非法 422 不改变数据；合法 200，只改题序、revision+1 | 通过 |
| 版本缺失和不存在题目 | 题目各变更接口缺 expected_revision，或操作不存在 qid | 缺版本 422；不存在题目 404；不写入 | 通过 |
| 过时版本 | 用旧 revision 修改元数据、添加、替换、删除、排序 | 全部 409；数据库原文档完全不变 | 通过 |
| 同版本并发 | 两线程同时用 revision=1 添加不同题目 | 一个 201、一个 409；数据库恰好一题、revision=2，题目为成功请求内容 | 通过 |
| 应用重建 | 同一真实库关闭旧应用，新建应用，原 token 读 me 和问卷 | 原用户、会话、问卷及题目保持；公开响应一致 | 通过；并非 MongoDB 进程重启测试 |
| 初始化、schema 与索引 | 创建问卷后初始化两次，读取集合选项和索引 | strict/error、必需字段、禁止多余字段、owner+created_at+_id 索引实际存在；原文档不变 | 通过 |
| 绕过 API 写非法文档 | 缺 owner_id、owner_id 字符串、schema_version 字符串、多余字段、未知题型 | MongoDB WriteError 121，原文档保留且无非法数据 | 通过 |

离线 28 项进一步覆盖：四种合法定义边界；空/过长题干；严格布尔/整数；未知类型/字段；选项不足与 NFKC 重名；多选数量矛盾与必答 max=0；文本长度矛盾；数字区间反向/无整数；bool、字符串、NaN、±Infinity、过大数值；问卷元数据、截止时区/过去时间、PATCH 空值与版本；题序重复/非法 ID；选项 ID 重复和格式错误。它们只证明配置校验，不证明真实回答校验。

## 真实问题和修复依据

新增用例首轮 45 项全部通过；代码/需求复核仍发现两个需要补强的边界：

1. `required=true` 但多选上限为 0，没有合法回答。修改 `MultipleChoiceInput` 拒绝该组合，并加一个离线回归用例，新增用例数变为 46。
2. 独立复核在真实 MongoDB 上复现已登录请求 `offset=9223372036854775808` 返回 500，原因是分页参数编码溢出。将 Query 限为 0–2147483647，并在已有分页用例追加上界外和超 64 位数值的 422 断言。

修复后的完整 83 项均通过。测试通过不能代替独立复核，也不应把复核发现的问题说成原测试已经覆盖。

## 运行说明的实际 HTTP 复现

真正启动 Uvicorn 后，提取 `06_run_and_reproduce.md` 第 7 节 PowerShell 代码执行；对预期 409 加 catch 并断言状态，再追加删除题目、另一账号越权和退出操作。请求从实际端口进入，不是 TestClient。

首轮状态码正确，但发现 Windows PowerShell 5 错读中文 JSON 响应。新增 `scripts/http_helpers.ps1`，按 UTF-8 编码请求和解码响应后重跑，额外断言中文标题恰为“课程体验问卷（修订）”，通过。没有要求修改系统编码或安装另一个 PowerShell。

| 操作顺序 | 实际结果 |
| --- | --- |
| 注册并登录演示账号 | 均成功，token 不打印或写入记录 |
| 创建中文标题草稿 | 201，revision=1，question_count=0 |
| 查询本人列表 | 200，包含自己的草稿 |
| 依次添加单选、多选、文本、数字 | 均 201，revision=5，question_count=4 |
| 修改单选题题干，带回原选项 ID | 200，revision=6；中文内容正常 |
| 反转全题序 | 200，revision=7 |
| 修改草稿标题 | 200，revision=8；中文标题往返完全一致 |
| 旧 revision 再次改标题 | 409；成功修改保留 |
| 删除一题 | 200，revision=9，question_count=3 |
| 另一账号访问该问卷 | 404 |
| 两个账号退出 | 成功，各自会话撤销 |

修正编码后的演示草稿 ID：`6ad07c3c78920ba39e02063b`。为保留真实开发过程，两轮 HTTP 示例创建的账号和草稿都留在开发库，没有通过清库恢复“干净”状态。普通用户可以按运行说明新建自己的演示数据，无需依赖这里的账号。

## 尚未覆盖

原文要求的创建问卷、添加题目测试已有对应；配置合法性已有校验测试。题目实际回答校验、配置化跳转、填写与提交、匿名/次数策略执行和统计仍需对应模块开发后测试。尚未做网页、压力测试、跨 MongoDB 版本验证或生产部署。

本轮全部提交用过程记录为 Markdown。临时命令脚本和运行日志只属于 `.runtime` 开发产物。

## 本轮结束状态

核对并保留开发库：5 个用户、0 个会话、2 份演示草稿；上一里程碑的原演示用户仍存在。无遗留 `survey_test_*` 库。仅停止本轮创建且已核对进程标识的 API 和 MongoDB，27018、8000 均已停止监听，MongoDB 数据目录保留。临时 HTTP 检查脚本已清理，运行说明中的正式 UTF-8 助手保留在 `scripts/`。
