# AI 使用日志与说明草稿

记录日期：2026-10-15。此文件随实际操作补记，不代表完整第一阶段报告。

## 使用工具和参与边界

本轮使用 Codex 编程代理及其文件、PowerShell、Python 执行工具，并使用协作子代理分别审查需求、编写测试和审查代码。界面未提供可由本项目独立核实的具体模型版本，故不编造型号。

以下代码和设计主要由 AI 提出、生成并验证。用户目前给出了任务范围和语言要求；本轮尚无用户亲自改代码、手动测试或独立设计的证据。最终提交时，“自己改了什么、自己设计了什么”必须由本人核实并补充，不能把 AI 的行为写成学生本人行为。

## 实际提示与生成内容

### 1. 用户原始需求（节选，未改写）

> “现在只要求做**第一阶段**，不要做第二阶段的事情”
>
> “实现的时候，同步记录要求的ai使用说明、测试记录、以及报告等对应内容的填写草稿”
>
> “额外创建一个markdown告诉我运行的环境/命令或者步骤”
>
> “README先不用写。编程语言选择python。”
>
> “不要一次性把第一阶段全部做完！”

输入材料：`D:/DBMS/大作业一.docx`。AI 使用 Python 标准库 zipfile/ElementTree 读取其中 `word/document.xml`，完整段落摘录保存在 `00_assignment_source.md` 并记录源文件 SHA-256。文档是作业要求材料，本轮实施边界仍以用户要求为准。

结果：将本轮限制为账号基础闭环；没有生成全部问卷系统或第二阶段功能。

### 2. 发给需求审查子代理的真实提示词

> 请基于已提取的作业文档做独立需求审查，不修改文件。为本轮仅做第一阶段的首个里程碑提出边界，建议范围为工程骨架 + MongoDB基础 + 注册登录，问卷功能留待后续。重点指出歧义（登录与匿名、多次填写等）、为第二阶段保留成果所需的最小扩展设计、应记录的AI/测试/报告内容，以及该里程碑该验证的真实测试。明确不要实现第二阶段或扩大到全部第一阶段。中文返回简明建议。

结果与修改：子代理最初建议包含简单账号页面；主代理决定本轮先完成 API 和交互接口文档，页面留待后续，并明确要求子代理同步修改范围。子代理起初没有看到提取文本，随后自行读取原 DOCX；没有在缺失原文时冒充完成审查。生成 `01_scope_and_requirements.md` 和 `04_report_draft.md`。

### 3. 发给测试子代理的真实提示词（关键片段）

> 负责仅 tests/ 和 pytest.ini 的自动化测试实现，勿改应用/文档。
>
> 测试重点：真Mongo积分集成（URI env TEST_MONGODB_URI 默认 mongodb://127.0.0.1:27018，独立随机问卷测试库前缀 survey_test_，显式标记integration，连接失败pytest.fail不要skip，清理仅该新库）；单元可独立离线跑 -m 'not integration'。
>
> 涵盖注册/重复/并发唯一性/校验/密码哈希不泄露/登录/身份隔离/注销/已过期但TTL尚未删拒绝/重启app后旧用户token可用/初始化幂等schema及索引。

说明：“真Mongo积分集成”为当时提示中的原始笔误，实际要求和实现均为真实 MongoDB 集成测试。主代理另给出了精确 API 路径、状态码、字段和用户名规范化规则；测试代理依据契约实现用例，而不是替应用模拟业务数据。

### 4. 发给代码审查子代理的真实提示词（节选）

> 请对当前D:\DBMS\project1/app与scripts/init_db.py做只读审查。本轮只做阶段一首个里程碑工程+Mongo+注册登录，未做页面/问卷/阶段二。聚焦可运行性、认证/数据安全、Mongo schema与幂等、同步PyMongo与FastAPI用法、可延续性，避免扩大范围。

实际审查结论：未发现本轮必须修复的阻塞问题；复核了同步 MongoDB 在线程池/同步路由中的使用、密码和会话处理、初始化保留数据及毫秒时间修复。审查代理在独立随机库补测了 Unicode 异常输入和索引，测试库已经清理；未修改源码。当前本地开发版本尚无登录限速；管理员绕开应用写入损坏的密码哈希时的容错可后续处理，不声称已具备完整生产防护。

