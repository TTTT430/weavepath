import{useCallback,useEffect,useMemo,useRef,useState}from'react';
import type{AIStatus,ConversationTurn,Graph,PrunePlan,Route,TurnCanvasSnapshot}from'../domain/types';
import{api,ApiError}from'../lib/api';
import{loadCanvasState,saveCanvasState,type CanvasViewport,type PersistedCanvasState}from'../lib/canvasState';
import{routeLabel}from'../domain/graph';
import{useI18n}from'../lib/i18n';
import{notifyWorkflowChanged}from'../lib/workflowEvents';
import{WorkflowGraph,useClickArbitration}from'../components/WorkflowGraph';
import{TurnCanvas}from'../components/TurnCanvas';
import{BranchDialog}from'../components/BranchDialog';
import{PruneDialog}from'../components/PruneDialog';
import{LanguageSelect}from'../components/LanguageSelect';
import{MarkdownMessage}from'../components/MarkdownMessage';
import{ErrorBanner}from'../components/ErrorBanner';

type Layer={kind:'workflow'}|{kind:'turn';instanceId:string};

function RouteChoice({route,label,selected,onSelect,onOpen}:{route:Route;label:string;selected:boolean;onSelect:()=>void;onOpen:()=>void}){const events=useClickArbitration(onSelect,onOpen);return <button className={selected?'current':''}{...events}>{label}</button>}

function CanvasConversationChoice({node,current,onSelect,onOpen}:{node:Graph['nodes'][number];current:boolean;onSelect:()=>void;onOpen:()=>void}){
 const events=useClickArbitration(onSelect,onOpen);
 return <button type="button" className={`${current?'current ':''}${node.status==='pruned'?'is-pruned':''}`.trim()} aria-current={current?'page':undefined} title={node.title}{...events}><i aria-hidden="true"/><span>{node.title}</span></button>;
}

export interface WorkspaceCanvasProps{
 workflowId:string
 visible?:boolean
 onContinue?:()=>void
 onClose?:()=>void
}

