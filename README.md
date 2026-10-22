# MongoDB 问卷系统

使用 Python、FastAPI 和 MongoDB 实现的在线问卷系统，包含网页、后端接口和自动化测试。网页采用原生 HTML、CSS 和 JavaScript，由同一个 FastAPI 服务提供。

## 功能

- 注册、登录、退出，以及本人问卷的管理权限检查。
- 创建和编辑问卷，配置单选、多选、文本填空和数字填空，设置必答、数量、长度和数值范围。
- 根据单选、多选或数字答案配置跳转，按规则顺序使用第一条满足条件的规则。
- 发布分享链接，设置截止时间或手动关闭问卷；填写者登录后逐题作答。
- 校验答案、回退修改、确认提交，支持匿名、允许重复填写的设置、并发提交和网络重试确认。
- 查看整卷及单题统计，包括选项次数、填写内容、数字均值和答卷明细。
- 第二阶段已增加后端API：个人题目的版本保存、历史与恢复，以及在问卷草稿中选用/替换具体版本。每份问卷保留独立快照；这些新增操作目前可通过 `/docs` 调试。

## 运行环境

| 项目 | 已验证环境 |
| --- | --- |
| 系统与终端 | Windows、PowerShell |
| Python | 3.13.9 |
| 数据库 | MongoDB 6.0.29 |
| 后端 | FastAPI、Pydantic、PyMongo，版本固定于 `requirements.txt` |
| 浏览器测试 | Playwright，默认使用本机 Microsoft Edge |

先安装 Python 和 MongoDB，确保终端中可以执行 `python` 和 `mongod`。前端随后端启动，无需额外构建。

## 安装与启动

### 1. 获取代码并安装依赖

```powershell
git clone https://github.com/joyce1116/DBMS_proj1.git
cd DBMS_proj1
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

后续命令均在项目根目录执行，直接使用虚拟环境中的 Python。

### 2. 终端 A：启动 MongoDB

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\start_mongo.ps1
```

脚本监听 `127.0.0.1:27018`，数据保存在 `.runtime/mongo-data`。保持此终端运行。

### 3. 终端 B：升级旧库、初始化并启动应用

当前问卷Schema为v4。**已有第一阶段或第二阶段第①部分的v3数据库**，须先停止应用、保持MongoDB运行，再执行以下显式迁移；新空库跳过迁移：

```powershell
.\.venv\Scripts\python.exe -B -X utf8 -m scripts.migrate_surveys_v4 --dry-run
.\.venv\Scripts\python.exe -B -X utf8 -m scripts.migrate_surveys_v4
```

迁移先保存原文备份与审计，仅补空的题目来源数组并更新结构版本；原问卷、题目、答卷、业务版本和时间保留。旧v1须依次v2→v3→v4，旧v2须v3→v4；不要对已升级库重跑更早版本的脚本。已经是v4时无需再次迁移。

```powershell
.\.venv\Scripts\python.exe -B -m scripts.init_db
.\.venv\Scripts\python.exe -B -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

浏览器打开 **http://127.0.0.1:8000/**。首次使用先注册账号。

结束时先在终端 B 按 `Ctrl+C` 停止应用，再在终端 A 按 `Ctrl+C` 停止 MongoDB。数据库文件会保留，下次启动后可以继续使用。

### 可选配置

默认配置可以直接运行。需要调整时，将 `.env.example` 复制为 `.env`，修改对应配置；环境变量优先于 `.env`。

| 配置项 | 默认值 | 用途 |
| --- | --- | --- |
| `MONGO_URI` | `mongodb://127.0.0.1:27018` | MongoDB 连接地址 |
| `MONGO_DB` | `survey_phase1` | 应用数据库名称 |
| `MONGO_TIMEOUT_MS` | `3000` | 数据库连接与请求超时，毫秒 |
| `SESSION_TTL_SECONDS` | `86400` | 登录会话有效期，秒 |

更换数据库端口时，同时调整启动脚本的 `-Port` 参数和 `MONGO_URI`。实际 `.env` 和本地数据目录已由 Git 忽略。

## 网页操作

1. 注册或登录，进入“我的问卷”，点击“创建问卷”。
2. 添加题目，设置选项、必答要求、选择数量、字数或数字范围。数字题勾选“只允许整数”后，上下限也须为整数。
3. 在“跳转规则”中选择来源题、条件和后方目标题或结束。规则按列表顺序判断。
4. 在“问卷设置”中保存匿名、重复填写和截止时间等设置，再发布问卷并复制分享链接。发布后题目和设置固定。
5. 在另一个浏览器或无痕窗口打开分享链接，使用填写账号登录，逐题作答并确认提交。回退修改会清除后续答案，重新计算答题路径。
6. 回到创建者页面，查看整卷统计、单题内容和答卷明细；需要停止收集时关闭问卷。

匿名填写时仍需登录，创建者查看匿名答卷时隐藏账号。提交结果未确认时，使用“重试确认提交”；刷新后可继续确认同一请求。

