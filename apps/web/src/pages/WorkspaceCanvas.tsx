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
 const[branch,setBranch]=useState(false),[branchAnchorId,setBranchAnchorId]=useState<string|number|undefined>(),[plan,setPlan]=useState<PrunePlan|null>(null),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const[aiStatus,setAiStatus]=useState<AIStatus|null>(null),[canvasDrafts,setCanvasDrafts]=useState<Record<string,string>>({}),[canvasSendingOwner,setCanvasSendingOwner]=useState(''),[canvasReply,setCanvasReply]=useState<{owner:string;state:'idle'|'thinking'|'error';error:string}>({owner:'',state:'idle',error:''});
 const[canvasState,setCanvasState]=useState<PersistedCanvasState>(()=>loadCanvasState(workflowId));
 const request=useRef(0),turnRequest=useRef(0),routeRequest=useRef(0),selectedRef=useRef('');
 const node=useMemo(()=>graph?.nodes.find(item=>item.id===selected),[graph,selected]);
 const turnState=layer.kind==='turn'?canvasState.turns[layer.instanceId]||{collapsedTurnIds:[],positions:{}}:undefined;
 const selectedTurn=useMemo(()=>turnSnapshot?.turns.find(turn=>turn.id===turnState?.selectedTurnId)||null,[turnSnapshot,turnState?.selectedTurnId]);

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
   const persisted=loadCanvasState(workflowId).turns[instanceId]?.selectedTurnId;
   const next=value.turns.some(turn=>turn.id===persisted)?persisted:(value.turns.at(-1)?.id||'');
   updateTurn(instanceId,{selectedTurnId:next});
   return value;
  }catch(caught){if(current===turnRequest.current){setTurnSnapshot(null);setError(caught instanceof ApiError&&caught.status===404?t('backendUpgradeRequired'):caught instanceof Error?caught.message:t('turnLoadFailed'))}return null}
  finally{if(current===turnRequest.current)setTurnLoading(false)}
 },[workflowId,t,updateTurn]);

 useEffect(()=>{request.current++;turnRequest.current++;routeRequest.current++;const persisted=loadCanvasState(workflowId),next=persisted.workflow.selectedId||'';selectedRef.current=next;setCanvasState(persisted);setGraph(null);setSelectedState(next);setRoutes([]);setLayer({kind:'workflow'});setTurnSnapshot(null);setWorkflowFocus(null);setError('')},[workflowId]);
 useEffect(()=>{if(visible)void load()},[visible,load]);
 useEffect(()=>{if(visible)api.aiStatus().then(setAiStatus).catch(()=>setAiStatus(null))},[visible,workflowId]);
 useEffect(()=>{const current=++routeRequest.current;setRoutes([]);if(!node||!graph)return;api.routes(graph.workflowId,node.topicId).then(value=>{if(current===routeRequest.current)setRoutes(value)}).catch(caught=>{if(current===routeRequest.current)setError(caught instanceof Error?caught.message:String(caught))})},[node?.id,node?.topicId,graph?.workflowId]);
 useEffect(()=>{if(!visible||layer.kind!=='turn')return;void loadTurns(layer.instanceId)},[visible,layer.kind,layer.kind==='turn'?layer.instanceId:'',loadTurns]);
 useEffect(()=>{const refresh=()=>{if(!visible)return;void load();if(layer.kind==='turn')void loadTurns(layer.instanceId)};const channel=new BroadcastChannel('conversation-workflow');channel.addEventListener('message',refresh);return()=>channel.close()},[visible,layer,load,loadTurns]);

 function selectNode(id:string){selectedRef.current=id;setSelectedState(id);updateWorkflow({selectedId:id})}
 function openCanvas(id:string){selectNode(id);setLayer({kind:'turn',instanceId:id});setTurnSnapshot(null)}
 function backToWorkflow(){setLayer({kind:'workflow'});setTurnLoading(false);turnRequest.current++}
 function toggleWorkflowCollapse(id:string){const current=canvasState.workflow.collapsedNodeIds,expanding=current.includes(id),next=expanding?current.filter(item=>item!==id):[...current,id];if(!expanding&&graph&&selected!==id&&isDescendant(graph,selected,id))selectNode(id);updateWorkflow({collapsedNodeIds:next})}
 function toggleTurnCollapse(instanceId:string,id:string){const current=canvasState.turns[instanceId]?.collapsedTurnIds||[],next=current.includes(id)?current.filter(item=>item!==id):[...current,id];updateTurn(instanceId,{collapsedTurnIds:next})}

 async function continueConversation(id:string){if(!graph||busy)return;setBusy(true);setError('');try{const result=await api.activate(graph.workflowId,id);if(result.activeInstanceId!==id)throw Error(t('failed'));notifyWorkflowChanged({type:'conversation-workflow-changed',workflowId:graph.workflowId,instanceId:id});await load();onContinue?.()}catch(caught){setError(caught instanceof Error?caught.message:String(caught))}finally{setBusy(false)}}

 async function fork(input:{title:string;topicId?:string;initialMessage?:string}){
  if(!graph||!node)return;setBusy(true);setError('');
  const expected=turnSnapshot?.instanceId===node.id?turnSnapshot.contentRevision:node.contentRevision||0;
  try{
   const exactTurnBranch=branchAnchorId!==undefined&&!!input.initialMessage;
   const result=exactTurnBranch
    ?await api.forkChat(graph.workflowId,node.id,{title:input.title,...(input.topicId?{topicId:input.topicId}:{}),initialMessage:input.initialMessage!,anchorMessageId:branchAnchorId!,expectedContentRevision:expected,idempotencyKey:crypto.randomUUID()})
    :await api.fork(graph.workflowId,node.id,{...input,...(branchAnchorId!==undefined?{anchorMessageId:branchAnchorId}:{}),expectedContentRevision:expected,idempotencyKey:crypto.randomUUID()});
   setBranch(false);setBranchAnchorId(undefined);setWorkflowFocus({id:result.node.id,revision:result.graphRevision});await load();openCanvas(result.node.id);
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
  const instanceId=layer.instanceId,owner=`${graph.workflowId}:${instanceId}`,content=(canvasDrafts[instanceId]||'').trim();
  if(!content)return;
  setCanvasSendingOwner(owner);setCanvasDrafts(current=>({...current,[instanceId]:''}));setError('');
  try{
   let status=aiStatus;
   if(!status){try{status=await api.aiStatus();setAiStatus(status)}catch{status=null}}
   setCanvasReply({owner,state:status?.configured?'thinking':'idle',error:''});
   if(status?.configured)await api.chat(graph.workflowId,instanceId,content);else await api.send(graph.workflowId,instanceId,content);
   notifyWorkflowChanged({type:'conversation-workflow-changed',workflowId:graph.workflowId,instanceId});
   await Promise.all([loadTurns(instanceId),load()]);
   setCanvasReply({owner,state:'idle',error:''});
  }catch(caught){
   await loadTurns(instanceId);
   const message=caught instanceof ApiError&&caught.code==='aiTimeout'?t('aiTimeout'):caught instanceof Error?caught.message:t('canvasSendFailed');
   setCanvasReply({owner,state:'error',error:message});
  }finally{setCanvasSendingOwner('')}
 }

 async function preparePrune(){if(!graph||!node)return;setBusy(true);try{setPlan(await api.prunePlan(graph.workflowId,node.id,node.id===graph.rootInstanceId))}catch(caught){setError(caught instanceof Error?caught.message:String(caught))}finally{setBusy(false)}}
 async function commitPrune(){if(!graph||!node||!plan)return;setBusy(true);try{const result=await api.pruneCommit(graph.workflowId,node.id,plan);setPlan(null);notifyWorkflowChanged({type:'conversation-workflow-changed',workflowId:graph.workflowId,...(result.activeInstanceId?{instanceId:result.activeInstanceId}:{})});backToWorkflow();await load()}catch(caught){setError(caught instanceof Error?caught.message:String(caught))}finally{setBusy(false)}}

 const routeText=(route:Route)=>route.memoryRoute.map(id=>graph?.nodes.find(item=>item.id===id)?.title||id).join(' → ');
 const workflowLabels=useMemo(()=>({locate:t('locateSelection'),fit:t('fitCanvas'),collapse:t('collapseBranch'),expand:t('expandBranch')}),[t]);
 const turnLabels=useMemo(()=>({locate:t('locateSelection'),fit:t('fitCanvas'),collapse:t('collapseTurn'),expand:t('expandTurn'),responses:t('turnResponses'),empty:t('noLocalTurns'),turn:t('turnLabel'),branch:t('branchFromTurn'),statusLabels:{completed:t('statusCompleted'),pending:t('statusPending'),running:t('statusRunning'),failed:t('statusFailed'),interrupted:t('statusInterrupted')},roleLabels:{user:t('roleUser'),assistant:t('roleAssistant'),tool:t('roleTool'),system:t('roleSystem')}}),[t]);
 const turnViewport=(value:CanvasViewport|undefined)=>value;

 if(!workflowId)return <div className="loading">{t('selectWorkflow')}</div>;
 if(!graph)return <div className="loading"><ErrorBanner message={error} onRetry={()=>void load()}/>{error?'':t('loading')}</div>;

 return <main className="workspace-canvas-page">
  <header className="canvas-header">
   <nav className="canvas-breadcrumb" aria-label={t('workflowCanvas')}><button type="button" className={layer.kind==='workflow'?'current':''} onClick={backToWorkflow}>{graph.name}</button>{layer.kind==='turn'&&<><span aria-hidden="true">›</span><strong>{node?.title||layer.instanceId}</strong></>}</nav>
   <p>{layer.kind==='workflow'?t('openTurnCanvas'):t('canvasLocalOnly')}</p>
   <LanguageSelect/>{onClose&&<button type="button" onClick={onClose}>{t('close')}</button>}
  </header>
  <ErrorBanner message={error} onRetry={()=>{setError('');if(layer.kind==='turn')void loadTurns(layer.instanceId);else void load()}}/>
  <section className="canvas-body">
   <div className="canvas-stack">
    <div className={`canvas-layer ${layer.kind==='workflow'?'is-active':''}`} data-testid="workflow-layer" hidden={layer.kind!=='workflow'} aria-hidden={layer.kind!=='workflow'}>
     <WorkflowGraph graph={graph} selectedId={selected} collapsedNodeIds={canvasState.workflow.collapsedNodeIds} nodePositions={canvasState.workflow.positions} initialViewport={canvasState.workflow.viewport} focusRequest={workflowFocus} onSelect={selectNode} onOpenCanvas={openCanvas} onToggleCollapse={toggleWorkflowCollapse} onViewportChange={viewport=>updateWorkflow({viewport})} onNodePositionChange={(id,position)=>updateWorkflow({positions:{...canvasState.workflow.positions,[id]:position}})} labels={workflowLabels}/>
    </div>
    <div className={`canvas-layer ${layer.kind==='turn'?'is-active':''}`} data-testid="turn-layer" hidden={layer.kind!=='turn'} aria-hidden={layer.kind!=='turn'}>
     {turnLoading&&!turnSnapshot?<div className="canvas-loading">{t('loadingTurns')}</div>:turnSnapshot&&layer.kind==='turn'?<TurnCanvas snapshot={turnSnapshot} selectedTurnId={turnState?.selectedTurnId||''} collapsedTurnIds={turnState?.collapsedTurnIds} turnPositions={turnState?.positions} initialViewport={turnViewport(turnState?.viewport)} onSelect={id=>updateTurn(layer.instanceId,{selectedTurnId:id})} onToggleCollapse={id=>toggleTurnCollapse(layer.instanceId,id)} onViewportChange={viewport=>updateTurn(layer.instanceId,{viewport})} onNodePositionChange={(id,position)=>updateTurn(layer.instanceId,{positions:{...(turnState?.positions||{}),[id]:position}})} onBranch={turn=>{updateTurn(layer.instanceId,{selectedTurnId:turn.id});setBranchAnchorId(turn.anchorMessageId);setBranch(true)}} labels={turnLabels}/>:null}
     {layer.kind==='turn'&&<form className="canvas-chat-composer" onSubmit={event=>{event.preventDefault();void sendFromCanvas()}}><div><textarea aria-label={t('canvasMessage')} value={canvasDrafts[layer.instanceId]||''} placeholder={t('canvasMessagePlaceholder')} onChange={event=>setCanvasDrafts(current=>({...current,[layer.instanceId]:event.target.value}))} onKeyDown={event=>{if(event.key==='Enter'&&!event.shiftKey){event.preventDefault();void sendFromCanvas()}}}/>{canvasReply.owner===`${graph.workflowId}:${layer.instanceId}`&&canvasReply.state==='thinking'&&<span role="status">{t('thinking')}…</span>}{canvasReply.owner===`${graph.workflowId}:${layer.instanceId}`&&canvasReply.state==='error'&&<span className="canvas-reply-error" role="alert">{canvasReply.error}</span>}{!aiStatus?.configured&&<small>{t('recordOnly')}</small>}</div><button className="primary" disabled={canvasSendingOwner!==''||!(canvasDrafts[layer.instanceId]||'').trim()}>{t('send')}</button></form>}
    </div>
   </div>
   <aside className="canvas-inspector">
    {layer.kind==='workflow'?<WorkflowInspector/>:<TurnInspector/>}
   </aside>
  </section>
  <BranchDialog open={branch} parentTitle={node?.title||''} busy={busy} requireMessage={branchAnchorId!==undefined} onClose={()=>{setBranch(false);setBranchAnchorId(undefined)}} onSubmit={input=>void fork(input)}/>
  <PruneDialog plan={plan} busy={busy} onClose={()=>setPlan(null)} onCommit={()=>void commitPrune()}/>
 </main>;

 function WorkflowInspector(){return <><h2>{t('details')}</h2>{node&&<><h3>{node.title}</h3><p>{node.summary||'—'}</p><small>{t('route')}</small><p>{routeLabel(graph!,node.id)}</p><h3>{t('routes')}</h3><div className="route-list">{routes.length?routes.map(route=><RouteChoice key={route.id} route={route} label={routeText(route)} selected={route.id===selected} onSelect={()=>selectNode(route.id)} onOpen={()=>openCanvas(route.id)}/>):<p>{t('noRoutes')}</p>}</div><div className="inspector-actions"><button className="primary" disabled={busy||node.status==='pruned'} onClick={()=>void continueConversation(node.id)}>{t('continueConversation')}</button><button disabled={busy||node.status==='pruned'} onClick={()=>openCanvas(node.id)}>{t('turnCanvas')}</button><button disabled={busy||node.status==='pruned'} onClick={()=>{setBranchAnchorId(undefined);setBranch(true)}}>{t('branch')}</button><button onClick={()=>toggleWorkflowCollapse(node.id)}>{canvasState.workflow.collapsedNodeIds.includes(node.id)?t('expandBranch'):t('collapseBranch')}</button><button className="danger-outline" disabled={busy||node.status==='pruned'} onClick={()=>void preparePrune()}>{t('archive')}</button></div></>}</>}

 function TurnInspector(){const instanceId=layer.kind==='turn'?layer.instanceId:'';return <><h2>{t('turnCanvas')}</h2><h3>{node?.title}</h3>{turnSnapshot&&<><small>{t('route')}</small><div className="route-chips">{turnSnapshot.memoryRoute.map(item=><span key={item.instanceId}>{item.title}</span>)}</div><section className="checkpoint-summary"><strong>{t('checkpointSummary')}</strong><p>{t('inheritedCount')}: {turnSnapshot.inheritedMessageCount}</p><small>{t('canvasLocalOnly')}</small></section></>}{selectedTurn&&<TurnDetails turn={selectedTurn}/>}<div className="inspector-actions"><button className="primary" disabled={busy||node?.status==='pruned'} onClick={()=>void continueConversation(instanceId)}>{t('continueConversation')}</button>{selectedTurn&&<button disabled={busy||node?.status==='pruned'} onClick={()=>{setBranchAnchorId(selectedTurn.anchorMessageId);setBranch(true)}}>{t('branchFromTurn')}</button>}<button onClick={backToWorkflow}>{t('backToWorkflow')}</button></div></>}

 function roleLabel(role:string){return role==='user'?t('roleUser'):role==='assistant'?t('roleAssistant'):role==='tool'?t('roleTool'):role==='system'?t('roleSystem'):role}
 function TurnDetails({turn}:{turn:ConversationTurn}){return <section className="turn-details"><h3>{t('selectedTurn')} {turn.sequence}</h3><article className="turn-detail-message user"><small>{roleLabel(turn.userMessage.role)}</small><p>{turn.userMessage.content}</p></article>{turn.responses.map(message=><article className={`turn-detail-message ${message.role}`} key={message.id}><small>{roleLabel(message.role)}</small>{message.role==='assistant'?<MarkdownMessage content={message.content}/>:<p>{message.content}</p>}</article>)}</section>}
}

function isDescendant(graph:Graph,candidateId:string,ancestorId:string){
 const nodes=new Map(graph.nodes.map(item=>[item.id,item]));
 let parent=nodes.get(candidateId)?.parentId||null;
 while(parent){if(parent===ancestorId)return true;parent=nodes.get(parent)?.parentId||null}
 return false;
}
