# 本轮数据库和 API 设计草稿

记录日期：2026-10-15。本文件保留第一阶段第一个里程碑的账号设计记录。后续问卷草稿和题目配置见 [`07_survey_draft_design.md`](07_survey_draft_design.md)，当前生命周期、分享及 schema v2 迁移见 [`09_survey_lifecycle_design.md`](09_survey_lifecycle_design.md)。以下历史范围描述仅指第一个里程碑，不代表当前全部进度。

## 为什么这样拆

使用 Python + FastAPI + 官方 PyMongo 驱动。`routes.py` 处理 HTTP，`services.py` 处理账号业务，`repositories.py` 处理 MongoDB 操作，`db.py` 定义数据库约束和索引。这是当前账号模块的实现，后续问卷模块沿用相同工程结构。

作业要求所有业务数据存 MongoDB，因此没有 SQLite、关系数据库或内存账号存储。密码的哈希、用户和登录会话都持久化到 MongoDB；`.env`、运行日志、测试结果是配置和开发记录，不承担业务数据存储。

用户和会话的生命周期不同：一个用户可多次登录，过期会话需要独立清理，因此分别存集合，避免把会话数组无限追加到用户文档中。用户没有嵌入问卷列表。后续问卷可保存 `owner_id`，但本轮未创建这些集合，也未实现问卷访问控制。

MongoDB 的文档验证、唯一索引和 TTL 索引在这里各有用途，不是把所有内容塞入一个集合。本轮账号本身字段固定，尚不能体现题型嵌套结构的优势；后续设计时，再根据实际需求决定题目和配置的嵌入边界，不把尚未实现的设计当作成果。

## 已落地的 schema

准确、可执行的定义以 [`app/db.py`](../app/db.py) 为准。

### users

| 字段 | BSON 类型 | 用途 |
| --- | --- | --- |
| `_id` | ObjectId | 稳定用户 ID；API 输出字符串 `id` |
| `username` | string | NFKC 规范化并去除两端空白后的显示名，3–32 位 ASCII 字母、数字、下划线 |
| `username_key` | string | 显示名再 casefold，作为不区分大小写的查询和唯一键 |
| `password_hash` | string | 带随机盐的 Argon2id 哈希，不保存明文密码 |
| `created_at` | date | UTC 注册时间 |
| `schema_version` | int，当前为 1 | 标识当前文档结构版本 |

索引：MongoDB 自带 `_id_`；`uq_users_username_key` 唯一索引。

用户名格式是本轮实现选择，作业未规定该格式。比如 ` Alice_01 ` 保存为 `Alice_01`，`alice_01`、`Ａｌｉｃｅ_01` 视为同一账号。输入上限先设为 256 字符，规范化后再检查 3–32 位格式。注册密码为 8–128 个字符且不能全为空白，不自动去除密码空格。登录密码允许 1–128 字符，短的错误密码也会走凭证校验。

重复注册依赖数据库唯一索引处理，不能靠“先查询不存在，再插入”保证并发正确。

### sessions

| 字段 | BSON 类型 | 用途 |
| --- | --- | --- |
| `_id` | ObjectId | 会话文档 ID |
| `user_id` | ObjectId | 指向用户；读取会话后仍检查用户存在 |
| `token_hash` | string | 高熵随机令牌的 SHA-256 十六进制摘要 |
| `created_at` | date | UTC 登录时间 |
| `expires_at` | date | UTC 到期时间 |
| `schema_version` | int，当前为 1 | 当前文档结构版本 |

索引：`uq_sessions_token_hash` 唯一索引；`ttl_sessions_expires_at` 在 `expires_at` 上设置 `expireAfterSeconds: 0`。

登录时通过 `secrets.token_urlsafe(32)` 生成随机 Bearer 令牌，原令牌只在登录响应中返回，库中只存摘要。密码的熵低，使用慢哈希 Argon2id；随机令牌的熵高，使用 SHA-256 查找摘要。这两种用途不可混用。