## 测试

### 常规测试

保持 MongoDB 运行，在新的终端执行：

```powershell
.\.venv\Scripts\python.exe -B -X utf8 -m pytest -q
```

覆盖账号、题目配置、跳转、校验、提交、权限、统计、并发和数据保留。默认排除浏览器用例。测试自行建立应用实例，使用随机命名的隔离数据库，并在结束后清理测试库。

### 离线测试

无需启动 MongoDB：

```powershell
.\.venv\Scripts\python.exe -B -X utf8 -m pytest -m "not integration and not browser" -q
```

### 浏览器测试

保持 MongoDB 运行，安装有 Microsoft Edge 时执行：

```powershell
.\.venv\Scripts\python.exe -B -X utf8 -m pytest tests/browser -m browser -q
```

浏览器用例自动启动临时 HTTP 服务，覆盖网页编辑、发布、填写、匿名、统计、分页、提交恢复和手机布局。测试截图保存在 `.runtime/browser/`。

使用 Playwright 自带的 Chromium 时执行：

```powershell
.\.venv\Scripts\python.exe -m playwright install chromium
$env:PLAYWRIGHT_CHANNEL = 'chromium'
.\.venv\Scripts\python.exe -B -X utf8 -m pytest tests/browser -m browser -q
```

常规及浏览器测试均不需要先启动终端 B。测试数据库连接使用 `TEST_MONGODB_URI`，默认也是 `mongodb://127.0.0.1:27018`；使用其他地址时，在运行测试的终端设置该环境变量。

最近一次完整功能回归：376 项常规测试、11 项浏览器测试通过，共 387 项；包含新增题目版本、问卷选题和v4迁移测试。

## HTTP 演示

同时运行 MongoDB 和应用后，在第三个终端执行：

```powershell
.\.venv\Scripts\python.exe -B -X utf8 -m scripts.demo
```

演示会注册三个临时命名账号，创建包含四种题型和三类跳转规则的问卷，保存三份答卷，并检查匿名、重试、权限和统计。预期年龄均值为 18，文本内容为 2 条，最后关闭问卷并退出账号。

**每次演示都会在应用数据库中保留新增账号、问卷和答卷。**

## 接口与代码结构

应用运行后可访问：

| 地址 | 用途 |
| --- | --- |
| `http://127.0.0.1:8000/` | 用户网页 |
| `http://127.0.0.1:8000/docs` | 交互式 API 文档 |
| `http://127.0.0.1:8000/openapi.json` | 完整请求与响应结构 |
| `http://127.0.0.1:8000/health/ready` | 数据库就绪检查 |

业务接口除注册和登录外均要求访问令牌。调试接口时先登录，再在 API 文档的 `Authorize` 中填入返回的令牌。交互式 API 文档使用外部 CDN，用户网页使用本地静态资源。

```text
app/
  main.py          应用入口
  auth/            注册、登录、密码与会话
  surveys/         问卷、题目、跳转、答案校验与发布状态
  responses/       提交、答卷查询与统计
  questions/       独立题目、不可变版本、历史与恢复
  database/        MongoDB 连接、文档约束与索引
  core/            环境配置与时间处理
  web/             网页入口和静态资源
scripts/           MongoDB 启动、初始化与 HTTP 演示
tests/             模型、逻辑和 MongoDB 集成测试
  browser/         浏览器流程测试
requirements.txt   Python 依赖
pytest.ini         测试配置
.env.example       环境配置示例
```

## 常见问题

| 现象 | 检查方式 |
| --- | --- |
| 找不到 `mongod` | 检查 MongoDB 是否已安装，并将其可执行文件目录加入 PATH |
| 应用启动时连接数据库失败 | 确认终端 A 正在运行，连接地址与端口和 `MONGO_URI` 一致 |
| 集成测试连接数据库失败 | 检查 MongoDB 和测试终端的 `TEST_MONGODB_URI` |
| 浏览器测试找不到 Edge | 按上面的 Chromium 命令安装浏览器并切换通道 |
| 网页无法加载接口数据 | 从 `http://127.0.0.1:8000/` 打开网页，并检查终端 B 的输出 |

## 系统与设计说明

### 系统说明

系统分为页面交互、业务处理和数据保存三部分。页面收集输入并显示结果，后端统一检查权限、问卷状态、答案和跳转规则，MongoDB 保存账号、问卷及答卷。

整体流程如下：

1. 创建者登录并建立草稿，配置题目、跳转规则和填写限制，保存后发布分享链接。
2. 填写者通过链接登录，逐题作答。后端校验已有答案并决定下一题，页面显示对应题目或错误提示。
3. 填写者确认提交，后端检查收集状态和填写次数，保存实际答题路径及答案并返回回执。
4. 创建者查看统计和答卷明细，也可手动关闭问卷；到达截止时间后停止接受新答卷。

### 数据库设计

