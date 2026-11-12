"""批次台账更新端到端核查（SQLite 内存库 + FastAPI TestClient）。

覆盖：新边写入、修订号覆盖、乱序旧修订忽略、同修订冲突整批拒绝、
坏记录整批不生效、平行/反向边保留、批次幂等重投、快照隔离、
批次查询原因、200边上限、角色403。
运行：PYTHONPATH=/tmp/pylibs python3 -I verify_ledger.py
（-I 隔离当前目录，避免同名模块注入）
"""
import os,sys
from types import SimpleNamespace
sys.path.insert(0,"/tmp/pylibs")
BACKEND=os.path.abspath(os.path.join(os.path.dirname(__file__),"..","..","backend"))
sys.path.insert(0,BACKEND)
os.environ["DATABASE_URL"]="sqlite+pysqlite:///:memory:"
os.environ["SEED_DEMO"]="false"
os.environ["REDIS_URL"]="redis://127.0.0.1:6379/0"
os.environ["JWT_SECRET"]="test-secret"

from sqlalchemy import create_engine,select
from sqlalchemy.pool import StaticPool
import src.config.database as dbmod
dbmod.engine=create_engine("sqlite+pysqlite:///:memory:",connect_args={"check_same_thread":False},poolclass=StaticPool)
dbmod.Session.configure(bind=dbmod.engine)

from fastapi.testclient import TestClient
from src.main import app
from src.models import User,NetworkDataset,TransferArc,OutageScenario,LedgerBatch
from src.utils.security import hash_password,token
from src.config.database import Session
from src.services.analysis import analyze

PASS=0;FAIL=0
def check(name,cond,extra=""):
 global PASS,FAIL
 if cond:PASS+=1;print("  PASS",name)
 else:FAIL+=1;print("  FAIL",name,extra)