### 5. 主代理直接生成的内容

主代理没有为每个源文件向自己发送另一条独立提示，不能虚构不存在的 prompt。它在上述用户请求和 DOCX 要求下直接生成：

- `app/`：配置、集合 schema/索引、密码与令牌处理、请求响应模型、MongoDB 仓储、账号服务、路由和应用工厂。
- `scripts/init_db.py`、`scripts/start_mongo.ps1`：初始化和本地数据库启动。
- `.env.example`、依赖清单、`.gitignore`。
- 数据库/API 草稿、此 AI 日志、测试记录、独立运行说明。

采用的主要设计：Argon2id 密码哈希；随机 Bearer 令牌的摘要存 MongoDB；UTC 时间；规范化用户名唯一索引；文档版本字段；已有 schema 不一致时显式停止，后续通过增量迁移保留数据。

## 已遇到的问题与 AI 调整

1. 最初从 PowerShell 管道向 Python 传递含中文文件名的脚本，中文路径变成 `????.docx`，读取失败。改为在父目录枚举 DOCX 文件，再以 UTF-8 输出；源文件没有改动。
2. 沙箱中创建 venv 时，ensurepip 的临时目录写入被拒绝。尝试把临时目录改到项目内仍失败，之后通过已授权的权限申请创建虚拟环境并安装依赖。没有改系统 Python 包或要求用户手动配置。
3. 一次调试代码假设 `app.routes` 所有对象都有 `.path`，在实际安装的 FastAPI 版本报 `AttributeError`。改用 `app.openapi()['paths']` 核对真实接口；该调试代码没有进入应用。
4. MongoDB TTL 删除不即时，不能只靠数据库删除来阻止过期凭证。实现主动到期过滤，并加入对应回归用例。这是设计时规避的问题，不冒充一次真实生产故障。
5. 首轮 35 项测试出现 7 个失败：其中 5 个来自注册返回的微秒时间与 MongoDB 毫秒存储不一致。主代理修改 `app/services.py`，新增 `mongo_utc_now()`，统一用户和会话的创建时间精度，原测试保持有效并通过。这是本轮真实发现的应用缺陷。
6. 其余 2 个失败来自测试自身把错误信息中的英文 `input`/`short` 误当作秘密泄露。测试代理修改 `tests/test_auth_integration.py` 的断言及短密码输入，改为核验错误对象不含 input/context。随后补充了字段 BSON 类型、旧 schema 冲突保护测试；最终主代理实跑 37 项全部通过，保留 1 条第三方弃用 warning。详情在 `05_test_record.md`。

## 最终 AI 使用说明待完善的材料

目前可如实写：AI 完成了需求拆分、基础设计、代码与测试生成、依赖环境检查、真实执行和记录整理。AI 曾在中文路径编码和框架调试接口上出错，修正后继续验证。测试实际结果见 `05_test_record.md`。

学生后续需要补充：自己理解并审查了哪些实现、亲自复现的命令和结果、实际修改的文件及原因、本人做出的设计决定。尚未做的事情保留为待补充。

## 续做：第一阶段第二个里程碑

### 本次真实用户提示词

> ok.继续做第一阶段未做完的东西

主代理结合上一轮“不要一下子全部做完”的约束，选择继续做问卷草稿与题目配置，不直接完成全部第一阶段。仍不做第二阶段，不写 README。

### 本次实际分工提示（原始节选）

给需求/设计复核子代理：

> 本轮沿用既有账号/Mongo工程，用户要继续第一阶段但保留分步要求，范围已定“问卷草稿管理+创建者权限+三类题目(单选/多选/填空文本和数字)配置校验”，不做发布/填写/跳转/统计/前端。请只读审查原文docs/00_assignment_source.md与现有架构，提出本轮最小合理数据模型与接口/权限/并发保留设计，特别题目和选项稳定ID，旧账号回归和新增collection保留旧数据。

给模型/schema 子代理：

> 负责仅新增 app/survey_models.py 和 app/survey_schema.py（不要改其他）。请实现本轮草稿问卷请求/响应模型和Mongo严格JSON schema常量，root负责路由/服务/仓储/db集成。保持Python3.13/Pydantic2现有依赖无新增。

