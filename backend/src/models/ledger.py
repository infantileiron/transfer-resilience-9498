from sqlalchemy import String,Integer,JSON,ForeignKey,UniqueConstraint,Index
from sqlalchemy.orm import Mapped,mapped_column
from src.config.database import Base
class LedgerBatch(Base):
 __tablename__="ledger_batches"
 __table_args__=(UniqueConstraint("dataset_id","batch_key"),Index("idx_ledger_batches_dataset","dataset_id","created_at"))
 id:Mapped[str]=mapped_column(String(80),primary_key=True)
 dataset_id:Mapped[str]=mapped_column(ForeignKey("network_datasets.id"))
 batch_key:Mapped[str]=mapped_column(String(160))
 status:Mapped[str]=mapped_column(String(20))
 payload_checksum:Mapped[str]=mapped_column(String(64))
 total_records:Mapped[int]=mapped_column(Integer)
 written_count:Mapped[int]=mapped_column(Integer)
 ignored_count:Mapped[int]=mapped_column(Integer)
 rejected_count:Mapped[int]=mapped_column(Integer)
 detail:Mapped[list]=mapped_column(JSON)
 submitted_by:Mapped[str]=mapped_column(String(160))
 created_at:Mapped[str]=mapped_column(String(64))
