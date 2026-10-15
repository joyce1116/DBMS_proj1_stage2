# 第一阶段里程碑 3：生命周期与迁移实测

日期：2026-10-15。只记录本轮已执行的发布、关闭、截止、登录只读分享和 schema 迁移；不代表问卷填写、跳转、提交、统计或前端已经完成。

## 环境、命令与结果

沿用原环境：Windows PowerShell、Python 3.13.9、MongoDB 6.0.29、FastAPI 0.143.1、PyMongo 4.19.0。固定依赖不变，没有新增安装。MongoDB 地址 `127.0.0.1:27018`，项目数据目录 `.runtime/mongo-data`。

功能接入后的旧用例回归：

```powershell
.\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_security.py tests/test_auth_integration.py tests/test_surveys_integration.py tests/test_survey_validation.py -q
```

实际 **83 passed, 1 warning in 16.77s**。原问卷创建用例的 `schema_version == 1` 随新文档 schema 调整为 `== 2`；其他旧业务断言保留，没有通过删除旧测试来取得回归通过。

最终完整验证：

```powershell
.\.venv\Scripts\python.exe -X utf8 -m pytest -q
.\.venv\Scripts\python.exe -m pip check
```

实际输出：

```text
........................................................................ [ 56%]
.......................................................                  [100%]
127 passed, 1 warning in 35.34s
No broken requirements found.
```

127 项 = 原有 83 项 + 生命周期 36 项 + 迁移 8 项。按方式为 36 项离线校验、91 项真实 MongoDB 集成测试；没有跳过。warning 仍为 Starlette TestClient 提示未来弃用 HTTPX，未屏蔽。新文件为 `tests/test_survey_lifecycle.py` 与 `tests/test_survey_migration.py`。

集成测试沿用随机 `survey_test_<uuid>` 独立库，连接失败会 fail，结束只清理自己的库。生命周期测试使用真实数据库和完整应用；迁移测试先在独立库构造旧 schema，不直接升级开发库。对时间和随机数的控制是为了重复验证边界，不是用内存库伪装数据库测试。

## 生命周期：步骤、输入与实际输出

公共步骤：注册/登录测试账号 → 创建自己的草稿 → 添加合法题目 → 携带当前 revision 调用操作 → 检查 HTTP 和 MongoDB 文档。每例使用独立数据库。

| 场景 | 输入或操作 | 预期 | 实际 |
| --- | --- | --- | --- |
| 草稿初始状态 | 创建空草稿，读详情及本人列表 | draft、is_open=false；share_id/share_url/两时间均 null；schema_version=2 | 一致，通过 |
| 发布保存信息 | 给草稿加合法题目，POST publish 当前 revision | 200；published/open；revision+1；分享 ID/URL；UTC 毫秒发布时间；题目、ID、配置和创建时间不变 | 一致，通过 |
| 空问卷拒绝发布 | 空题目列表发布 | 422，不写入，不生成分享标识 | 一致，通过 |
| 发布时重新判断截止 | 固定当前时间为 deadline 或晚 1ms，再发布 | 均 422，原文档完全不变 | 一致，通过 |
| 发布重验题目 | 在专属测试库写入 schema 允许但业务非法的跨字段或 ID/标签配置，再发布 | 422，不发布也不部分更新 | 一致，通过 |
| 严格版本参数 | 缺版本、0、bool、字符串、float、额外 status 字段 | 422，库不变 | 六个参数化用例通过 |
| 旧版本与错误状态 | 旧 revision 发布/关闭；草稿关闭；二次发布；重复关闭/尝试重开 | 409，原数据与分享 ID 不变 | 一致，通过 |
| 创建者权限 | 用另一用户操作本人 ID、新 ObjectId 或非法 ID | 发布/关闭均 404，不泄露是否为他人问卷 | 三个参数化用例通过 |
| 生命周期及分享未登录 | 缺少或无效 Bearer 调发布、关闭、分享 | 401，使用原账号认证 | 一致，通过 |
| 发布和关闭后冻结 | 对 published/closed 做元数据 PATCH、题目新增/替换/删除、排序 | 合法编辑请求全部 409，原文档不变 | 两个状态均通过 |
| 登录分享最小响应 | owner 与另一登录用户访问链接，分别测试匿名设置 true/false | 200/no-store；仅允许字段；稳定题目 ID；无 owner/share/账号信息；不改变“我的问卷”归属 | 两种设置通过 |
| 未知分享标识 | 合法格式但不存在、格式非法 | 404 | 一致，通过 |
| 截止边界 | 固定时点在截止前 1ms、相等、后 1ms，读分享与管理详情 | 前为 200/open；等于及之后为 410/expired；持久 status 仍 published、revision 和文档不变 | 三个参数化用例通过 |
| 已过期再关闭 | 将检查时点固定为截止时刻，POST close | 200/closed；关闭时间为检查时点，分享仍 410 | 一致，通过 |
| 同版本双发布/双关闭 | 两线程同时发相同版本请求 | 一个 200、一个 409，revision 只加 1 | 两种操作均通过 |
| 发布与编辑竞争 | 两线程以同一 revision 发布和改标题 | 恰好一个成功；发布成功则内容冻结，编辑成功则仍为草稿且未分配链接 | 一致，通过 |
| 分享 ID 碰撞重试 | 固定随机数先返回已用标识，再返回新标识；唯一冲突由真实 MongoDB 产生 | 第二份安全发布，原分享与原文档不变，revision 只增加 1 | 一致，通过 |
| 应用重建 | 关闭旧 TestClient，在同一库重建应用，复用旧 token 与分享链接 | 用户、会话、题目和分享仍有效，库内容不变 | 一致，通过；不是 MongoDB 进程重启测试 |
| 索引/schema/保留 | 多份 share=null 草稿并存；核对索引，初始化两次，尝试复制已用分享标识 | partial unique 正确、重复字符串被拒、无 surveys TTL、原数据不变，状态枚举正确 | 一致，通过 |