默认登录有效期 24 小时，可通过环境变量配置。每次鉴权查询都要求 `expires_at > 当前 UTC 时间`，不能等 TTL 后台任务删除文档才认为过期。退出仅删除当前令牌对应会话，不影响同账号其他登录。

所有日期写入 MongoDB 的 BSON date，客户端开启 `tz_aware=True`。BSON 日期精度为毫秒；注册和登录生成时间时也统一到毫秒，确保首次返回与后续读取的时间一致。这是首轮真实测试发现并修复的问题。

两种集合都使用 strict/error JSON Schema，必需字段必须齐全，禁止未知字段。`users` 中直接增加明文 `password` 会被数据库拒绝。schema 不能替代应用业务规则，也不提供跨集合外键约束。

## 初始化与保留数据

运行 `python -m scripts.init_db`，或启动应用，都会检查连接、创建尚不存在的集合并确保索引存在。重复运行保留已有用户和会话；没有启动清库、自动重置、生产种子账号。

已有同名集合的验证器不符合当前版本时，启动明确失败，提示先备份、显式迁移；不悄悄覆盖验证器。`schema_version` 当前只是版本标记，没有声称已经实现迁移框架。未来改 schema，应添加增量迁移并验证旧数据和旧接口。

## 当前 API

默认地址 `http://127.0.0.1:8000`。接口契约也可查看 `/openapi.json` 和 `/docs`。业务接口目前仅有以下四个。

| 方法和路径 | 输入 | 成功输出 | 主要失败状态 |
| --- | --- | --- | --- |
| `POST /api/auth/register` | JSON `username`、`password` | 201：`id`、`username`、`created_at` | 重名 409；格式错误 422 |
| `POST /api/auth/login` | JSON `username`、`password` | 200：`access_token`、`token_type`、`expires_at`、`user` | 凭证错误 401；格式错误 422 |
| `GET /api/auth/me` | `Authorization: Bearer <token>` | 200：当前用户公开字段 | 未授权/过期 401 |
| `POST /api/auth/logout` | 同上 | 204，无响应体 | 未授权/过期 401 |
| `GET /health/live` | 无 | 200：`{"status":"ok"}` | 无数据库查询 |
| `GET /health/ready` | 无 | 200：`{"status":"ok","database":"connected"}` | 数据库异常 503 |

`user` 仅含 `id`、`username`、`created_at`。错误凭证统一返回 401 和 `WWW-Authenticate: Bearer`，不区别账号不存在或密码错误。未知账号也进行一次密码哈希验证，减少明显的耗时差异。响应模型屏蔽内部字段；422 校验响应移除原始输入和异常上下文，避免把密码返回给调用者。登录响应设置 `Cache-Control: no-store`。

数据库请求异常返回简短的 503，不向调用者展示连接串或数据库错误详情。启动时数据库不可用会启动失败；`/health/live` 与 `/health/ready` 是已经成功启动后的检查接口。

没有接收客户端传入的 `user_id` 来决定当前身份。后续受保护的问卷接口应复用 `get_current_user`，再把用户 ID 加入 MongoDB 查询条件。当前测试能验证账号身份隔离，不能替代将来的“只能查看自己的问卷”测试。

## 参考核对

- [FastAPI 官方密码哈希说明](https://fastapi.tiangolo.com/tutorial/security/oauth2-jwt/)：本轮采用其介绍的 pwdlib/Argon2 密码哈希方式；令牌使用 MongoDB 会话，不采用教程的 JWT。
- [PyMongo 官方日期说明](https://www.mongodb.com/docs/languages/python/pymongo-driver/current/data-formats/dates-and-times/)：UTC 和带时区读取。
- [MongoDB 官方 TTL 索引说明](https://www.mongodb.com/docs/manual/core/index-ttl/)：过期删除由后台任务完成，因此鉴权另外检查到期时间。