| 集合 | 保存内容 |
| --- | --- |
| `users` | 用户名、密码哈希和注册时间 |
| `sessions` | 所属账号、登录凭据摘要和有效期 |
| `surveys` | 创建者、标题、填写设置、状态、编辑版本、内嵌题目/选项/跳转，以及选题版本来源和选项映射 |
| `submissions` | 所属问卷、填写账号、匿名选择、请求标识、提交时间、实际路径和答案 |
| `question_versions` | 独立题目的版本家族、父版本、完整配置、所有者及创建/派生/恢复操作 |

会话关联账号，问卷关联创建者，答卷关联问卷和填写者。题目与选项使用固定编号，规则和答案通过这些编号找到对应内容。

问卷配置需要整体读取和编辑，因此将题目、选项和规则放入同一文档，减少逐项拆成关系表后的关联读取和拼装。答卷随提交持续增加，独立保存后，每次填写只需追加一份答卷。

MongoDB 的嵌套文档和数组适合表达多种题型及答案。文档校验约束保存格式，单文档原子更新保护草稿编辑，唯一索引执行用户名唯一、请求去重和填写限次，聚合管道生成统计。

### API 说明

下表列出主要业务接口。`{id}` 为问卷编号，`{qid}` 为题目编号，`{share_id}` 为分享标识；完整请求和响应结构见运行后的 `/openapi.json`。

| 方法与路径 | 用途及主要输入 |
| --- | --- |
| `POST /api/auth/register`；`POST /api/auth/login` | 提交用户名和密码，注册或获取登录令牌 |
| `GET/POST /api/surveys` | 列出本人问卷或创建草稿 |
| `GET/PATCH /api/surveys/{id}` | 读取详情，或携带编辑版本修改问卷设置 |
| `POST /api/surveys/{id}/questions` | 携带当前编辑版本添加题目；指定题目路径支持修改和删除 |
| `PUT /api/surveys/{id}/rules` | 提交当前编辑版本和跳转规则列表 |
| `POST /api/surveys/{id}/publish`；`POST /api/surveys/{id}/close` | 携带当前编辑版本发布或关闭问卷 |
| `GET /survey/{share_id}` | 打开填写入口，登录后获取问卷内容 |
| `POST /survey/{share_id}/validate` | 提交题目编号和答案，执行单题校验 |
| `POST /survey/{share_id}/navigate` | 提交问卷版本和已有答案，取得答题路径及下一题 |
| `POST /survey/{share_id}/responses` | 提交问卷版本、请求标识、匿名选择和完整答案，取得回执 |
| `GET /api/surveys/{id}/responses` | 创建者分页读取答卷 |
| `GET /api/surveys/{id}/statistics`；`GET /api/surveys/{id}/statistics/{qid}` | 查看整卷统计或单题统计与填空内容 |
| `GET/POST /api/questions`；`GET /api/questions/{version_id}` | 创建个人题目版本、列出本人所有版本或读取具体版本 |
| `GET /api/questions/{version_id}/history`；`POST .../versions`；`POST .../restore` | 读取版本历史、派生或恢复为新的版本 |
| `POST /api/surveys/{id}/question-selections`；`PUT .../question-selections/{qid}` | body提供version_id，query提供expected_revision，添加或显式替换草稿选题 |

常见错误状态：401 未登录，404 资源不存在或无管理权限，409 版本或重复冲突，410 问卷关闭或截止，422 配置或答案错误，503 数据库不可用。

### 关键逻辑说明

**编辑与发布：** 草稿保存时同时检查创建者、草稿状态和编辑版本，成功后版本递增。同一旧版本的后续保存提示冲突。发布后固定题目、规则和填写设置，使各份答卷对应一致的问卷内容。

**跳转与校验：** 后端从第一题开始，检查必答、选项归属、数量、字数和数值范围，再按顺序采用第一条满足条件的规则。未命中时进入下一题，命中时转到后方题目或结束。跳过的必答题可省略；回退修改会清除后续答案，最终提交重新计算路径并拒绝旧分支答案。

**提交与重试：** 提交流程为“核对登录及历史请求 → 检查开放状态、版本和匿名设置 → 重算路径并校验答案 → 检查填写次数 → 确认受理 → 保存答卷 → 返回回执”。相同请求标识和内容再次到达时返回原回执，内容改变则提示冲突。网络故障后保留原标识和内容重试，确认同一次提交的结果。

**匿名与并发：** 匿名答卷在创建者页面隐藏账号，内部保留账号关联，用于限次和重试。数据库唯一约束处理同时到达的提交。受理与关闭操作在同一问卷记录上确定先后：先受理的请求可继续保存，先关闭或已经截止时拒绝新请求。

**统计：** 按问卷筛选已保存答卷，再按题目汇总回答份数和选项次数，分页展示填空内容，并计算有效数字的均值。留空和被跳过的题目不参与均值计算。

详细设计、流程图和测试分析见单独提交的《项目完成报告》。
