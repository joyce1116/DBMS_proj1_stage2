# 环境、运行与测试复现

适用范围：第一阶段前两个里程碑，包含账号及问卷草稿/题目配置后端。尚未实现发布、填写、跳转、统计或用户网页。所有命令在 Windows PowerShell 的项目根目录执行；无需激活虚拟环境，也不需要创建 README。本轮沿用原有 `.venv` 和固定依赖，没有新增依赖。

## 本轮实测环境

| 项目 | 实测值 |
| --- | --- |
| 系统 | Windows，PowerShell |
| 项目目录 | `D:\DBMS\project1` |
| Python | 3.13.9，64 位 |
| MongoDB Server | 6.0.29，本机已有安装 |
| MongoDB 程序 | `C:\Program Files\MongoDB\Server\6.0\bin\mongod.exe`，已在 PATH |
| FastAPI / PyMongo | 0.143.1 / 4.19.0 |
| Uvicorn / pytest / HTTPX | 0.54.0 / 9.1.1 / 0.28.1 |
| 依赖锁定 | `requirements.txt`，含测试依赖 |
| 数据库地址 | `mongodb://127.0.0.1:27018` |
| 开发数据库 | `survey_phase1` |

这里记录实际运行环境，不表示验证过其他 Python/MongoDB 版本。

## 1. Python 环境

本轮已建立 `.venv` 并安装好依赖，在当前电脑通常可以直接进入第 2 步。新环境按下列命令操作：

本轮验证结束后已停止 API 和 MongoDB，保留数据库文件；当前要运行请按第 2、3 步重新启动。

```powershell
Set-Location D:\DBMS\project1
python --version
mongod --version
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip check
```

`requirements.in` 记录直接依赖范围，`requirements.txt` 锁定本轮实际安装的全部依赖。复现使用后者。首次下载依赖需要联网。

可选复制配置文件；以下命令不会覆盖已有 `.env`：

```powershell
if (-not (Test-Path -LiteralPath .env)) {
    Copy-Item -LiteralPath .env.example -Destination .env
}
```

不复制时，代码默认值已经与 `.env.example` 相同。环境变量优先于 `.env`。变量包括 `MONGO_URI`、`MONGO_DB`、`MONGO_TIMEOUT_MS`、`SESSION_TTL_SECONDS`；会话有效期默认 86400 秒。

## 2. 在终端 A 启动 MongoDB

```powershell
Set-Location D:\DBMS\project1
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\start_mongo.ps1
```

脚本使用已安装的 `mongod`，只绑定 `127.0.0.1`，端口 27018，数据目录为项目下 `.runtime\mongo-data`。保留终端 A，按 Ctrl+C 停止数据库；停止并重新启动会保留数据。不要删除这个目录来“重新初始化”。

如果提示端口已占用，先用下面的连接检查确认是否已有可用实例；不要对不明进程执行终止。若另选端口，启动脚本使用 `-Port 27019`，并同步调整 `.env` 的 `MONGO_URI` 及测试的 `TEST_MONGODB_URI`。

```powershell
.\.venv\Scripts\python.exe -c "from pymongo import MongoClient; c=MongoClient('mongodb://127.0.0.1:27018', serverSelectionTimeoutMS=3000); print(c.admin.command('ping')); c.close()"
```

若 PATH 中找不到 `mongod`，可用实测路径直接运行，参数与脚本相同：

```powershell
New-Item -ItemType Directory -Path .runtime\mongo-data -Force | Out-Null
& 'C:\Program Files\MongoDB\Server\6.0\bin\mongod.exe' --dbpath 'D:\DBMS\project1\.runtime\mongo-data' --bind_ip 127.0.0.1 --port 27018
```

本地演示实例未开启 MongoDB 账户认证，仅用于当前电脑的课程开发。对外部署时需另行配置数据库认证和 HTTPS；本轮未进行部署。

## 3. 在终端 B 初始化并启动 API

```powershell
Set-Location D:\DBMS\project1
.\.venv\Scripts\python.exe -m scripts.init_db
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

预期初始化输出：`MongoDB initialized: survey_phase1 (users, sessions, surveys)`。初始化可以重复执行；API 启动也会做相同检查。此次只新增 surveys 集合与索引，保留已有用户和会话；无需清库、重建虚拟环境或手工迁移。MongoDB 不可连接或旧集合 schema 不兼容时会启动失败，先处理具体问题，不要清库。

浏览器可访问 `http://127.0.0.1:8000/docs` 交互测试；账号与 survey drafts 两组接口均可操作。`/docs` 的默认界面使用外部 CDN，离线时用下列命令。JSON 接口契约为 `http://127.0.0.1:8000/openapi.json`。当前没有主页或问卷页面，访问 `/` 返回 404 是预期结果。

## 4. 在终端 C 手动跑通账号流程

这些命令会在开发数据库中保留一个演示账号。每次生成新用户名，可以重复执行。不要使用其他系统的真实密码。

