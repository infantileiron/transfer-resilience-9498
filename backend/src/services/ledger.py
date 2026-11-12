import uuid,math,hashlib
from fastapi import HTTPException
from sqlalchemy import select
from src.models import NetworkDataset,TransferArc,LedgerBatch
from src.services.datasets import require
from src.utils.time import now
from src.utils.serialization import serialize
MAX_CAPACITY=1000000
MAX_MINUTES=1000000
MAX_ARCS=200
def _checksum(records):
 body="|".join("{}:{}:{}:{}:{}".format(r.get("arc_id"),r.get("source"),r.get("target"),r.get("capacity"),r.get("travel_minutes")) if isinstance(r,dict) else repr(r) for r in records)
 return hashlib.sha256(body.encode("utf-8")).hexdigest()
def _validate_record(raw,node_ids):
 # 返回(规整后的字典, None)或(None, 拒绝原因)
 if not isinstance(raw,dict):return None,"每条记录须为JSON对象"
 arc_id=raw.get("arc_id")
 if not isinstance(arc_id,str) or not arc_id.strip():return None,"边ID须为非空字符串"
 arc_id=arc_id.strip()
 if len(arc_id)>60:return None,"边ID长度不能超过60"
 revision=raw.get("revision")
 if not isinstance(revision,int) or isinstance(revision,bool) or revision<0:return None,"修订号须为非负整数"
 source=raw.get("source");target=raw.get("target")
 if not isinstance(source,str) or not source.strip():return None,"源节点须为非空字符串"
 if not isinstance(target,str) or not target.strip():return None,"目标节点须为非空字符串"
 source,target=source.strip(),target.strip()
 if source not in node_ids:return None,"源节点不在网络节点集合中"
 if target not in node_ids:return None,"目标节点不在网络节点集合中"
 capacity=raw.get("capacity")
 if not isinstance(capacity,int) or isinstance(capacity,bool) or capacity<0 or capacity>MAX_CAPACITY:return None,"容量须为0至{}的非负整数".format(MAX_CAPACITY)
 travel=raw.get("travel_minutes")
 if isinstance(travel,bool) or not isinstance(travel,(int,float)) or not math.isfinite(travel) or travel<0 or travel>MAX_MINUTES:return None,"时长须为0至{}的有限非负数".format(MAX_MINUTES)
 return {"arc_id":arc_id,"source":source,"target":target,"capacity":capacity,"travel_minutes":float(travel),"revision":revision},None
def _same_values(a,b):
 return a["source"]==b["source"] and a["target"]==b["target"] and a["capacity"]==b["capacity"] and a["travel_minutes"]==b["travel_minutes"]
