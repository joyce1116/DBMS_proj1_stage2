# MongoDB 问卷系统

使用 Python、FastAPI 和 MongoDB 实现的在线问卷系统，包含网页、后端接口和自动化测试。网页采用原生 HTML、CSS 和 JavaScript，由同一个 FastAPI 服务提供。

## 功能

- 注册、登录、退出，以及本人问卷的管理权限检查。
- 创建和编辑问卷，配置单选、多选、文本填空和数字填空，设置必答、数量、长度和数值范围。
- 根据单选、多选或数字答案配置跳转，按规则顺序使用第一条满足条件的规则。
- 发布分享链接，设置截止时间或手动关闭问卷；填写者登录后逐题作答。
- 校验答案、回退修改、确认提交，支持匿名、允许重复填写的设置、并发提交和网络重试确认。
- 查看整卷及单题统计，包括选项次数、填写内容、数字均值和答卷明细。

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

### 3. 终端 B：初始化数据库并启动应用

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

最近一次完整功能回归：286 项常规测试、11 项浏览器测试通过，共 297 项。

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