```powershell
Set-Location D:\DBMS\project1
$apiBase = 'http://127.0.0.1:8000'
Invoke-RestMethod "$apiBase/health/ready"
$demoUsername = 'demo_' + [guid]::NewGuid().ToString('N').Substring(0, 10)
$demoBody = @{ username = $demoUsername; password = 'CourseDemo_2026' } | ConvertTo-Json

# 201：返回用户公开信息，不返回密码/哈希。
$newUser = Invoke-RestMethod "$apiBase/api/auth/register" -Method Post -ContentType 'application/json' -Body $demoBody
$newUser

# 登录令牌保存在变量里，不必复制或打印。
$loginResult = Invoke-RestMethod "$apiBase/api/auth/login" -Method Post -ContentType 'application/json' -Body $demoBody
$authHeaders = @{ Authorization = "Bearer $($loginResult.access_token)" }

# 200：应与 newUser 是同一用户。
Invoke-RestMethod "$apiBase/api/auth/me" -Headers $authHeaders

# 204，无响应体。
Invoke-RestMethod "$apiBase/api/auth/logout" -Method Post -Headers $authHeaders

# 预期 401，PowerShell 显示 HTTP 错误，表示退出后令牌已失效。
Invoke-RestMethod "$apiBase/api/auth/me" -Headers $authHeaders
```

还可以再次用相同用户名注册观察 409；把密码改错再登录观察 401；用 2 字符用户名注册观察 422。完整输入/预期/实际结果见 `05_test_record.md`。

## 5. 自动化测试

离线单元测试不需要 MongoDB，也不需要启动 API：

```powershell
.\.venv\Scripts\python.exe -m pytest -m 'not integration' -q
```

完整测试需要终端 A 的 MongoDB；无需启动终端 B 的 API，因为测试通过 TestClient 自行创建应用：

```powershell
$env:TEST_MONGODB_URI = 'mongodb://127.0.0.1:27018'
.\.venv\Scripts\python.exe -m pytest -q
```

只跑集成测试：

```powershell
.\.venv\Scripts\python.exe -m pytest -m integration -q
```

测试创建随机 `survey_test_<uuid>` 数据库，结束时只清理自己创建的库，不使用 `survey_phase1`。连接失败会报失败，不会跳过后声称通过。若上次测试被强制中断留下测试库，可先查明名称和来源，本轮不提供按前缀批量清库命令。

## 6. 文件位置与正常停机

- 需求边界：`docs/01_scope_and_requirements.md`。
- MongoDB schema、API、关键逻辑：`docs/02_database_and_api.md` 和 `app/db.py`。
- AI 日志、报告草稿、测试实测：`docs/03_ai_usage_log.md`、`docs/04_report_draft.md`、`docs/05_test_record.md`。
- 新增问卷草稿设计与测试：`docs/07_survey_draft_design.md`、`docs/08_survey_draft_tests.md`。
- 运行生成的数据、日志和临时结果：`.runtime/`，不应当作代码提交，但数据库目录应保留用于继续开发。

先在终端 B 按 Ctrl+C 停止 API，再在终端 A 按 Ctrl+C 停止 MongoDB。下一次按第 2、3 步启动，沿用现有用户和集合。

## 7. 继续手动复现：草稿与题目

先完成第 2、3 步。下面代码在开发库保存一个新演示账号及其草稿问卷，可直接连续执行。先允许当前终端执行项目脚本（仅当前进程），再加载 UTF-8 请求助手，显式处理请求与响应编码，避免 Windows PowerShell 5 将中文 JSON 读成乱码。此助手只是命令行复现工具，正常 JSON 客户端可直接调用 API。

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
. .\scripts\http_helpers.ps1
$apiBase = 'http://127.0.0.1:8000'
$demoUsername = 'survey_' + [guid]::NewGuid().ToString('N').Substring(0, 10)
$demoBody = @{ username = $demoUsername; password = 'CourseDemo_2026' } | ConvertTo-Json
$newUser = Invoke-Utf8RestMethod "$apiBase/api/auth/register" -Method Post -ContentType 'application/json' -Body $demoBody
$loginResult = Invoke-Utf8RestMethod "$apiBase/api/auth/login" -Method Post -ContentType 'application/json' -Body $demoBody
$authHeaders = @{ Authorization = "Bearer $($loginResult.access_token)" }

# 201：创建空草稿，revision=1。
$surveyBody = @{ title = '课程体验问卷'; description = '草稿测试'; allow_anonymous = $true } | ConvertTo-Json
$draft = Invoke-Utf8RestMethod "$apiBase/api/surveys" -Method Post -Headers $authHeaders -ContentType 'application/json; charset=utf-8' -Body ([Text.Encoding]::UTF8.GetBytes($surveyBody))
$surveyId = $draft.id
$draft | Select-Object id,title,status,revision,question_count

# 200：这里只列出自己的问卷。
Invoke-Utf8RestMethod "$apiBase/api/surveys?offset=0&limit=20" -Headers $authHeaders

