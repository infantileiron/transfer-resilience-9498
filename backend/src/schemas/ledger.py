from typing import Any
from pydantic import BaseModel,Field
class LedgerBatchInput(BaseModel):
 dataset_id:str=Field(min_length=1,max_length=80)
 batch_key:str=Field(min_length=1,max_length=160)
 # 宽进严校验：记录形态错误也在服务端逐条记为拒绝原因，而非整请求422
 records:list[Any]=Field(min_length=1,max_length=500)
