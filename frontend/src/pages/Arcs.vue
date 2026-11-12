<script setup lang="ts">
import {ref,computed,watch} from 'vue';
import type {Workspace} from '../stores/workspace';
import type {LedgerBatch} from '../types/domain';
import PageHeading from '../components/common/PageHeading.vue';
import Notice from '../components/common/Notice.vue';
import {create} from '../api/arcs';
import {listBatches,submitBatch} from '../api/ledger';
import {useAsync} from '../hooks/useAsync';
const props=defineProps<{data:Workspace;canWrite:boolean;refresh:()=>Promise<void>}>();
const dataset=ref(''),arcId=ref(''),source=ref(''),target=ref(''),capacity=ref(1),minutes=ref(1);
const ds=computed(()=>props.data.datasets.find(d=>d.id===dataset.value)||props.data.datasets[0]);
const arcs=computed(()=>props.data.arcs.filter(a=>a.dataset_id===ds.value?.id).sort((a,b)=>a.arc_id.localeCompare(b.arc_id)));
const {busy,error,execute}=useAsync();
async function submit(){
 await execute(async()=>{
  await create({dataset_id:ds.value?.id,arc_id:arcId.value,source:source.value||ds.value?.nodes[0]?.node_id,target:target.value||ds.value?.nodes[ds.value.nodes.length-1]?.node_id,capacity:capacity.value,travel_minutes:minutes.value});
  arcId.value='';await props.refresh();
 });
}
// 边台账批次更新
const batchKey=ref('');
const recordsText=ref('[{"arc_id":"e01","source":"S","target":"A","capacity":9,"travel_minutes":5.5,"revision":1}]');
const batches=ref<LedgerBatch[]>([]);
const expanded=ref<Record<string,boolean>>({});
const batchBusy=ref(false);
const batchError=ref('');
const batchInfo=ref('');
async function loadBatches(){
 if(!ds.value){batches.value=[];return;}
 try{batches.value=await listBatches(ds.value.id);}catch(e){batchError.value=(e as Error).message;}
}
watch(ds,loadBatches,{immediate:true});
function toggle(id:string){expanded.value[id]=!expanded.value[id];}
async function submitLedger(){
 batchError.value='';batchInfo.value='';
 let records:unknown;
 try{records=JSON.parse(recordsText.value);}
 catch(e){batchError.value='JSON解析失败：'+(e as Error).message;return;}
 if(!Array.isArray(records)||records.length===0){batchError.value='批次须为非空记录数组';return;}
 if(!batchKey.value.trim()){batchError.value='请填写运营批次号（同批次重复投递只生效一次）';return;}
 batchBusy.value=true;
 try{
  const result=await submitBatch({dataset_id:ds.value?.id,batch_key:batchKey.value.trim(),records});
  batchInfo.value=`批次已生效：写入${result.written_count}条 / 忽略${result.ignored_count}条 / 拒绝${result.rejected_count}条${result.redelivered?'（重复投递，沿用首次结果，未重复生效）':''}`;
  batchKey.value='';
  await Promise.all([loadBatches(),props.refresh()]);
 }catch(e){
  const raw=(e as Error).message;
  try{
   const parsed=JSON.parse(raw) as LedgerBatch;
   if(parsed&&parsed.id){
    batchError.value=`批次被整批拒绝（任一坏记录使整批不生效）：写入0 / 忽略${parsed.ignored_count} / 拒绝${parsed.rejected_count}，逐条原因见下方批次记录。`;
    batchKey.value='';
    await Promise.all([loadBatches(),props.refresh()]);
   }else batchError.value=raw;
  }catch{batchError.value=raw;}
 }finally{batchBusy.value=false;}
}
</script>
<template>
 <PageHeading code="03 / CAPACITY & TIME" title="同向平行边，也要逐条入账。" description="容量为非负整数箱/小时，时长为非负分钟。边按修订号更新，旧修订不覆盖新值；平行边、反向边各自保留独立ID。"/>
 <section class="paper">
  <div class="section-heading">
   <h2>逐边容量台账</h2>
   <select aria-label="边台账网络" :value="ds?.id" @change="dataset=($event.target as HTMLSelectElement).value;source='';target=''">
    <option v-for="d in data.datasets" :key="d.id" :value="d.id">{{d.name}}</option>
   </select>
  </div>
  <div class="table-scroll">
   <table>
    <thead><tr><th>独立边ID</th><th>源 → 目标</th><th>容量 / 箱每小时</th><th>时长 / 分钟</th><th>当前修订号</th></tr></thead>
    <tbody>
     <tr v-for="a in arcs" :key="a.id">
      <td><b>{{a.arc_id}}</b></td>
      <td>{{a.source}} → {{a.target}}</td>
      <td>{{a.capacity}}</td>
      <td>{{a.travel_minutes.toFixed(3)}}</td>
      <td>r{{a.revision}}</td>
     </tr>
    </tbody>
   </table>
  </div>
 </section>
 <section class="paper" v-if="canWrite">
  <h2>按批次补发更新台账</h2>
  <p class="caption">同一运营批次按批次号识别：重复投递同批次只生效一次。每条记录带修订号，乱序到达也以修订号为准——旧修订不覆盖新值；同修订号取值冲突会拒绝该记录；平行边、反向边凭各自边ID独立保留。任一坏记录都会使整批不生效（拒绝批次仍可在下方查询原因）。更新不影响已冻结推演，只有更新后新建的推演才使用新台账。</p>
  <form class="grid-form" @submit.prevent="submitLedger">
   <label>运营批次号（幂等键）<input aria-label="运营批次号" required v-model="batchKey" placeholder="例如 20261112-am-01"/></label>
   <label class="wide">补发边记录JSON（含revision修订号）
    <textarea rows="5" required v-model="recordsText" :placeholder='`[{"arc_id":"e01","source":"S","target":"A","capacity":9,"travel_minutes":5.5,"revision":1}, …]`'></textarea>
   </label>
   <Notice v-if="batchError" error>{{batchError}}</Notice>
   <Notice v-else-if="batchInfo">{{batchInfo}}</Notice>
   <button :disabled="batchBusy">核查并提交批次</button>
  </form>
 </section>
 <section class="paper">
  <div class="section-heading">
   <h2>批次写入 / 忽略 / 拒绝记录</h2>
   <span>仅当前网络 · 点开查看逐条原因</span>
  </div>
   <p v-if="batches.length===0" class="caption">该网络还没有补发批次。</p>
   <div v-for="b in batches" :key="b.id" class="batch-record">
    <div class="batch-head" @click="toggle(b.id)">
      <div>
       <b>{{b.batch_key}}</b>
       <span :class="['status',b.status]">{{b.status==='applied'?'已生效':'整批拒绝'}}</span>
       <span v-if="b.redelivered" class="tag warning">重复投递</span>
      </div>
      <div class="batch-counts">
       <span class="tag pass">写入 {{b.written_count}}</span>
       <span class="tag warning">忽略 {{b.ignored_count}}</span>
       <span class="tag cut">拒绝 {{b.rejected_count}}</span>
       <small>{{b.created_at}} · {{b.submitted_by}} · 共{{b.total_records}}条</small>
      </div>
     </div>
     <div v-if="expanded[b.id]" class="audit-details">
      <div class="table-scroll">
       <table>
        <thead><tr><th>#</th><th>边ID</th><th>修订号</th><th>处理</th><th>原因</th></tr></thead>
        <tbody>
         <tr v-for="d in b.detail" :key="d.index" :class="{disabled:d.decision==='ignored'}">
          <td>{{d.index+1}}</td>
          <td><b>{{d.arc_id ?? '—'}}</b></td>
          <td>{{d.revision ?? '—'}}</td>
          <td><span :class="['tag',d.decision==='written'?'pass':d.decision==='rejected'?'cut':'warning']">{{d.decision==='written'?'写入':d.decision==='ignored'?'忽略':'拒绝'}}</span></td>
          <td>{{d.reason}}</td>
         </tr>
        </tbody>
       </table>
      </div>
     </div>
   </div>
 </section>
 <section class="paper" v-if="canWrite">
  <h2>新增一条独立边</h2>
  <form class="arc-form" @submit.prevent="submit">
   <label>新边ID<input aria-label="新边ID" required v-model="arcId"/></label>
   <label>源节点<select aria-label="边源节点" :value="source||ds?.nodes[0]?.node_id" @change="source=($event.target as HTMLSelectElement).value"><option v-for="n in ds?.nodes" :key="n.node_id" :value="n.node_id">{{n.node_id}} · {{n.label}}</option></select></label>
   <label>目标节点<select aria-label="边目标节点" :value="target||ds?.nodes[ds.nodes.length-1]?.node_id" @change="target=($event.target as HTMLSelectElement).value"><option v-for="n in ds?.nodes" :key="n.node_id" :value="n.node_id">{{n.node_id}} · {{n.label}}</option></select></label>
   <label>容量 / 箱每小时<input aria-label="边容量" type="number" min="0" max="1000000" step="1" v-model.number="capacity"/></label>
   <label>时长 / 分钟<input aria-label="边时长" type="number" min="0" max="1000000" step="any" v-model.number="minutes"/></label>
   <button :disabled="busy">保存独立边</button>
  </form>
  <Notice v-if="error" error>{{error}}</Notice>
  <p class="caption">新边修订号从0开始；新增边不会改变已锁定的推演快照，如需纳入比较请新建停用方案。端点相同也不得复用旧边ID。</p>
 </section>
</template>
