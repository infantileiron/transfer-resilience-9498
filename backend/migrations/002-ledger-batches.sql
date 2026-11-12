-- 批次台账更新增量结构。初次建表仍由 SQLAlchemy metadata 完成（含下列对象）；
-- 旧库由 src/main.py 的 ensure_schema() 幂等补列，本文件作为评审留痕。
-- 1) transfer_arcs 增加修订号：旧数据视为修订0。
ALTER TABLE transfer_arcs ADD COLUMN revision INTEGER NOT NULL DEFAULT 0;
-- 2) 批次台账：(dataset_id,batch_key) 唯一，重复投递同批次只生效一次；
--    status=applied 时本批才写入 transfer_arcs；rejected 时整批不生效，明细保留拒绝原因。
--    detail(JSON) 逐条记录 written/ignored/rejected 及原因；平行/反向边凭各自 arc_id 独立保留。
CREATE TABLE ledger_batches (
 id VARCHAR(80) PRIMARY KEY,
 dataset_id VARCHAR(80) NOT NULL,
 batch_key VARCHAR(160) NOT NULL,
 status VARCHAR(20) NOT NULL,
 payload_checksum VARCHAR(64) NOT NULL,
 total_records INTEGER NOT NULL,
 written_count INTEGER NOT NULL,
 ignored_count INTEGER NOT NULL,
 rejected_count INTEGER NOT NULL,
 detail JSON NOT NULL,
 submitted_by VARCHAR(160) NOT NULL,
 created_at VARCHAR(64) NOT NULL,
 UNIQUE (dataset_id,batch_key),
 INDEX idx_ledger_batches_dataset (dataset_id,created_at)
);
