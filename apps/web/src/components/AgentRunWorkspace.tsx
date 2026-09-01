import {
 useEffect,
 useMemo,
 useRef,
 useState,
 type KeyboardEvent,
 type RefObject,
} from 'react';
import type {AIStatus,AgentRun,AgentRunEvent,Graph,Instance} from '../domain/types';
import {memoryPath} from '../domain/graph';
import {api,ApiError} from '../lib/api';
import {useI18n} from '../lib/i18n';

const EVENT_PAGE_SIZE=100;
const DEFAULT_TOOL={name:'safe_calculator',version:'1.0.0'};
const lines=(value:string)=>value.split(/\r?\n/).map(x=>x.trim()).filter(Boolean);
const show=(value:unknown)=>typeof value==='string'?value:JSON.stringify(value,null,2);
const runKey=(owner:string,runId:string|number)=>`${owner}:${runId}`;

interface RunCompletionTarget {workflowId:string;instanceId:string;inputContentRevision:number}
interface Props {
 graph:Graph|null;
 active?:Instance;
 contentRevision:number;
 aiStatus?:AIStatus|null;
 onRunCompleted?:(target:RunCompletionTarget)=>void|Promise<void>;
}

interface OwnedRuns {owner:string;items:AgentRun[]}
interface OwnedSelection {owner:string;run:AgentRun|null}
interface OwnedEvents {
 owner:string;
 runId:string|number|null;
 items:AgentRunEvent[];
 cursor:number;
 hasMore:boolean;
 loading:boolean;
}

function modelLabel(snapshot:unknown,fallback?:AIStatus|null){
 const value=snapshot&&typeof snapshot==='object'?snapshot as Record<string,unknown>:{};
 const provider=typeof value.provider==='string'?value.provider:fallback?.provider;
 const model=typeof value.model==='string'?value.model:fallback?.model;
 return [provider,model].filter(Boolean).join(' / ')||'—';
}

function focusAfterClose(ref:RefObject<HTMLButtonElement|null>){
 window.setTimeout(()=>ref.current?.focus(),0);
}

function dialogKeys(event:KeyboardEvent<HTMLElement>,close:()=>void){
 if(event.key==='Escape'){
  event.preventDefault();
  close();
  return;
 }
 if(event.key!=='Tab')return;
 const focusable=Array.from(event.currentTarget.querySelectorAll<HTMLElement>(
  'button:not([disabled]), input:not([disabled]), textarea:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])',
 ));
 if(!focusable.length){event.preventDefault();return}
 const first=focusable[0],last=focusable[focusable.length-1];
 if(event.shiftKey&&document.activeElement===first){event.preventDefault();last.focus()}
 else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus()}
}