def submit_batch(db,payload,actor):
 dataset=require(db,payload.dataset_id)
 checksum=_checksum(payload.records)
 node_ids={n["node_id"] for n in dataset.nodes}
 # 幂等：同(dataset, batch_key)重复投递只生效一次
 existing_batch=db.scalar(select(LedgerBatch).where(LedgerBatch.dataset_id==payload.dataset_id,LedgerBatch.batch_key==payload.batch_key))
 if existing_batch:
  if existing_batch.payload_checksum!=checksum:raise HTTPException(409,"批次号已使用且记录内容不一致，拒绝重复投递")
  result=serialize(existing_batch);result["redelivered"]=True;return result
 # 锁定现有边行，避免并发批次交叉写入
 current=list(db.scalars(select(TransferArc).where(TransferArc.dataset_id==payload.dataset_id).with_for_update()))
 by_id={a.arc_id:a for a in current}
 details=[];any_rejected=False
 # 第一遍：逐条格式校验，按边ID归集（同边多条=重复/乱序记录）
 groups:dict[str,list[dict]]={}
 for index,raw in enumerate(payload.records):
  clean,error=_validate_record(raw,node_ids)
  if error:
   any_rejected=True
   arc_id=raw.get("arc_id") if isinstance(raw,dict) else None
   rev=raw.get("revision") if isinstance(raw,dict) else None
   details.append({"index":index,"arc_id":arc_id if isinstance(arc_id,str) else None,"revision":rev if isinstance(rev,int) and not isinstance(rev,bool) else None,"decision":"rejected","reason":error})
   continue
  details.append({"index":index,"arc_id":clean["arc_id"],"revision":clean["revision"],"decision":None,"reason":"","planned":clean})
  groups.setdefault(clean["arc_id"],[]).append(details[-1])
 # 第二遍：同边ID内按修订号归并，选出本批最高修订记录
 effective:dict[str,dict]={}
 for arc_id,entries in groups.items():
  winner=entries[0]
  for entry in entries[1:]:
   clean,win_clean=entry["planned"],winner["planned"]
   if clean["revision"]<win_clean["revision"]:
    entry["decision"]="ignored";entry["reason"]="同批已含该边更高修订号{}，乱序旧记录不生效".format(win_clean["revision"])
   elif clean["revision"]>win_clean["revision"]:
    if winner["decision"] is None:winner["decision"]="ignored";winner["reason"]="同批已含该边更高修订号{}，乱序旧记录不生效".format(clean["revision"])
    winner=entry
   elif _same_values(clean,win_clean):
    entry["decision"]="ignored";entry["reason"]="同批重复记录（修订号与取值一致），仅保留一条"
   else:
    any_rejected=True
    entry["decision"]="rejected";entry["reason"]="同批同修订号{}取值冲突，无法确定采用哪条记录".format(clean["revision"])
    winner["decision"]="rejected";winner["reason"]="同批同修订号{}取值冲突，无法确定采用哪条记录".format(win_clean["revision"])
  if winner["decision"] is None:effective[arc_id]=winner
 # 第三遍：本批有效记录与台账当前修订号比较
 for arc_id,entry in effective.items():
  clean=entry["planned"];revision=clean["revision"];arc=by_id.get(arc_id)
  if arc is None:
   entry["decision"]="written";entry["reason"]="新边写入"
  elif revision<arc.revision:
   entry["decision"]="ignored";entry["reason"]="修订号{}低于台账当前修订号{}，旧修订不覆盖新值".format(revision,arc.revision)
  elif revision==arc.revision:
   if _same_values(clean,arc.__dict__):
    entry["decision"]="ignored";entry["reason"]="修订号{}内容与台账一致，无需更新".format(revision)
   else:
    any_rejected=True
    entry["decision"]="rejected";entry["reason"]="修订号{}与台账当前修订号相同但取值冲突，须使用更高修订号".format(revision)
  else:
   entry["decision"]="written";entry["reason"]="修订号{}覆盖旧修订号{}".format(revision,arc.revision)
   entry["old"]={"source":arc.source,"target":arc.target,"capacity":arc.capacity,"travel_minutes":arc.travel_minutes,"revision":arc.revision}
 # 新边数量受每网络200条上限约束
 new_arc_ids={d["arc_id"] for d in details if d["decision"]=="written" and d["arc_id"] not in by_id}
 if len(by_id)+len(new_arc_ids)>MAX_ARCS:
  any_rejected=True
  for d in details:
   if d["arc_id"] in new_arc_ids and d["decision"]=="written":d["decision"]="rejected";d["reason"]="写入后边数超过每网络{}条上限，整批拒绝".format(MAX_ARCS)
 details.sort(key=lambda d:d["index"])
 counts={key:sum(1 for d in details if d["decision"]==key) for key in ("written","ignored","rejected")}
 status="rejected" if any_rejected else "applied"
 batch=LedgerBatch(id=str(uuid.uuid4()),dataset_id=payload.dataset_id,batch_key=payload.batch_key,status=status,payload_checksum=checksum,total_records=len(payload.records),written_count=counts["written"],ignored_count=counts["ignored"],rejected_count=counts["rejected"],detail=details,submitted_by=actor,created_at=now())
 db.add(batch);db.flush()
 if status=="applied":
  # 任一坏记录都不会走到这里：全部通过才落台账
  for d in details:
   if d["decision"]!="written":continue
   clean=d["planned"];arc=by_id.get(d["arc_id"])
   if arc is None:
    db.add(TransferArc(id=str(uuid.uuid4()),dataset_id=payload.dataset_id,arc_id=clean["arc_id"],source=clean["source"],target=clean["target"],capacity=clean["capacity"],travel_minutes=clean["travel_minutes"],revision=clean["revision"],created_at=now()))
   else:
    arc.source=clean["source"];arc.target=clean["target"];arc.capacity=clean["capacity"];arc.travel_minutes=clean["travel_minutes"];arc.revision=clean["revision"]
 db.commit()
 result=serialize(batch);result["redelivered"]=False
 if status=="rejected":raise HTTPException(422,result)
 return result
def list_batches(db,dataset_id):
 require(db,dataset_id)
 return [serialize(b) for b in db.scalars(select(LedgerBatch).where(LedgerBatch.dataset_id==dataset_id).order_by(LedgerBatch.created_at.desc()))]
def get_batch(db,batch_id):
 item=db.get(LedgerBatch,batch_id)
 if not item:raise HTTPException(404,"批次不存在")
 return serialize(item)
