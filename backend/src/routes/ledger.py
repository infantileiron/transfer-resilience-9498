from fastapi import APIRouter,Depends
from src.config.database import db_session
from src.controllers.auth import current_user,writer
from src.schemas.ledger import LedgerBatchInput
from src.services import ledger as service
router=APIRouter(prefix="/ledger")
@router.get("/datasets/{dataset_id}/batches")
def listing(dataset_id:str,db=Depends(db_session),user=Depends(current_user)):return service.list_batches(db,dataset_id)
@router.get("/batches/{batch_id}")
def detail(batch_id:str,db=Depends(db_session),user=Depends(current_user)):return service.get_batch(db,batch_id)
@router.post("/batches")
def submit(payload:LedgerBatchInput,db=Depends(db_session),user=Depends(writer)):return service.submit_batch(db,payload,user["email"])