36 项包括参数化用例，不把每个断言另计一次。没有把回答校验列入这些结果。

## 迁移测试

迁移 8 项使用真实 MongoDB。测试覆盖：

- 完整 v1 原文档转为 v2 后，只新增三字段并改结构版本，旧 ID、revision、时间、题目和选项逐字段一致；备份保留原文，后续业务编辑后重跑仍不覆盖备份。
- dry-run 完全只读；没有 surveys 的库不初始化、不凭空创建迁移记录。
- 未知 validator、损坏文档、未知 schema_version 三种输入，在迁移写入前拒绝。
- 在测试中让真实数据库只转换部分文档后抛出模拟中断，再执行 dry-run 和续跑，最终完成并保留同一份首次备份。这是主动注入的恢复测试，不是真实开发库发生过中断。
- 未知审计状态、没有本迁移记录的过渡验证器被拒绝。
- 新建 v2 集合无需虚构迁移或备份记录。

迁移代理单独执行为 **8 passed in 0.44s**，之后主代理在上面的 127 项全量测试中再次执行。这里使用真实数据库存储，仅为模拟中断而 patch 一次 update_many 调用；没有模拟文档迁移结果。

## 当前开发库的真实迁移

执行前 API 保持停止，仅 MongoDB 运行。原库为 5 用户、0 会话、2 份 v1 草稿。先运行：

```powershell
.\.venv\Scripts\python.exe -X utf8 -m scripts.migrate_surveys_v2 --dry-run
```

实际输出：

```json
{"backup_collection": null, "dry_run": true, "state": "dry_run", "v1_count": 2, "v2_count": 0}
```

随后正式迁移：

```powershell
.\.venv\Scripts\python.exe -X utf8 -m scripts.migrate_surveys_v2
```

实际 state=completed、converted_count=2。备份集合：

```text
surveys_v1_backup_621f221225da403e89ce2383e6a08397
```

逐文档核对迁移前内存快照、迁移后的原集合及 MongoDB 备份，以下原字段完全一致（只将 schema_version 改为 2 并新增三个 null 字段）：

| 旧问卷 ID | 业务 revision | 题数 | 核验 |
| --- | --- | --- | --- |
| 6ad07be878920ba39e020636 | 9 | 3 | _id/owner/元数据/题序/题目和选项 ID/创建与更新时间均保留 |
| 6ad07c3c78920ba39e02063b | 9 | 3 | 同上，原文备份完整 |

同时对 users、sessions 迁移前后整批文档比较，完全不变；数量仍为 5/0/2。随后初始化安装分享唯一索引，再执行同一迁移脚本，实际返回 already_v2、v1_count=0、v2_count=2，没有新增或覆盖备份。

`schema_migrations` 中保留 `surveys_v1_to_v2` 完成记录。备份集合及审计保留，不属于临时测试库，不应按测试前缀清理。

## 真实 Uvicorn / PowerShell 流程

真正启动 Uvicorn 后，提取运行说明第 8 节 PowerShell 命令执行，使用已验证的 UTF-8 助手，并增加断言检查状态/版本、中文往返、分享字段与关闭后的链接不变。

实际演示用户 `publish_1b6675cfcc`，问卷 `6ad081114d265053ebc1bba3`。登录 token 没有打印或写进记录。

| 操作 | 实际 |
| --- | --- |
| 注册、登录、创建中文问卷并添加文本题 | 成功；题目添加后 revision=2 |
| 发布 | 200，status=published、availability=open、is_open=true、revision=3 |
| 未登录访问匿名问卷分享 | 401 |
| 带登录 token 读取分享 | 200，中文“发布流程演示”和题干正常，只有最小字段 |
| 修改已发布标题 | 409 |
| 关闭 | 200，closed、is_open=false、revision=4，share_url 保持相同 |
| 再读原分享链接 | 410 |
| 退出 | 成功，会话撤销 |

演示截止时间为未来一天，自动化已固定时钟验证精确边界，未让实际 HTTP 流程长时间等待。演示问卷最终为 closed，保留数据，没有产生答案或统计数据。

## 问题、审查和实际限制

本轮测试没有发现需要修改实现的失败用例，不虚构“AI 写错再修好”的经历。实现前确认旧 strict validator 不允许新字段，因此选择显式迁移，而非先把数据写坏后再清库。原 schema 版本断言的调整是模型演进，不是放宽业务要求。

独立代码审查另在自建真实测试库确认分享碰撞三次耗尽返回 503 且草稿不变、8 轮发布/编辑竞争只有一个成功、MongoDB 拒绝缺少关闭时间的 closed 状态。这些补测不另计入 127 项，独立库已删除。

迁移依赖所有应用停止的离线前提；standalone 上不保证跨 collMod 与 update 的事务，不能在线修改同时迁移。本轮还没有网页、填写提交、跳转、匿名/次数策略执行、统计、压力测试或生产部署。

## 结束状态

最终开发库保留 6 个用户、0 个会话、3 份问卷（两份迁移后的旧草稿、一份本轮关闭的演示问卷）。原文备份仍为 2 份，迁移审计 completed；在新增业务数据后重跑迁移仍返回 already_v2。没有遗留 `survey_test_*` 库。

仅停止本轮启动且核对过进程标识的 API 和 MongoDB，8000、27018 均停止监听；MongoDB 正常 shutdown，数据、备份和审计保留。临时 HTTP 检查脚本已清理，正式运行步骤在 `06_run_and_reproduce.md` 第 8 节。