export function WorkspaceCanvas({workflowId,visible=true,onContinue,onClose}:WorkspaceCanvasProps){
 const{t}=useI18n();
 const[graph,setGraph]=useState<Graph|null>(null),[selected,setSelectedState]=useState(''),[routes,setRoutes]=useState<Route[]>([]);
 const[layer,setLayer]=useState<Layer>({kind:'workflow'}),[turnSnapshot,setTurnSnapshot]=useState<TurnCanvasSnapshot|null>(null),[turnLoading,setTurnLoading]=useState(false);
 const[workflowFocus,setWorkflowFocus]=useState<{id:string;revision:number}|null>(null);
 const[branch,setBranch]=useState(false),[branchAnchorId,setBranchAnchorId]=useState<string|number|undefined>(),[branchSourceId,setBranchSourceId]=useState(''),[plan,setPlan]=useState<PrunePlan|null>(null),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const[renamingId,setRenamingId]=useState(''),[renameDraft,setRenameDraft]=useState('');
 const[renamingWorkflow,setRenamingWorkflow]=useState(false),[workflowNameDraft,setWorkflowNameDraft]=useState('');
 const[aiStatus,setAiStatus]=useState<AIStatus|null>(null),[canvasDrafts,setCanvasDrafts]=useState<Record<string,string>>({}),[canvasSendingOwner,setCanvasSendingOwner]=useState(''),[canvasReply,setCanvasReply]=useState<{owner:string;state:'idle'|'thinking'|'error';error:string}>({owner:'',state:'idle',error:''});
 const[prunedRouteIds,setPrunedRouteIds]=useState<string[]>([]);
 const[canvasState,setCanvasState]=useState<PersistedCanvasState>(()=>loadCanvasState(workflowId));
 const request=useRef(0),turnRequest=useRef(0),routeRequest=useRef(0),selectedRef=useRef('');
 const node=useMemo(()=>graph?.nodes.find(item=>item.id===selected),[graph,selected]);
 const turnState=layer.kind==='turn'?canvasState.turns[layer.instanceId]||{collapsedTurnIds:[],positions:{}}:undefined;
 const selectedTurn=useMemo(()=>turnSnapshot?.turns.find(turn=>turn.id===turnState?.selectedTurnId)||null,[turnSnapshot,turnState?.selectedTurnId]);
 const selectedRouteId=layer.kind==='turn'?(turnState?.selectedRouteInstanceId||selectedTurn?.routeInstanceId||turnSnapshot?.activeRouteInstanceId||layer.instanceId):'';

 const updateWorkflow=useCallback((patch:Partial<PersistedCanvasState['workflow']>)=>setCanvasState(current=>{const next={...current,workflow:{...current.workflow,...patch}};saveCanvasState(workflowId,next);return next}),[workflowId]);
 const updateTurn=useCallback((instanceId:string,patch:Partial<PersistedCanvasState['turns'][string]>)=>setCanvasState(current=>{const existing=current.turns[instanceId]||{collapsedTurnIds:[],positions:{}},next={...current,turns:{...current.turns,[instanceId]:{...existing,...patch}}};saveCanvasState(workflowId,next);return next}),[workflowId]);

 const load=useCallback(async()=>{
  const current=++request.current;
  if(!workflowId){setGraph(null);return null}
  try{
   const value=await api.graph(workflowId);
   if(current!==request.current)return null;
   setGraph(value);setError('');
   const previous=selectedRef.current,persisted=loadCanvasState(workflowId).workflow.selectedId;
   const next=value.nodes.some(item=>item.id===previous)?previous:value.nodes.some(item=>item.id===persisted)?persisted!:(value.activeInstanceId||value.rootInstanceId);
   selectedRef.current=next;setSelectedState(next);updateWorkflow({selectedId:next});
   return value;
  }catch(caught){if(current===request.current)setError(caught instanceof Error?caught.message:String(caught));return null}
 },[workflowId,updateWorkflow]);

 const loadTurns=useCallback(async(instanceId:string)=>{
  const current=++turnRequest.current;setTurnLoading(true);
  try{
   const value=await api.turns(workflowId,instanceId);
   if(current!==turnRequest.current)return null;
   setTurnSnapshot(value);setError('');
   const persistedState=loadCanvasState(workflowId).turns[instanceId],persisted=persistedState?.selectedTurnId;
   const knownRoutes=new Set(value.routeNodes?.map(route=>route.routeInstanceId)||Object.keys(value.routeTitles||{}));
   const preferredRoute=persistedState?.selectedRouteInstanceId&&knownRoutes.has(persistedState.selectedRouteInstanceId)?persistedState.selectedRouteInstanceId:(value.activeRouteInstanceId||instanceId);
   const activeTurns=value.turns.filter(turn=>(turn.routeInstanceId||instanceId)===preferredRoute);
   const next=value.turns.some(turn=>turn.id===persisted)?persisted:(activeTurns.at(-1)?.id||`route:${preferredRoute}`);
   updateTurn(instanceId,{selectedTurnId:next,selectedRouteInstanceId:preferredRoute});
   return value;
  }catch(caught){if(current===turnRequest.current){setTurnSnapshot(null);setError(caught instanceof ApiError&&caught.status===404?t('backendUpgradeRequired'):caught instanceof Error?caught.message:t('turnLoadFailed'))}return null}
  finally{if(current===turnRequest.current)setTurnLoading(false)}
 },[workflowId,t,updateTurn]);

 useEffect(()=>{request.current++;turnRequest.current++;routeRequest.current++;const persisted=loadCanvasState(workflowId),next=persisted.workflow.selectedId||'';selectedRef.current=next;setCanvasState(persisted);setPrunedRouteIds([]);setGraph(null);setSelectedState(next);setRoutes([]);setLayer({kind:'workflow'});setTurnSnapshot(null);setWorkflowFocus(null);setBranchSourceId('');setRenamingId('');setError('')},[workflowId]);
 useEffect(()=>{if(visible)void load()},[visible,load]);
 useEffect(()=>{if(visible)api.aiStatus().then(setAiStatus).catch(()=>setAiStatus(null))},[visible,workflowId]);
 useEffect(()=>{const current=++routeRequest.current;setRoutes([]);if(!node||!graph)return;api.routes(graph.workflowId,node.topicId).then(value=>{if(current===routeRequest.current)setRoutes(value)}).catch(caught=>{if(current===routeRequest.current)setError(caught instanceof Error?caught.message:String(caught))})},[node?.id,node?.topicId,graph?.workflowId]);
 useEffect(()=>{if(!visible||layer.kind!=='turn')return;void loadTurns(layer.instanceId)},[visible,layer.kind,layer.kind==='turn'?layer.instanceId:'',loadTurns]);
 useEffect(()=>{const refresh=()=>{if(!visible)return;void load();if(layer.kind==='turn')void loadTurns(layer.instanceId)};const channel=new BroadcastChannel('conversation-workflow');channel.addEventListener('message',refresh);return()=>channel.close()},[visible,layer,load,loadTurns]);

 function selectNode(id:string){selectedRef.current=id;setSelectedState(id);updateWorkflow({selectedId:id})}
 function focusWorkflowNode(id:string){selectNode(id);setWorkflowFocus(current=>({id,revision:(current?.revision||0)+1}))}
 function openCanvas(id:string){turnRequest.current++;selectNode(id);setLayer({kind:'turn',instanceId:id});setTurnSnapshot(null);setTurnLoading(false)}
 function switchConversationCanvas(id:string){if(layer.kind==='turn'&&layer.instanceId===id)return;openCanvas(id)}
 function backToWorkflow(){setLayer({kind:'workflow'});setTurnLoading(false);turnRequest.current++}
 function toggleWorkflowCollapse(id:string){const current=canvasState.workflow.collapsedNodeIds,expanding=current.includes(id),next=expanding?current.filter(item=>item!==id):[...current,id];if(!expanding&&graph&&selected!==id&&isDescendant(graph,selected,id))selectNode(id);updateWorkflow({collapsedNodeIds:next})}
 function toggleTurnCollapse(instanceId:string,id:string){const current=canvasState.turns[instanceId]?.collapsedTurnIds||[],next=current.includes(id)?current.filter(item=>item!==id):[...current,id];updateTurn(instanceId,{collapsedTurnIds:next})}

 async function continueConversation(id:string){if(!graph||busy)return;setBusy(true);setError('');try{const result=await api.activate(graph.workflowId,id);if(result.activeInstanceId!==id)throw Error(t('failed'));notifyWorkflowChanged({type:'conversation-workflow-changed',workflowId:graph.workflowId,instanceId:id});await load();onContinue?.()}catch(caught){setError(caught instanceof Error?caught.message:String(caught))}finally{setBusy(false)}}

 async function quickWorkflowBranch(sourceId:string){
  if(!graph||busy)return;
  const source=graph.nodes.find(item=>item.id===sourceId);if(!source)return;
  setBusy(true);setError('');selectNode(sourceId);
  try{
   const result=await api.fork(graph.workflowId,sourceId,{expectedContentRevision:source.contentRevision||0,idempotencyKey:crypto.randomUUID()});
   const refreshed=await load();
   if(refreshed?.nodes.some(item=>item.id===result.node.id)){selectNode(result.node.id);setWorkflowFocus({id:result.node.id,revision:result.graphRevision})}
   notifyWorkflowChanged({type:'conversation-workflow-changed',workflowId:graph.workflowId,instanceId:result.node.id});
  }catch(caught){if(caught instanceof ApiError&&caught.status===409){await load();setError(t('forkConflict'))}else setError(caught instanceof Error?caught.message:String(caught))}finally{setBusy(false)}
 }

 async function quickTurnBranch(turn:ConversationTurn){
  if(!graph||busy||layer.kind!=='turn')return;
  const ownerId=layer.instanceId,sourceId=turn.routeInstanceId||ownerId,expected=turnSnapshot?.routeContentRevisions?.[sourceId]??turnSnapshot?.contentRevision??0;
  setBusy(true);setError('');updateTurn(ownerId,{selectedTurnId:turn.id,selectedRouteInstanceId:sourceId});
  try{
   const result=await api.forkChat(graph.workflowId,sourceId,{anchorMessageId:turn.anchorMessageId,expectedContentRevision:expected,idempotencyKey:crypto.randomUUID()});
   await load();
   const refreshed=await loadTurns(ownerId),childTurn=refreshed?.turns.find(item=>item.routeInstanceId===result.node.id);
   updateTurn(ownerId,{selectedTurnId:childTurn?.id||`route:${result.node.id}`,selectedRouteInstanceId:result.node.id});
   notifyWorkflowChanged({type:'conversation-workflow-changed',workflowId:graph.workflowId,instanceId:result.node.id});
  }catch(caught){if(caught instanceof ApiError&&caught.status===409){await loadTurns(ownerId);setError(t('forkConflict'))}else if(caught instanceof ApiError&&caught.status===404)setError(t('backendUpgradeRequired'));else setError(caught instanceof Error?caught.message:String(caught))}finally{setBusy(false)}
 }

 async function renameInstance(instanceId:string){
  const title=renameDraft.trim();if(!graph||!title||busy)return;
  setBusy(true);setError('');
  try{
   await api.renameInstance(graph.workflowId,instanceId,title,graph.graphRevision);
   setRenamingId('');setRenameDraft('');await load();if(layer.kind==='turn')await loadTurns(layer.instanceId);
   notifyWorkflowChanged({type:'conversation-workflow-changed',workflowId:graph.workflowId,instanceId});
  }catch(caught){if(caught instanceof ApiError&&caught.status===409){await load();if(layer.kind==='turn')await loadTurns(layer.instanceId);setError(t('renameConflict'))}else setError(caught instanceof Error?caught.message:String(caught))}finally{setBusy(false)}
 }

 async function renameWorkflow(){
  const name=workflowNameDraft.trim();if(!graph||!name||busy)return;setBusy(true);setError('');
  try{await api.renameWorkflow(graph.workflowId,name,graph.graphRevision);setRenamingWorkflow(false);setWorkflowNameDraft('');await load();notifyWorkflowChanged({type:'conversation-workflow-changed',workflowId:graph.workflowId})}
  catch(caught){if(caught instanceof ApiError&&caught.status===409){await load();setError(t('renameConflict'))}else setError(caught instanceof Error?caught.message:String(caught))}finally{setBusy(false)}
 }

 async function fork(input:{title:string;topicId?:string;initialMessage?:string}){
  if(!graph||!node)return;setBusy(true);setError('');
  const exactTurnBranch=branchAnchorId!==undefined;
  const sourceId=exactTurnBranch?(branchSourceId||node.id):node.id;
  const expected=exactTurnBranch?(turnSnapshot?.routeContentRevisions?.[sourceId]??turnSnapshot?.contentRevision??node.contentRevision??0):(node.contentRevision||0);
  try{
   const result=exactTurnBranch
    ?await api.forkChat(graph.workflowId,sourceId,{...(input.title?{title:input.title}:{}),...(input.topicId?{topicId:input.topicId}:{}),...(input.initialMessage?{initialMessage:input.initialMessage}:{}),anchorMessageId:branchAnchorId!,expectedContentRevision:expected,idempotencyKey:crypto.randomUUID()})
    :await api.fork(graph.workflowId,node.id,{...(input.title?{title:input.title}:{}),...(input.topicId?{topicId:input.topicId}:{}),...(input.initialMessage?{initialMessage:input.initialMessage}:{}),expectedContentRevision:expected,idempotencyKey:crypto.randomUUID()});
   setBranch(false);setBranchAnchorId(undefined);setBranchSourceId('');
   if(exactTurnBranch&&layer.kind==='turn'){
    await load();const refreshed=await loadTurns(layer.instanceId),childTurn=refreshed?.turns.find(turn=>turn.routeInstanceId===result.node.id);
    updateTurn(layer.instanceId,{selectedTurnId:childTurn?.id||`route:${result.node.id}`,selectedRouteInstanceId:result.node.id});
   }else{
    setWorkflowFocus({id:result.node.id,revision:result.graphRevision});await load();openCanvas(result.node.id);
   }
   const replyResult=result as{replyStatus?:string;replyErrorCode?:string|null};
   if(replyResult.replyStatus==='failed')setError(`${t('branchReplyFailed')}${replyResult.replyErrorCode?` (${replyResult.replyErrorCode})`:''}`);
  }catch(caught){
   if(caught instanceof ApiError&&caught.status===409){const refreshed=layer.kind==='turn'?await loadTurns(layer.instanceId):await load();if(refreshed)setError(t('forkConflict'))}
   else if(caught instanceof ApiError&&caught.status===404)setError(t('backendUpgradeRequired'));
   else setError(caught instanceof Error?caught.message:String(caught));
  }finally{setBusy(false)}
 }

 async function sendFromCanvas(){
  if(layer.kind!=='turn'||!graph||canvasSendingOwner)return;
  const ownerInstanceId=layer.instanceId,instanceId=selectedRouteId||ownerInstanceId,owner=`${graph.workflowId}:${ownerInstanceId}`,content=(canvasDrafts[ownerInstanceId]||'').trim();
  if(!content)return;
  setCanvasSendingOwner(owner);setCanvasDrafts(current=>({...current,[ownerInstanceId]:''}));setError('');
  try{
   let status=aiStatus;
   if(!status){try{status=await api.aiStatus();setAiStatus(status)}catch{status=null}}
   setCanvasReply({owner,state:status?.configured?'thinking':'idle',error:''});
   if(status?.configured)await api.chat(graph.workflowId,instanceId,content);else await api.send(graph.workflowId,instanceId,content);
   notifyWorkflowChanged({type:'conversation-workflow-changed',workflowId:graph.workflowId,instanceId});
   await Promise.all([loadTurns(ownerInstanceId),load()]);
   setCanvasReply({owner,state:'idle',error:''});
  }catch(caught){
   await loadTurns(ownerInstanceId);
   const message=caught instanceof ApiError&&caught.code==='aiTimeout'?t('aiTimeout'):caught instanceof Error?caught.message:t('canvasSendFailed');
   setCanvasReply({owner,state:'error',error:message});
  }finally{setCanvasSendingOwner('')}
 }

 async function preparePrune(targetId=node?.id){if(!graph||!targetId)return;setBusy(true);try{setPlan(await api.prunePlan(graph.workflowId,targetId,targetId===graph.rootInstanceId))}catch(caught){setError(caught instanceof Error?caught.message:String(caught))}finally{setBusy(false)}}
 async function commitPrune(){if(!graph||!node||!plan)return;setBusy(true);try{const targetId=plan.targetInstanceId||node.id,result=await api.pruneCommit(graph.workflowId,targetId,plan);setPrunedRouteIds(current=>[...new Set([...current,...result.prunedInstanceIds])]);setPlan(null);notifyWorkflowChanged({type:'conversation-workflow-changed',workflowId:graph.workflowId,...(result.activeInstanceId?{instanceId:result.activeInstanceId}:{})});if(layer.kind==='turn'&&targetId!==node.id){await loadTurns(layer.instanceId)}else{backToWorkflow();await load()}}catch(caught){setError(caught instanceof Error?caught.message:String(caught))}finally{setBusy(false)}}

 const routeText=(route:Route)=>route.memoryRoute.map(id=>graph?.nodes.find(item=>item.id===id)?.title||id).join(' → ');
 const workflowLabels=useMemo(()=>({locate:t('locateSelection'),fit:t('fitCanvas'),collapse:t('collapseBranch'),expand:t('expandBranch'),branch:t('quickBranch'),openCanvas:t('turnCanvas'),details:t('details'),emptySummary:t('emptyNodeSummary')}),[t]);
 const turnLabels=useMemo(()=>({locate:t('locateSelection'),fit:t('fitCanvas'),collapse:t('collapseTurn'),expand:t('expandTurn'),responses:t('turnResponses'),empty:t('noLocalTurns'),emptyBranch:t('emptyBranchHint'),turn:t('turnLabel'),branch:t('quickBranch'),details:t('details'),statusLabels:{completed:t('statusCompleted'),pending:t('statusPending'),running:t('statusRunning'),failed:t('statusFailed'),interrupted:t('statusInterrupted')},roleLabels:{user:t('roleUser'),assistant:t('roleAssistant'),tool:t('roleTool'),system:t('roleSystem')}}),[t]);
 const turnViewport=(value:CanvasViewport|undefined)=>value;

 if(!workflowId)return <div className="loading">{t('selectWorkflow')}</div>;
 if(!graph)return <div className="loading"><ErrorBanner message={error} onRetry={()=>void load()}/>{error?'':t('loading')}</div>;

 return <main className="workspace-canvas-page">
  <header className="canvas-header">
   <nav className="canvas-breadcrumb" aria-label={t('workflowCanvas')}>{renamingWorkflow?<form onSubmit={event=>{event.preventDefault();void renameWorkflow()}}><input autoFocus aria-label={t('workflowName')} value={workflowNameDraft} maxLength={240} onChange={event=>setWorkflowNameDraft(event.target.value)}/><button type="button" onClick={()=>setRenamingWorkflow(false)}>{t('cancel')}</button><button className="primary" disabled={busy||!workflowNameDraft.trim()}>{t('save')}</button></form>:<><button type="button" className={layer.kind==='workflow'?'current':''} onClick={backToWorkflow}>{graph.name}</button><button type="button" className="breadcrumb-edit" aria-label={t('renameWorkflow')} onClick={()=>{setWorkflowNameDraft(graph.name);setRenamingWorkflow(true)}}>{t('rename')}</button></>}{layer.kind==='turn'&&<><span aria-hidden="true">›</span><strong>{node?.title||layer.instanceId}</strong></>}</nav>
   <p>{layer.kind==='workflow'?t('openTurnCanvas'):t('canvasLocalOnly')}</p>
   <LanguageSelect/>{onClose&&<button type="button" onClick={onClose}>{t('close')}</button>}
  </header>
  <ErrorBanner message={error} onRetry={()=>{setError('');if(layer.kind==='turn')void loadTurns(layer.instanceId);else void load()}}/>
  <section className="canvas-body">
   <aside className="canvas-conversation-sidebar">
    <header><strong>{t('conversations')}</strong><small>{graph.name}</small></header>
    <nav className="canvas-conversation-list" aria-label={t('conversations')}>
     {graph.nodes.map(item=><CanvasConversationChoice key={item.id} node={item} current={(layer.kind==='turn'?layer.instanceId:selected)===item.id} onSelect={layer.kind==='workflow'?()=>focusWorkflowNode(item.id):()=>switchConversationCanvas(item.id)} onOpen={()=>switchConversationCanvas(item.id)}/>) }
    </nav>
   </aside>
   <div className="canvas-stack">
    <div className={`canvas-layer ${layer.kind==='workflow'?'is-active':''}`} data-testid="workflow-layer" hidden={layer.kind!=='workflow'} aria-hidden={layer.kind!=='workflow'}>
     <WorkflowGraph graph={graph} selectedId={selected} collapsedNodeIds={canvasState.workflow.collapsedNodeIds} nodePositions={canvasState.workflow.positions} initialViewport={canvasState.workflow.viewport} focusRequest={workflowFocus} onSelect={selectNode} onOpenCanvas={openCanvas} onBranch={id=>void quickWorkflowBranch(id)} onToggleCollapse={toggleWorkflowCollapse} onViewportChange={viewport=>updateWorkflow({viewport})} onNodePositionChange={(id,position)=>updateWorkflow({positions:{...canvasState.workflow.positions,[id]:position}})} labels={workflowLabels}/>
    </div>
    <div className={`canvas-layer ${layer.kind==='turn'?'is-active':''}`} data-testid="turn-layer" hidden={layer.kind!=='turn'} aria-hidden={layer.kind!=='turn'}>
     {turnLoading&&!turnSnapshot?<div className="canvas-loading">{t('loadingTurns')}</div>:turnSnapshot&&layer.kind==='turn'?<TurnCanvas snapshot={turnSnapshot} hiddenRouteIds={prunedRouteIds} selectedTurnId={turnState?.selectedTurnId||''} collapsedTurnIds={turnState?.collapsedTurnIds} turnPositions={turnState?.positions} initialViewport={turnViewport(turnState?.viewport)} onSelect={(id,routeInstanceId)=>updateTurn(layer.instanceId,{selectedTurnId:id,selectedRouteInstanceId:routeInstanceId})} onToggleCollapse={id=>toggleTurnCollapse(layer.instanceId,id)} onViewportChange={viewport=>updateTurn(layer.instanceId,{viewport})} onNodePositionChange={(id,position)=>updateTurn(layer.instanceId,{positions:{...(turnState?.positions||{}),[id]:position}})} onBranch={turn=>void quickTurnBranch(turn)} labels={turnLabels}/>:null}
     {layer.kind==='turn'&&<form className="canvas-chat-composer" onSubmit={event=>{event.preventDefault();void sendFromCanvas()}}><div><textarea aria-label={t('canvasMessage')} value={canvasDrafts[layer.instanceId]||''} placeholder={t('canvasMessagePlaceholder')} onChange={event=>setCanvasDrafts(current=>({...current,[layer.instanceId]:event.target.value}))} onKeyDown={event=>{if(event.key==='Enter'&&!event.shiftKey){event.preventDefault();void sendFromCanvas()}}}/>{canvasReply.owner===`${graph.workflowId}:${layer.instanceId}`&&canvasReply.state==='thinking'&&<span role="status">{t('thinking')}…</span>}{canvasReply.owner===`${graph.workflowId}:${layer.instanceId}`&&canvasReply.state==='error'&&<span className="canvas-reply-error" role="alert">{canvasReply.error}</span>}{!aiStatus?.configured&&<small>{t('recordOnly')}</small>}</div><button className="primary" disabled={canvasSendingOwner!==''||!(canvasDrafts[layer.instanceId]||'').trim()}>{t('send')}</button></form>}
    </div>
   </div>
   <aside className="canvas-inspector">
    {layer.kind==='workflow'?<WorkflowInspector/>:<TurnInspector/>}
   </aside>
  </section>
  <BranchDialog open={branch} parentTitle={(branchSourceId&&turnSnapshot?.routeTitles?.[branchSourceId])||node?.title||''} busy={busy} onClose={()=>{setBranch(false);setBranchAnchorId(undefined);setBranchSourceId('')}} onSubmit={input=>void fork(input)}/>
  <PruneDialog plan={plan} busy={busy} onClose={()=>setPlan(null)} onCommit={()=>void commitPrune()}/>
 </main>;

 function EditableTitle({instanceId,title}:{instanceId:string;title:string}){const editing=renamingId===instanceId;return <div className="inspector-title">{editing?<form onSubmit={event=>{event.preventDefault();void renameInstance(instanceId)}}><input autoFocus aria-label={t('renameConversation')} value={renameDraft} maxLength={240} onChange={event=>setRenameDraft(event.target.value)}/><button type="button" onClick={()=>{setRenamingId('');setRenameDraft('')}}>{t('cancel')}</button><button className="primary" disabled={busy||!renameDraft.trim()}>{t('save')}</button></form>:<><h3>{title}</h3><button type="button" onClick={()=>{setRenamingId(instanceId);setRenameDraft(title)}}>{t('rename')}</button></>}</div>}

 function WorkflowInspector(){return <><h2>{t('details')}</h2>{node&&<><EditableTitle instanceId={node.id} title={node.title}/><p>{node.summary||'—'}</p><small>{t('route')}</small><p>{routeLabel(graph!,node.id)}</p><h3>{t('routes')}</h3><div className="route-list">{routes.length?routes.map(route=><RouteChoice key={route.id} route={route} label={routeText(route)} selected={route.id===selected} onSelect={()=>selectNode(route.id)} onOpen={()=>openCanvas(route.id)}/>):<p>{t('noRoutes')}</p>}</div><div className="inspector-actions"><button className="primary" disabled={busy||node.status==='pruned'} onClick={()=>void continueConversation(node.id)}>{t('continueConversation')}</button><button disabled={busy||node.status==='pruned'} onClick={()=>openCanvas(node.id)}>{t('turnCanvas')}</button><button disabled={busy||node.status==='pruned'} onClick={()=>{setBranchSourceId('');setBranchAnchorId(undefined);setBranch(true)}}>{t('branchOptions')}</button><button onClick={()=>toggleWorkflowCollapse(node.id)}>{canvasState.workflow.collapsedNodeIds.includes(node.id)?t('expandBranch'):t('collapseBranch')}</button><button className="danger-outline" disabled={busy||node.status==='pruned'} onClick={()=>void preparePrune()}>{t('archive')}</button></div></>}</>}

 function TurnInspector(){const instanceId=layer.kind==='turn'?layer.instanceId:'',routeId=selectedRouteId||instanceId,routeTitle=turnSnapshot?.routeTitles?.[routeId]||node?.title||routeId,memoryRoute=turnSnapshot?.routeMemoryRoutes?.[routeId]||turnSnapshot?.memoryRoute||[],inheritedCount=turnSnapshot?.routeInheritedMessageCounts?.[routeId]??turnSnapshot?.inheritedMessageCount??0;return <><h2>{t('turnCanvas')}</h2><EditableTitle instanceId={routeId} title={routeTitle}/>{turnSnapshot&&<><small>{t('route')}</small><div className="route-chips">{memoryRoute.map(item=><span key={item.instanceId}>{item.title}</span>)}</div><section className="checkpoint-summary"><strong>{t('checkpointSummary')}</strong><p>{t('inheritedCount')}: {inheritedCount}</p><small>{t('canvasLocalOnly')}</small></section></>}{selectedTurn&&<TurnDetails turn={selectedTurn}/>} {!selectedTurn&&routeId!==instanceId&&<p className="empty-route-note">{t('emptyBranchHint')}</p>}<div className="inspector-actions"><button className="primary" disabled={busy||node?.status==='pruned'} onClick={()=>void continueConversation(routeId)}>{t('continueConversation')}</button>{selectedTurn&&<button disabled={busy||node?.status==='pruned'} onClick={()=>{setBranchSourceId(selectedTurn.routeInstanceId||instanceId);setBranchAnchorId(selectedTurn.anchorMessageId);setBranch(true)}}>{t('branchOptions')}</button>}{routeId!==instanceId&&<button className="danger-outline" disabled={busy} onClick={()=>void preparePrune(routeId)}>{t('archive')}</button>}<button onClick={backToWorkflow}>{t('backToWorkflow')}</button></div></>}

 function roleLabel(role:string){return role==='user'?t('roleUser'):role==='assistant'?t('roleAssistant'):role==='tool'?t('roleTool'):role==='system'?t('roleSystem'):role}
 function TurnDetails({turn}:{turn:ConversationTurn}){return <section className="turn-details"><h3>{t('selectedTurn')} {turn.sequence}</h3><article className="turn-detail-message user"><small>{roleLabel(turn.userMessage.role)}</small><p>{turn.userMessage.content}</p></article>{turn.responses.map(message=><article className={`turn-detail-message ${message.role}`} key={message.id}><small>{roleLabel(message.role)}</small>{message.role==='assistant'?<MarkdownMessage content={message.content}/>:<p>{message.content}</p>}</article>)}</section>}
}

function isDescendant(graph:Graph,candidateId:string,ancestorId:string){
 const nodes=new Map(graph.nodes.map(item=>[item.id,item]));
 let parent=nodes.get(candidateId)?.parentId||null;
 while(parent){if(parent===ancestorId)return true;parent=nodes.get(parent)?.parentId||null}
 return false;
}
