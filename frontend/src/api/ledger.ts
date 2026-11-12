import {request,post} from './client';import type {LedgerBatch} from '../types/domain';
export const listBatches=(datasetId:string)=>request<LedgerBatch[]>('/ledger/datasets/'+datasetId+'/batches');
export const getBatch=(batchId:string)=>request<LedgerBatch>('/ledger/batches/'+batchId);
export const submitBatch=(data:unknown)=>post<LedgerBatch>('/ledger/batches',data);
