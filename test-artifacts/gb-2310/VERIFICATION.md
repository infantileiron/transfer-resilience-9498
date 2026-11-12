# gb-2310 · 边台账按批次更新 —— 核查记录

## 需求映射

| 需求 | 实现 | 核查点 |
|---|---|---|
| 按批次更新已有网络，不再只能新建 | `POST /ledger/batches`（`routes/ledger.py` + `services/ledger.py`），按 `(dataset_id, batch_key)` 投递 | 2/9 组 |
| 按边ID和修订号处理重复、乱序记录 | 三遍处理：逐条校验→同边ID内按修订号归并（高修订胜出、乱序/重复记忽略、同修订冲突拒绝）→与台账修订号比较 | 3 组 |
| 旧修订不覆盖新值 | 记录修订号 `<` 台账修订号 → ignored；`=` 同值忽略/冲突拒绝；`>` 才覆盖并写入新修订号 | 3/4 组 |
| 平行边、反向边各自保留 | 仅按 `(dataset_id, arc_id)` 定位，从不按端点合并；更新只改该 ID 行 | 2 组 |
| 任一坏记录使整批不生效 | 全部记录先在事务内判定，出现任一 rejected 则批次 `status=rejected`、台账零写入（仅批次留痕），返回 422 | 5 组 |
| 重复投递同批只生效一次 | `LedgerBatch` 唯一键 `(dataset_id,batch_key)`；同校验和重投返回首次结果 `redelivered=true`；同键不同内容 409 | 6 组 |
| 推演仍用创建时快照，更新后新建推演才用新台账 | 推演只读 `OutageScenario.arc_snapshot`，批次更新不改写快照；新方案重新 `snapshot()` | 8 组 |
| 页面能查每批写入、忽略和拒绝原因 | `GET /ledger/datasets/{id}/batches`、`GET /ledger/batches/{id}`；容量与时长页新增批次记录区，可展开逐条原因 | 7 组 + 前端构建 |

## 数据结构

- `transfer_arcs` 新增 `revision INTEGER NOT NULL DEFAULT 0`；单条新增/网络导入/种子的历史边均为修订0。
- 新表 `ledger_batches`：批次状态、SHA256 校验和、写入/忽略/拒绝计数、`detail` JSON 逐条原因、提交人、唯一键 `(dataset_id,batch_key)`。
- 旧库无迁移工具，启动时 `main.ensure_schema()` 用 SQLAlchemy 建表并幂等 `ALTER TABLE` 补列（已用旧形态库验证回填为0、重复执行不报错）；评审留痕见 `backend/migrations/002-ledger-batches.sql`。

## 运行方式

```sh
# 后端核查（依赖安装在 /tmp/pylibs，-I 隔离当前目录防同名模块注入）
python3 -I test-artifacts/gb-2310/verify_ledger.py
# 前端类型检查与构建
cd frontend && npm ci && npm run build
```

## 结果

- 后端：65 项断言全部通过，覆盖角色403、新边/平行/反向、修订覆盖、乱序忽略、同修订冲突、9类坏记录整批回滚、同批重复ID与同修订冲突、幂等重投与409、批次查询、快照隔离、200边上限、非对象记录逐条留痕。日志见 `verify-run.log`。
- 旧库迁移：修订列回填0、`ledger_batches` 建表、重复执行幂等。
- 种子：两次 `seed()` 计数一致（22弧全部 revision=0），不重复新增。
- 前端：`vue-tsc --noEmit` 零错误，`vite build` 成功。