with TestClient(app) as client:
 with Session() as db:
  for role in ("admin","analyst","viewer"):
   db.add(User(id=role,email=role+"@hengyuan.demo",password=hash_password("x"),role=role))
  db.commit()
 admin=token(SimpleNamespace(id="admin",email="admin@hengyuan.demo",role="admin"))
 analyst=token(SimpleNamespace(id="analyst",email="analyst@hengyuan.demo",role="analyst"))
 viewer=token(SimpleNamespace(id="viewer",email="viewer@hengyuan.demo",role="viewer"))
 H_ADMIN={"Authorization":"Bearer "+admin};H_VIEW={"Authorization":"Bearer "+viewer}

 def login(h):return h
 def post_batch(h,payload):return client.post("/ledger/batches",json=payload,headers=h)
 def make_dataset(name="核查网络"):
  r=client.post("/datasets",headers=H_ADMIN,json={"name":name,"source":"unit.json",
   "nodes":[{"node_id":"S","label":"源"},{"node_id":"A","label":"甲"},{"node_id":"B","label":"乙"},{"node_id":"T","label":"汇"}],
   "arcs":[{"arc_id":"e01","source":"S","target":"A","capacity":5,"travel_minutes":6}]})
  assert r.status_code==200,r.text
  return r.json()["id"]
 def arc_map(ds):
  with Session() as db:return {a.arc_id:a for a in db.scalars(select(TransferArc).where(TransferArc.dataset_id==ds))}

 print("== 1. 角色：viewer 禁止提交，可读批次 ==")
 ds=make_dataset()
 r=post_batch(H_VIEW,{"dataset_id":ds,"batch_key":"b0","records":[{"arc_id":"e01","source":"S","target":"A","capacity":7,"travel_minutes":6,"revision":1}]})
 check("viewer 提交被403",r.status_code==403,r.status_code)
 check("viewer 可查批次列表",client.get(f"/ledger/datasets/{ds}/batches",headers=H_VIEW).status_code==200)

 print("== 2. 批次写入新边（含平行边/反向边，各自保留） ==")
 r=post_batch(H_ADMIN,{"dataset_id":ds,"batch_key":"b1","records":[
  {"arc_id":"e02","source":"S","target":"A","capacity":3,"travel_minutes":9,"revision":0},
  {"arc_id":"e03","source":"A","target":"S","capacity":2,"travel_minutes":4,"revision":0},
  {"arc_id":"e04","source":"A","target":"T","capacity":4,"travel_minutes":2,"revision":0}]})
 check("首批200? 状态200",r.status_code==200,r.text)
 body=r.json()
 check("写入3条",body["written_count"]==3 and body["status"]=="applied",body)
 am=arc_map(ds)
 check("平行边 e01/e02 同时保留",set(["e01","e02"])<=set(am) and am["e01"].target=="A" and am["e02"].target=="A")
 check("反向边 e03(A->S) 独立保留",am["e03"].source=="A" and am["e03"].target=="S")
 check("新边修订号为0",all(am[x].revision==0 for x in ("e02","e03","e04")))
 check("边列表API返回revision",client.get("/arcs",headers=H_ADMIN).json()[0]["revision"]==0)

 print("== 3. 修订号覆盖 + 乱序：旧修订不覆盖新值 ==")
 # 先到 rev3，再乱序补到 rev1/rev2
 r=post_batch(H_ADMIN,{"dataset_id":ds,"batch_key":"b2","records":[{"arc_id":"e01","source":"S","target":"A","capacity":9,"travel_minutes":5,"revision":3}]})
 check("rev3覆盖rev0",r.status_code==200 and arc_map(ds)["e01"].capacity==9 and arc_map(ds)["e01"].revision==3,r.text)
 r=post_batch(H_ADMIN,{"dataset_id":ds,"batch_key":"b3","records":[
  {"arc_id":"e01","source":"S","target":"A","capacity":7,"travel_minutes":8,"revision":1},
  {"arc_id":"e01","source":"S","target":"A","capacity":8,"travel_minutes":7,"revision":2}]})
 body=r.json()
 check("乱序批次整体applied（仅含忽略）",r.status_code==200 and body["status"]=="applied",r.text)
 check("两条旧修订均忽略",body["ignored_count"]==2 and body["written_count"]==0,body)
 e01=arc_map(ds)["e01"]
 check("旧修订未覆盖新值",e01.capacity==9 and e01.travel_minutes==5 and e01.revision==3)
 check("忽略原因落库",all(d["decision"]=="ignored" and ("旧修订" in d["reason"] or "乱序" in d["reason"]) for d in body["detail"]))
 # 同批内同修订号取值冲突 → 整批拒绝
 r=post_batch(H_ADMIN,{"dataset_id":ds,"batch_key":"b3x","records":[
  {"arc_id":"e02","source":"S","target":"A","capacity":4,"travel_minutes":9,"revision":1},
  {"arc_id":"e02","source":"S","target":"A","capacity":5,"travel_minutes":9,"revision":1}]})
 check("同批同修订冲突整批拒绝",r.status_code==422 and arc_map(ds)["e02"].capacity==3,r.text)
 # 同批乱序两修订，高修订胜出并写入
 r=post_batch(H_ADMIN,{"dataset_id":ds,"batch_key":"b3y","records":[
  {"arc_id":"e02","source":"S","target":"A","capacity":6,"travel_minutes":9,"revision":2},
  {"arc_id":"e02","source":"S","target":"A","capacity":4,"travel_minutes":9,"revision":1}]})
 body=r.json()
 check("同批乱序高修订写入、低修订忽略",r.status_code==200 and body["written_count"]==1 and body["ignored_count"]==1,body)
 check("台账取最高修订值",arc_map(ds)["e02"].capacity==6 and arc_map(ds)["e02"].revision==2)

 print("== 4. 同修订号同值忽略、冲突拒绝 ==")
 r=post_batch(H_ADMIN,{"dataset_id":ds,"batch_key":"b4","records":[{"arc_id":"e01","source":"S","target":"A","capacity":9,"travel_minutes":5,"revision":3}]})
 check("同修订同值→忽略且applied",r.status_code==200 and r.json()["ignored_count"]==1,r.text)
 r=post_batch(H_ADMIN,{"dataset_id":ds,"batch_key":"b5","records":[{"arc_id":"e01","source":"S","target":"A","capacity":11,"travel_minutes":5,"revision":3}]})
 check("同修订冲突→422整批拒绝",r.status_code==422,r.status_code)
 detail=r.json()["detail"]
 check("拒绝批次含明细且状态rejected",detail["status"]=="rejected" and detail["rejected_count"]==1,detail)
 check("冲突原因可查", "相同但取值冲突" in detail["detail"][0]["reason"],detail["detail"][0])
 check("冲突未改台账",arc_map(ds)["e01"].capacity==9 and arc_map(ds)["e01"].revision==3)

 print("== 5. 任一坏记录使整批不生效（含本应写入的好记录） ==")
 before=arc_map(ds)
 bad_cases=[
  ("负容量",{"arc_id":"e09","source":"A","target":"T","capacity":-1,"travel_minutes":1,"revision":0}),
  ("分数容量",{"arc_id":"e09","source":"A","target":"T","capacity":1.5,"travel_minutes":1,"revision":0}),
  ("布尔容量",{"arc_id":"e09","source":"A","target":"T","capacity":True,"travel_minutes":1,"revision":0}),
  ("未知端点",{"arc_id":"e09","source":"A","target":"Z","capacity":1,"travel_minutes":1,"revision":0}),
  ("缺边ID",{"source":"A","target":"T","capacity":1,"travel_minutes":1,"revision":0}),
  ("负修订号",{"arc_id":"e09","source":"A","target":"T","capacity":1,"travel_minutes":1,"revision":-2}),
  ("字符串修订号",{"arc_id":"e09","source":"A","target":"T","capacity":1,"travel_minutes":1,"revision":"1"}),
 ]
 for i,(label,bad) in enumerate(bad_cases):
  good={"arc_id":"e10","source":"S","target":"T","capacity":2,"travel_minutes":3,"revision":0}
  r=post_batch(H_ADMIN,{"dataset_id":ds,"batch_key":f"bad{i}","records":[bad,good]})
  check(f"坏记录[{label}]整批422",r.status_code==422,label)
  check(f"坏记录[{label}]好记录未写入","e10" not in arc_map(ds),label)
 # NaN/Inf 是非标准JSON，用原始报文验证服务端拒绝
 import json as _json
 for label,token in (("NaN时长","NaN"),("Inf时长","Infinity")):
  raw=_json.dumps({"dataset_id":ds,"batch_key":"nan-"+label,"records":[{"arc_id":"e09","source":"A","target":"T","capacity":1,"travel_minutes":token,"revision":0}]},allow_nan=True).replace('"'+token+'"',token)
  r=client.post("/ledger/batches",content=raw,headers={**H_ADMIN,"Content-Type":"application/json"})
  check(f"坏记录[{label}]整批422",r.status_code==422,label)
  check(f"坏记录[{label}]零写入","e09" not in arc_map(ds),label)
 check("台账在所有坏批次后保持不变",{k:(v.capacity,v.revision) for k,v in arc_map(ds).items()}=={k:(v.capacity,v.revision) for k,v in before.items()})
 # 记录形态错误（非对象）也逐条拒绝并留痕，而非请求级422
 r=client.post("/ledger/batches",headers=H_ADMIN,json={"dataset_id":ds,"batch_key":"shape1","records":["not-an-object",42]})
 check("非对象记录→422且批次留痕",r.status_code==422 and r.json()["detail"]["rejected_count"]==2,r.text)
 check("非对象拒绝原因可查",all("JSON对象" in d["reason"] for d in r.json()["detail"]["detail"]))
 # 空批次/缺字段仍是请求级422
 check("空批次请求级422",client.post("/ledger/batches",headers=H_ADMIN,json={"dataset_id":ds,"batch_key":"empty","records":[]}).status_code==422)
 check("缺批次号请求级422",client.post("/ledger/batches",headers=H_ADMIN,json={"dataset_id":ds,"records":[{"arc_id":"e1","revision":0}]}).status_code==422)
 # 同批内边ID重复
 r=post_batch(H_ADMIN,{"dataset_id":ds,"batch_key":"dup1","records":[
  {"arc_id":"e11","source":"A","target":"T","capacity":1,"travel_minutes":1,"revision":0},
  {"arc_id":"e11","source":"A","target":"B","capacity":2,"travel_minutes":2,"revision":0}]})
 check("同批重复边ID整批拒绝",r.status_code==422 and "e11" not in arc_map(ds),r.text)

 print("== 6. 幂等：重复投递同批次只生效一次 ==")
 payload={"dataset_id":ds,"batch_key":"idem1","records":[{"arc_id":"e04","source":"A","target":"T","capacity":7,"travel_minutes":2,"revision":2}]}
 r1=post_batch(H_ADMIN,payload);r2=post_batch(H_ADMIN,payload)
 check("首次生效",r1.status_code==200 and r1.json()["redelivered"]==False,r1.text)
 check("重投200且标记redelivered",r2.status_code==200 and r2.json()["redelivered"]==True,r2.text)
 check("重投沿用同一批次ID",r1.json()["id"]==r2.json()["id"])
 check("台账只被写一次",arc_map(ds)["e04"].capacity==7 and arc_map(ds)["e04"].revision==2)
 with Session() as db:
  n=len(list(db.scalars(select(LedgerBatch).where(LedgerBatch.batch_key=="idem1"))))
 check("批次表只有一行",n==1,n)
 r3=post_batch(H_ADMIN,{"dataset_id":ds,"batch_key":"idem1","records":[{"arc_id":"e04","source":"A","target":"T","capacity":8,"travel_minutes":2,"revision":5}]})
 check("同批次号不同内容→409",r3.status_code==409,r3.status_code)
 check("409未改台账",arc_map(ds)["e04"].capacity==7)

 print("== 7. 拒绝批次可在页面查询写入/忽略/拒绝原因 ==")
 r=client.get(f"/ledger/datasets/{ds}/batches",headers=H_ADMIN)
 batches=r.json()
 check("批次列表含拒绝批次",any(b["status"]=="rejected" for b in batches) and any(b["status"]=="applied" for b in batches))
 rejected=next(b for b in batches if b["batch_key"]=="b5")
 r=client.get("/ledger/batches/"+rejected["id"],headers=H_ADMIN)
 check("批次详情可查",r.status_code==200 and r.json()["detail"][0]["decision"]=="rejected")
 check("不存在批次404",client.get("/ledger/batches/nope",headers=H_ADMIN).status_code==404)
 check("未知网络批次列表404",client.get("/ledger/datasets/nope/batches",headers=H_ADMIN).status_code==404)

 print("== 8. 快照隔离：旧推演用创建时快照，新推演才用新台账 ==")
 # 当前 e01 rev3 cap9；先冻结方案
 r=client.post("/scenarios",headers=H_ADMIN,json={"name":"更新前方案","dataset_id":ds,"source_node":"S","sink_node":"T","disabled_arc_ids":[]})
 check("更新前方案创建",r.status_code==200,r.text)
 old_scenario_id=r.json()["id"]
 with Session() as db:
  old_snap={a["arc_id"]:a["capacity"] for a in db.get(OutageScenario,old_scenario_id).arc_snapshot}
 check("旧快照冻结rev3容量9",old_snap["e01"]==9,old_snap)
 # 台账更新到 rev4 cap12
 r=post_batch(H_ADMIN,{"dataset_id":ds,"batch_key":"snap1","records":[{"arc_id":"e01","source":"S","target":"A","capacity":12,"travel_minutes":5,"revision":4}]})
 check("rev4生效",r.status_code==200 and arc_map(ds)["e01"].capacity==12,r.text)
 with Session() as db:
  still=db.get(OutageScenario,old_scenario_id)
  old_snap_after={a["arc_id"]:a["capacity"] for a in still.arc_snapshot}
  old_result=analyze(db.get(NetworkDataset,ds),still)
 check("旧方案快照不随更新变化",old_snap_after["e01"]==9)
 check("旧推演仍基于旧快照",old_result["arcs"] and next(a for a in old_result["arcs"] if a["arc_id"]=="e01")["capacity"]==9)
 r=client.post("/scenarios",headers=H_ADMIN,json={"name":"更新后方案","dataset_id":ds,"source_node":"S","sink_node":"T","disabled_arc_ids":[]})
 new_snap={a["arc_id"]:a["capacity"] for a in r.json()["arc_snapshot"]}
 check("新方案冻结新台账rev4容量12",new_snap["e01"]==12,new_snap)

 print("== 9. 每网络200条边上限 ==")
 ds2=make_dataset("上限网络")
 recs=[{"arc_id":f"x{i:03}","source":"S","target":"T","capacity":1,"travel_minutes":1,"revision":0} for i in range(200)]
 r=post_batch(H_ADMIN,{"dataset_id":ds2,"batch_key":"lim1","records":recs})
 check("已有1边+200新边=201→整批拒绝",r.status_code==422,r.text)
 with Session() as db:
  n=len(list(db.scalars(select(TransferArc).where(TransferArc.dataset_id==ds2))))
 check("超限批次零写入",n==1,n)
 recs199=recs[:199]
 r=post_batch(H_ADMIN,{"dataset_id":ds2,"batch_key":"lim2","records":recs199})
 check("1+199=200→生效",r.status_code==200 and r.json()["written_count"]==199,r.text)

print(f"\n结果：{PASS} 通过 / {FAIL} 失败")
sys.exit(1 if FAIL else 0)