提示随后给出了实际字段契约：四个 type 变体、严格类型、选项重名与 ID 规则、多选/文本/数字上下界、问卷元数据与 PATCH 空值规则、响应结构、嵌套 MongoDB schema。实际生成的代码保存在这两个文件中，不用概括文字替代源码。

给测试子代理：

> 负责仅新增 tests/test_surveys_integration.py、tests/test_survey_validation.py；不要修改其他已有文件。root实现草稿问卷API，另一agent写app/survey_models.py。沿用tests/conftest.py真实Mongo独立随机测试库，marker integration；root稍后启动localhost27018，先编测试。保持原37用例不改。

> 测试建议25-40个有价值参数化用例覆盖CRUD own lists/权限、非法配置及无写入、题目四种添加与ID稳定、更新/删/排序、revision并发、重建app保留旧users/sessions/问卷、真实schema/owner索引/直接非法数据拒绝。

实际以有用的参数边界为准，最终新增 46 个用例。提示还给出了具体 API 契约和状态码；没有让测试代理使用内存库代替真实 MongoDB。

给独立代码复核子代理的追加提示（原始节选）：

> 请现在做独立只读code review app/survey_service.py,survey_repository.py,survey_routes.py及main/db集成，尤其ID保留/越权/CAS/类型错误与原接口兼容，不改文件。

### 得到什么，采用或修改了什么

- 模型代理生成了 `survey_models.py` 和 `survey_schema.py`；主代理采用后补上“多选必答但 max=0”必须拒绝的规则。
- 主代理直接生成 `survey_repository.py`、`survey_service.py`、`survey_routes.py`，在既有 `main.py` 和 `db.py` 接入；保留原认证代码及 users/sessions schema。
- 测试代理新增两个测试文件；主代理在已有分页用例中追加巨大 offset 的边界断言，原 37 个账号测试保持不变。
- 设计复核代理更新范围表与报告草稿；主代理补数据库/API 设计、复现命令、此日志及实际测试记录。
- 主代理在实跑运行说明后新增 `scripts/http_helpers.ps1`，处理 Windows PowerShell 5 的 JSON 响应编码，不改变服务端标准 JSON 响应。

这些设计和改动仍由 AI 执行。用户给出了继续开发的要求，目前没有新增证据表明学生本人亲自写了或修改了某个模块。

### 真实问题与修正

1. 设计复核发现多选题 `required=true,max_selections=0` 无法满足必答要求。主代理补模型校验，测试代理补回归用例。它是在审查时发现的配置矛盾，不冒充某次失败测试。
2. 独立复核使用真实 MongoDB 复现超大 `offset=9223372036854775808` 返回 500，原因是驱动编码分页参数溢出。主代理将 offset 限为 0–2147483647，追加越界应 422 的断言。复核的独立测试库已清理。
3. 运行文档的首轮真实 PowerShell HTTP 流程状态码正确，但 Windows PowerShell 5 将缺少显式 charset 的标准 JSON 响应按错误编码解读，显示中文乱码；若将错误解读的选项文字再次提交，还会影响编辑内容。新增显式 UTF-8 解码助手，重新执行文档流程并断言中文题名往返一致，通过。没有把仅状态码通过当作中文内容也正确。
4. 模型代理的一次临时 smoke 命令同样遇到 PowerShell 管道中文字符替换，改用 Unicode 转义检查；源文件由正确的 UTF-8 补丁创建，未将该命令编码问题误记成模型缺陷。

原 37 个用例接入后先回归通过。新增用例首轮为 45 passed，随后补必答多选配置用例。最终全量 **83 passed, 1 warning in 13.47s**，包括原 37 项和新增 46 项。既有第三方 TestClient 弃用警告如实保留，没有新增依赖。详细输入、步骤与实际结果见 `08_survey_draft_tests.md`。

## 续做：第一阶段第三个里程碑

### 用户真实提示

> 继续做吧。

主代理沿用分步实施、不要一次做完第一阶段的约束，本轮选择发布/关闭/截止和登录只读分享，不实现网页、回答、跳转、提交或统计。全程在原代码上修改，没有开始第二阶段。

### 子代理真实提示节选

给设计审查：