export function AgentRunWorkspace({graph,active,contentRevision,aiStatus,onRunCompleted}:Props){
 const{t}=useI18n();
 const[briefOwner,setBriefOwner]=useState('');
 const[panelOwner,setPanelOwner]=useState('');
 const[objective,setObjective]=useState('');
 const[constraints,setConstraints]=useState('');
 const[deliverables,setDeliverables]=useState('');
 const[checks,setChecks]=useState('');
 const[confirmed,setConfirmed]=useState(false);
 const[runState,setRunState]=useState<OwnedRuns>({owner:'',items:[]});
 const[selectedState,setSelectedState]=useState<OwnedSelection>({owner:'',run:null});
 const[eventState,setEventState]=useState<OwnedEvents>({owner:'',runId:null,items:[],cursor:0,hasMore:false,loading:false});
 const[routeError,setRouteError]=useState({owner:'',message:''});
 const[listLoadingOwner,setListLoadingOwner]=useState('');
 const[pendingOwners,setPendingOwners]=useState<Set<string>>(()=>new Set());
 const loadGenerations=useRef<Map<string,number>>(new Map()),detailSeq=useRef(0),currentKeyRef=useRef('');
 const createLocks=useRef<Set<string>>(new Set()),eventLocks=useRef<Set<string>>(new Set());
 const idempotency=useRef<Map<string,{signature:string;key:string}>>(new Map());
 const launchRef=useRef<HTMLButtonElement>(null),countRef=useRef<HTMLButtonElement>(null);
 const key=graph&&active?`${graph.workflowId}:${active.id}`:'';
 currentKeyRef.current=key;
 const route=useMemo(()=>graph&&active?memoryPath(graph,active.id):[],[graph,active]);
 const runs=runState.owner===key?runState.items:[];
 const selected=selectedState.owner===key?selectedState.run:null;
 const events=eventState.owner===key&&selected&&String(eventState.runId)===String(selected.runId)?eventState.items:[];
 const error=routeError.owner===key?routeError.message:'';
 const listLoading=listLoadingOwner===key;
 const createPending=pendingOwners.has(key);
 const briefOpen=briefOwner===key&&!!key;
 const panelOpen=panelOwner===key&&!!key;

 function setErrorFor(owner:string,message:string){
  if(currentKeyRef.current===owner)setRouteError({owner,message});
 }

 async function loadRunsFor(workflowId:string,instanceId:string,owner:string,clearError=true){
  const seq=(loadGenerations.current.get(owner)||0)+1;
  loadGenerations.current.set(owner,seq);
  if(currentKeyRef.current===owner){
   setListLoadingOwner(owner);
   if(clearError)setRouteError({owner,message:''});
  }
  try{
   const value=await api.agentRuns(workflowId,instanceId);
   const items=[...value].reverse();
   if(loadGenerations.current.get(owner)===seq&&currentKeyRef.current===owner)setRunState({owner,items});
   return items;
  }catch{
   if(loadGenerations.current.get(owner)===seq)setErrorFor(owner,t('runLoadFailed'));
   return [];
  }finally{
   if(loadGenerations.current.get(owner)===seq&&currentKeyRef.current===owner)setListLoadingOwner('');
  }
 }

 useEffect(()=>{
  detailSeq.current++;
  setRunState({owner:key,items:[]});
  setSelectedState({owner:key,run:null});
  setEventState({owner:key,runId:null,items:[],cursor:0,hasMore:false,loading:false});
  setBriefOwner('');
  setPanelOwner('');
  setConfirmed(false);
  setRouteError({owner:key,message:''});
  if(graph&&active&&key)void loadRunsFor(graph.workflowId,active.id,key);
 },[key]);

 function openBrief(){
  setObjective(active?.title||'');
  setConstraints('');
  setDeliverables('');
  setChecks('');
  setConfirmed(false);
  setRouteError({owner:key,message:''});
  setBriefOwner(key);
 }

 function closeBrief(){setBriefOwner('');focusAfterClose(launchRef)}
 function closePanel(){setPanelOwner('');focusAfterClose(countRef)}

 function addRun(owner:string,run:AgentRun){
  if(currentKeyRef.current!==owner)return;
  setRunState(current=>{
   const items=current.owner===owner?current.items:[];
   return{owner,items:items.some(item=>String(item.runId)===String(run.runId))?items:[run,...items]};
  });
 }

 function creationError(error:unknown){
  const code=error instanceof ApiError?error.code:undefined;
  return code?`${t('runCreateFailed')} ${t('errorCode')}: ${code}`:t('runCreateFailed');
 }

 async function inspect(run:AgentRun,owner=key){
  const seq=++detailSeq.current;
  const lock=runKey(owner,run.runId);
  if(currentKeyRef.current===owner){
   setSelectedState({owner,run});
   setEventState({owner,runId:run.runId,items:[],cursor:0,hasMore:false,loading:true});
   setPanelOwner(owner);
  }
  eventLocks.current.add(lock);
  try{
   const[detail,eventPage]=await Promise.all([
    api.agentRun(run.runId),
    api.agentRunEvents(run.runId,0,EVENT_PAGE_SIZE),
   ]);
   if(seq!==detailSeq.current||currentKeyRef.current!==owner)return;
   const cursor=eventPage.nextAfterSequence??eventPage.events.at(-1)?.sequence??0;
   setSelectedState({owner,run:detail});
   setEventState({
    owner,
    runId:detail.runId,
    items:eventPage.events,
    cursor,
    hasMore:eventPage.events.length===EVENT_PAGE_SIZE&&cursor>0,
    loading:false,
   });
  }catch{
   if(seq===detailSeq.current){
    setErrorFor(owner,t('runLoadFailed'));
    if(currentKeyRef.current===owner)setEventState(current=>({...current,loading:false}));
   }
  }finally{eventLocks.current.delete(lock)}
 }

 async function loadMoreEvents(){
  if(!selected||eventState.owner!==key||!eventState.hasMore||eventState.loading)return;
  const owner=key,runId=selected.runId,after=eventState.cursor,lock=runKey(owner,runId);
  if(eventLocks.current.has(lock))return;
  eventLocks.current.add(lock);
  setEventState(current=>({...current,loading:true}));
  try{
   const page=await api.agentRunEvents(runId,after,EVENT_PAGE_SIZE);
   if(currentKeyRef.current!==owner||selectedState.owner!==owner||String(selectedState.run?.runId)!==String(runId))return;
   const cursor=page.nextAfterSequence??page.events.at(-1)?.sequence??after;
   setEventState(current=>({
    owner,
    runId,
    items:[...current.items,...page.events.filter(item=>!current.items.some(old=>old.sequence===item.sequence))],
    cursor,
    hasMore:page.events.length===EVENT_PAGE_SIZE&&cursor>after,
    loading:false,
   }));
  }catch{
   setErrorFor(owner,t('runLoadFailed'));
   if(currentKeyRef.current===owner)setEventState(current=>({...current,loading:false}));
  }finally{eventLocks.current.delete(lock)}
 }

 async function create(){
  if(!graph||!active||!objective.trim()||!confirmed||createLocks.current.has(key))return;
  const owner=key,workflowId=graph.workflowId,instanceId=active.id,inputContentRevision=contentRevision;
  const input={
   objective:objective.trim(),
   constraints:lines(constraints),
   deliverables:lines(deliverables),
   acceptanceChecks:lines(checks),
   expectedContentRevision:inputContentRevision,
  };
  const signature=JSON.stringify(input);
  const previous=idempotency.current.get(owner);
  const idempotencyKey=previous?.signature===signature?previous.key:crypto.randomUUID();
  idempotency.current.set(owner,{signature,key:idempotencyKey});
  createLocks.current.add(owner);
  setPendingOwners(current=>new Set(current).add(owner));
  setRouteError({owner,message:''});
  try{
   const run=await api.createAgentRun(workflowId,instanceId,{...input,idempotencyKey});
   idempotency.current.delete(owner);
   addRun(owner,run);
   if(currentKeyRef.current===owner){setBriefOwner('');setPanelOwner(owner)}
   if(run.status==='completed')await onRunCompleted?.({workflowId,instanceId,inputContentRevision});
   if(currentKeyRef.current===owner)await inspect(run,owner);
  }catch(caught){
   setErrorFor(owner,creationError(caught));
   const apiError=caught instanceof ApiError?caught:undefined;
   const listed=await loadRunsFor(workflowId,instanceId,owner,false);
   let persisted=apiError?.runId?listed.find(run=>String(run.runId)===String(apiError.runId)):undefined;
   if(!persisted&&apiError?.runId){
    try{persisted=await api.agentRun(apiError.runId)}catch{/* The stable code remains visible even if detail recovery fails. */}
   }
   if(persisted){
    idempotency.current.delete(owner);
    if(currentKeyRef.current===owner){addRun(owner,persisted);setBriefOwner('');setPanelOwner(owner);await inspect(persisted,owner)}
   }
  }finally{
   createLocks.current.delete(owner);
   setPendingOwners(current=>{const next=new Set(current);next.delete(owner);return next});
  }
 }

 const status=(run:AgentRun)=>t(({queued:'runQueued',running:'runRunning',completed:'runCompleted',failed:'runFailed',interrupted:'runInterrupted'}as const)[run.status]||'runFailed');
 const tools=selected?.availableTools?.length?selected.availableTools:[DEFAULT_TOOL];

 return <div className="agent-run-entry">
  <button ref={launchRef} className="agent-run-launch" disabled={!graph||!active||createPending} onClick={openBrief}>▶ {createPending?t('waitingForRun'):t('runWithAgent')}</button>
  <button ref={countRef} className="agent-run-count" disabled={!graph||!active} onClick={()=>setPanelOwner(key)}>{t('runs')} {runs.length}</button>
  {briefOpen&&<div className="modal-backdrop" onMouseDown={event=>{if(event.target===event.currentTarget)closeBrief()}}>
   <section className="modal agent-brief" role="dialog" aria-modal="true" aria-labelledby="agent-brief-title" aria-describedby="agent-brief-provenance" onKeyDown={event=>dialogKeys(event,closeBrief)}>
    <header className="modal-heading"><h2 id="agent-brief-title">{t('executionBrief')}</h2><button type="button" aria-label={t('close')} onClick={closeBrief}>×</button></header>
    <small>{t('concreteRoute')}</small>
    <div className="route-chips">{route.map(node=><span key={node.id} title={node.id}>{node.title}</span>)}</div>
    <dl id="agent-brief-provenance" className="run-provenance">
     <div><dt>{t('workflowId')}</dt><dd>{graph?.workflowId}</dd></div>
     <div><dt>{t('instanceId')}</dt><dd>{active?.id}</dd></div>
     <div><dt>{t('revision')}</dt><dd>{contentRevision}</dd></div>
     <div><dt>{t('adapterModel')}</dt><dd>{modelLabel(undefined,aiStatus)}</dd></div>
     <div><dt>{t('tools')}</dt><dd>{DEFAULT_TOOL.name} {DEFAULT_TOOL.version}</dd></div>
    </dl>
    <label>{t('objective')}<textarea autoFocus value={objective} onChange={event=>setObjective(event.target.value)}/></label>
    <label>{t('constraints')}<textarea placeholder={t('onePerLine')} value={constraints} onChange={event=>setConstraints(event.target.value)}/></label>
    <label>{t('deliverables')}<textarea placeholder={t('onePerLine')} value={deliverables} onChange={event=>setDeliverables(event.target.value)}/></label>
    <label>{t('acceptanceChecks')}<textarea placeholder={t('onePerLine')} value={checks} onChange={event=>setChecks(event.target.value)}/></label>
    <label className="agent-confirm"><input type="checkbox" checked={confirmed} disabled={createPending} onChange={event=>setConfirmed(event.target.checked)}/><span>{t('confirmAgentRun')}</span></label>
    {createPending&&<p className="agent-wait" role="status">{t('runWaitHint')}</p>}
    {error&&<p className="agent-error" role="alert">{error}</p>}
    <div className="modal-actions"><button type="button" onClick={closeBrief}>{createPending?t('close'):t('cancel')}</button><button type="button" className="primary" disabled={createPending||!objective.trim()||!confirmed} onClick={()=>void create()}>{createPending?t('waitingForRun'):t('startRun')}</button></div>
   </section>
  </div>}
  {panelOpen&&<div className="modal-backdrop" onMouseDown={event=>{if(event.target===event.currentTarget)closePanel()}}>
   <section className="modal agent-runs" role="dialog" aria-modal="true" aria-labelledby="agent-runs-title" onKeyDown={event=>dialogKeys(event,closePanel)}>
    <header><h2 id="agent-runs-title">{t('runs')}</h2><button type="button" onClick={()=>graph&&active&&void loadRunsFor(graph.workflowId,active.id,key)} disabled={listLoading}>{t('refresh')}</button><button type="button" aria-label={t('close')} autoFocus onClick={closePanel}>×</button></header>
    {error&&<p className="agent-error" role="alert">{error}</p>}
    <div className="agent-runs-layout">
     <div className="run-list">{!runs.length&&!listLoading&&<p>{t('noRuns')}</p>}{runs.map(run=><button type="button" key={run.runId} className={String(selected?.runId)===String(run.runId)?'current':''} onClick={()=>void inspect(run,key)}><strong>{run.objective}</strong><span className={`run-status ${run.status}`}>{status(run)}</span><small>{t('revision')}: {run.inputContentRevision}</small>{run.errorCode&&<em>{t('errorCode')}: {run.errorCode}</em>}</button>)}</div>
     {selected&&<article className="run-detail">
      <h3>{selected.objective}</h3>
      <p><span className={`run-status ${selected.status}`}>{status(selected)}</span> · {t('revision')}: {selected.inputContentRevision}</p>
      <dl className="run-provenance compact">
       <div><dt>{t('workflowId')}</dt><dd>{selected.workflowId}</dd></div>
       <div><dt>{t('instanceId')}</dt><dd>{selected.instanceId}</dd></div>
       <div><dt>{t('adapterModel')}</dt><dd>{modelLabel(selected.modelSnapshot,aiStatus)}</dd></div>
       <div><dt>{t('tools')}</dt><dd>{tools.map(tool=>`${tool.name} ${tool.version}`).join(', ')}</dd></div>
       {selected.contextSha256&&<div><dt>{t('contextHash')}</dt><dd title={selected.contextSha256}>{selected.contextSha256}</dd></div>}
      </dl>
      <small>{t('concreteRoute')}</small>
      <div className="route-chips">{(selected.memoryRoute?.length?selected.memoryRoute:route.map(node=>({instanceId:node.id,topicId:node.topicId,title:node.title}))).map(node=><span key={node.instanceId} title={node.instanceId}>{node.title}</span>)}</div>
      {selected.errorCode&&<p className="agent-error">{t('errorCode')}: {selected.errorCode}</p>}
      <h4>{t('events')}</h4>
      <ol className="run-events">{events.map(event=><li key={event.sequence}><strong>{event.type}</strong><pre>{show(event.payload)}</pre></li>)}</ol>
      {eventState.owner===key&&String(eventState.runId)===String(selected.runId)&&eventState.hasMore&&<button type="button" disabled={eventState.loading} onClick={()=>void loadMoreEvents()}>{t('loadMoreEvents')}</button>}
      {(selected.finalAnswer||selected.toolResults?.length||selected.finalMessageId)&&<><h4>{t('result')}</h4><pre>{show(selected.finalAnswer||selected.toolResults?.length?selected.finalAnswer||selected.toolResults:{finalMessageId:selected.finalMessageId})}</pre></>}
     </article>}
    </div>
   </section>
  </div>}
 </div>;
}
