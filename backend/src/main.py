from contextlib import asynccontextmanager
import logging,redis
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import text,inspect
from src.config.database import engine
from src.config.settings import SEED_DEMO,REDIS_URL
from src.middlewares.errors import ErrorMiddleware
from src.middlewares.audit import AuditMiddleware
from src.routes import auth,datasets,arcs,ledger,scenarios,runs,reviews,reports,audit
def ensure_schema():
 # 无正式迁移工具：首次由SQLAlchemy建表，旧库仅做增量补列（幂等）
 from src.config.database import Base
 from src import models  # noqa:F401 注册全部模型
 Base.metadata.create_all(engine)
 inspector=inspect(engine)
 arc_columns={c["name"] for c in inspector.get_columns("transfer_arcs")}
 with engine.begin() as conn:
  if "revision" not in arc_columns:conn.execute(text("ALTER TABLE transfer_arcs ADD COLUMN revision INTEGER NOT NULL DEFAULT 0"))
@asynccontextmanager
async def lifespan(app):
 ensure_schema()
 if SEED_DEMO:
  try:
   from scripts.seed_demo import seed
   seed()
  except Exception:logging.exception("seed failed")
 yield
app=FastAPI(title="衡远 · 中转网络韧性推演",lifespan=lifespan)
@app.exception_handler(RequestValidationError)
async def invalid_input(request,error):
 details=[".".join(str(x) for x in item["loc"])+": "+item["msg"] for item in error.errors()]
 return JSONResponse({"detail":"输入校验失败："+"；".join(details)},status_code=422)
app.add_middleware(AuditMiddleware);app.add_middleware(ErrorMiddleware)
for module in (auth,datasets,arcs,ledger,scenarios,runs,reviews,reports,audit):app.include_router(module.router)
@app.get("/health")
def health():
 with engine.connect() as db:db.execute(text("SELECT 1"))
 redis.Redis.from_url(REDIS_URL).ping();return {"status":"ok"}