# 201：添加单选题，生成题目与选项ID，revision加1。
$questionBody = @{ type = 'single_choice'; title = '你的年级'; required = $true; options = @(@{ label = '大一' }, @{ label = '大二' }) } | ConvertTo-Json -Depth 6
$draft = Invoke-Utf8RestMethod "$apiBase/api/surveys/$surveyId/questions?expected_revision=$($draft.revision)" -Method Post -Headers $authHeaders -ContentType 'application/json; charset=utf-8' -Body ([Text.Encoding]::UTF8.GetBytes($questionBody))
$singleId = $draft.questions[0].id
$draft.questions

# 多选：恰好选择2个，用min=max表达。
$questionBody = @{ type = 'multiple_choice'; title = '选两种水果'; required = $true; options = @(@{ label = '苹果' }, @{ label = '香蕉' }, @{ label = '西瓜' }); min_selections = 2; max_selections = 2 } | ConvertTo-Json -Depth 6
$draft = Invoke-Utf8RestMethod "$apiBase/api/surveys/$surveyId/questions?expected_revision=$($draft.revision)" -Method Post -Headers $authHeaders -ContentType 'application/json; charset=utf-8' -Body ([Text.Encoding]::UTF8.GetBytes($questionBody))

# 文本填空：长度2到100。
$questionBody = @{ type = 'text'; title = '建议'; min_length = 2; max_length = 100 } | ConvertTo-Json
$draft = Invoke-Utf8RestMethod "$apiBase/api/surveys/$surveyId/questions?expected_revision=$($draft.revision)" -Method Post -Headers $authHeaders -ContentType 'application/json; charset=utf-8' -Body ([Text.Encoding]::UTF8.GetBytes($questionBody))

# 数字填空：0到120的整数。
$questionBody = @{ type = 'number'; title = '年龄'; min_value = 0; max_value = 120; integer_only = $true; required = $true } | ConvertTo-Json
$draft = Invoke-Utf8RestMethod "$apiBase/api/surveys/$surveyId/questions?expected_revision=$($draft.revision)" -Method Post -Headers $authHeaders -ContentType 'application/json; charset=utf-8' -Body ([Text.Encoding]::UTF8.GetBytes($questionBody))
$draft | Select-Object id,revision,question_count

# 200：修改题干时带回原选项ID，保留题目和选项身份。
$editBody = @{ type = 'single_choice'; title = '请选择当前年级'; required = $true; options = @($draft.questions[0].options | Select-Object id,label) } | ConvertTo-Json -Depth 6
$draft = Invoke-Utf8RestMethod "$apiBase/api/surveys/$surveyId/questions/$($singleId)?expected_revision=$($draft.revision)" -Method Put -Headers $authHeaders -ContentType 'application/json; charset=utf-8' -Body ([Text.Encoding]::UTF8.GetBytes($editBody))

# 200：整份题序反转，必须传入全部ID且各出现一次。
$questionIds = @($draft.questions | ForEach-Object { $_.id })
[array]::Reverse($questionIds)
$orderBody = @{ question_ids = $questionIds } | ConvertTo-Json -Depth 4
$draft = Invoke-Utf8RestMethod "$apiBase/api/surveys/$surveyId/question-order?expected_revision=$($draft.revision)" -Method Put -Headers $authHeaders -ContentType 'application/json' -Body $orderBody

# 200：修改元数据。保存旧版本号，用于下一条冲突演示。
$oldRevision = $draft.revision
$patchBody = @{ expected_revision = $draft.revision; title = '课程体验问卷（修订）' } | ConvertTo-Json
$draft = Invoke-Utf8RestMethod "$apiBase/api/surveys/$surveyId" -Method Patch -Headers $authHeaders -ContentType 'application/json; charset=utf-8' -Body ([Text.Encoding]::UTF8.GetBytes($patchBody))

# 捕获预期409：旧版本不得覆盖已经保存的修改。
$staleBody = @{ expected_revision = $oldRevision; title = 'This must not overwrite the title' } | ConvertTo-Json
try {
    Invoke-Utf8RestMethod "$apiBase/api/surveys/$surveyId" -Method Patch -Headers $authHeaders -ContentType 'application/json' -Body $staleBody | Out-Null
    throw 'Expected HTTP 409'
} catch {
    if ([int]$_.Exception.Response.StatusCode -ne 409) { throw }
    Write-Output 'Expected stale revision: HTTP 409'
}

# 200：重新读取后，标题仍为“课程体验问卷（修订）”。
Invoke-Utf8RestMethod "$apiBase/api/surveys/$surveyId" -Headers $authHeaders
```

删除某题时使用 `DELETE /api/surveys/{id}/questions/{qid}?expected_revision=当前版本`，成功返回完整问卷和新版本。尝试错误上下界、只剩一个选项、重复标签等会得到 422 且不写入。要验证权限，另注册登录一个账号，用其 token 访问上述 surveyId，应为 404；自己的列表不包含它。自动化用例覆盖了这些步骤。

只跑本轮新增测试（需要 MongoDB，仍不需要启动 API）：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_survey_validation.py tests/test_surveys_integration.py -q
```

测试范围是草稿和题目配置，不会发布或提交问卷。完整设计及预期错误见 `07_survey_draft_design.md`，实测结果见 `08_survey_draft_tests.md`。