> 本轮继续第一阶段第3个里程碑，范围先定发布/关闭/截止时间判断/分享链接(登录后只读JSON)，不做网页、填写提交、跳转、统计。请只读审查现有app/与原文docs/00_assignment_source.md，快速给生命周期+兼容增量建议，重点需要显式迁移现surveys schema v1(仅draft)到v2，绝不丢弃旧数据/重做；发布后冻结题目/元数据、仅允许关闭，无重新发布；分享仍须登录，匿名不等于免登录。

给迁移实现：

> 仅负责app/survey_migration.py和scripts/migrate_surveys_v2.py（可新增tests/test_survey_migration.py专测你代码；不要改其他文件，root会修改survey_schema提供常量）。本轮显式离线MongoDB迁移surveys v1(仅draft)到v2，保留所有旧数据，启动不偷偷迁移。

> 正常流程应用需停止(明确离线条件，不实现在线迁移)：在Mongo `schema_migrations` 记录 _id:'surveys_v1_to_v2',state:'in_progress'/'completed',backup_collection唯一名surveys_v1_backup_<uuid>,started_at/completed_at、counts；每个v1原文档备份到该新集合(包含原_id，一条不漏，不能覆盖既有数据)，再将surveys validator换为 {'$or':[{'$jsonSchema':LEGACY},{'$jsonSchema':new}]}；update_many只match schema_version1设置schema_version2,share_id:null,published_at:null,closed_at:null；最终核对无旧/非法doc并切new strict/error，记录完成。

给生命周期测试：

> 仅负责新增 tests/test_survey_lifecycle.py，不改原测试或app。root实现本轮第一阶段M3：发布/关闭/截止/登录分享JSON，不做页面/回答/跳转/统计。现有83tests必须回归，另一agent做显式迁移。

> 建议约20-30项有价值case覆盖流程/状态、publish invalid、不写入、unauth+owner、deadline equality/expiry/closing、冻结全部编辑、share最小数据/必须login/匿名不豁免、并发双发布只有一成功、重建共享token与旧会话保留、schema/index。

以上只是实际长提示的明确节选，完整接口契约由主代理一并给出。测试代理最终按有用参数边界实现 36 项，并自行补了发布/编辑竞争与真实重复分享标识重试；迁移代理实现 8 项迁移测试，没有把断言数冒充用例数。

### 得到的代码与实际修改

- 主代理在原 survey service/repository/routes 上增加发布/关闭 CAS、UTC 截止检查、独立分享响应和认证入口；没有重写账号模块。
- 原 v1 schema 保留为 `LEGACY_SURVEY_SCHEMA`，在其复制结构上增加 v2 字段及状态/时间组合约束，保留题目结构；db 初始化增加 partial unique 分享索引并继续拒绝静默迁移。
- 迁移代理生成 `app/survey_migration.py`、`scripts/migrate_surveys_v2.py` 和 8 项测试；主代理复核后依次运行 dry-run、正式迁移、逐字段核验和幂等重跑。
- 生命周期代理新增 `tests/test_survey_lifecycle.py`。旧测试只有新文档 schema_version 的预期从 1 改为 2；原业务测试仍回归。
- 主代理依据设计复核，将分享随机碰撞的重试限定为确实来自 share_id 索引的 DuplicateKeyError；其他数据库错误保留原处理方式。
- 设计审查代理更新范围和报告草稿，主代理写设计、测试和运行记录并补实际结果。

### 采用的选择与审查取舍

发布后冻结、不能重开、当前截止时刻相等即过期、分享仍须登录均明确记录。审查曾建议分享响应连 revision 也不返回；主代理选择保留发布 revision，供后续提交引用固定内容，同时排除 owner、状态和账号等管理数据。这是 AI 作出的具体取舍，不伪装成原文逐字规定或用户亲自设计。

本轮旧 83 项回归通过，新增生命周期测试首轮 34 项通过，后补两项定向通过，迁移 8 项通过；最终主代理实跑 **127 passed, 1 warning in 35.34s**。实际迁移保留两份旧草稿，备份和原字段核对完成；运行说明第 8 节的真实 HTTP 流程通过。详细输入输出在 `10_survey_lifecycle_tests.md`。

本轮没有实际测试失败需要修复，不编造 AI 错误。迁移的中断是测试主动注入，真实开发库迁移一次成功。剩余网页、填写/跳转/提交/统计尚未实现。学生本人亲自复现、改动及独立设计的经历仍待本人核实补写。
