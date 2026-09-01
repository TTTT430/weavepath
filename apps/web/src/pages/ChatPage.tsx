import{useCallback,useEffect,useMemo,useRef,useState}from'react';
import type{AIStatus,Graph,Message,MessageSnapshot,WorkflowSummary}from'../domain/types';
import{api,ApiError}from'../lib/api';
import{useI18n}from'../lib/i18n';
import{routeLabel}from'../domain/graph';
import{LanguageSelect}from'../components/LanguageSelect';
import{ErrorBanner}from'../components/ErrorBanner';
import{ModelSettingsDialog}from'../components/ModelSettingsDialog';
import{MarkdownMessage}from'../components/MarkdownMessage';
import{AgentRunWorkspace}from'../components/AgentRunWorkspace';

type ReplyState='idle'|'thinking'|'error';
interface OwnedSnapshot extends MessageSnapshot {owner:string}
interface OwnedReply {owner:string;state:ReplyState;error:string}
interface OwnedMessages {owner:string;items:Message[]}

export interface ChatPageProps{
 onOpenWorkflow?:(workflowId:string)=>void
 onWorkspaceChange?:(context:{workflowId:string;graph:Graph|null})=>void
}

export function ChatPage({onOpenWorkflow,onWorkspaceChange}:ChatPageProps={}){
 const{t}=useI18n();
 const[settingsOpen,setSettingsOpen]=useState(false);
 const[workflows,setWorkflows]=useState<WorkflowSummary[]>([]);
 const[workflowId,setWorkflowId]=useState(localStorage.getItem('cw.workflow')||'');
 const[graph,setGraph]=useState<Graph|null>(null);
 const[snapshot,setSnapshot]=useState<OwnedSnapshot>({owner:'',messages:[],contentRevision:0});
 const[inheritedState,setInheritedState]=useState<OwnedMessages>({owner:'',items:[]});
 const[memoryOpenOwner,setMemoryOpenOwner]=useState('');
 const[memoryLoadingOwner,setMemoryLoadingOwner]=useState('');
 const[draft,setDraft]=useState('');
 const[error,setError]=useState('');
 const[workflowBusy,setWorkflowBusy]=useState(false);
 const[pendingOwners,setPendingOwners]=useState<Set<string>>(()=>new Set());
 const[creating,setCreating]=useState(false);
 const[newName,setNewName]=useState('');
 const[rootTitle,setRootTitle]=useState('');
 const[aiStatus,setAiStatus]=useState<AIStatus|null>(null);
 const[reply,setReply]=useState<OwnedReply>({owner:'',state:'idle',error:''});
 const[editingId,setEditingId]=useState('');
 const[editDraft,setEditDraft]=useState('');
 const[copiedId,setCopiedId]=useState('');
 const messagesRef=useRef<HTMLDivElement>(null);
 const graphRequest=useRef(0),memoryRequest=useRef(0);
 const sendLocks=useRef<Set<string>>(new Set());
 const snapshotGenerations=useRef<Map<string,number>>(new Map());
 const snapshotRef=useRef(snapshot),activeKey=useRef('');
 const active=useMemo(()=>graph?.nodes.find(node=>node.id===graph.activeInstanceId),[graph]);
 const owner=graph?.activeInstanceId?`${graph.workflowId}:${graph.activeInstanceId}`:'';
 activeKey.current=owner;
 snapshotRef.current=snapshot;
 const messages=snapshot.owner===owner?snapshot.messages:[];
 const nodeRevision=snapshot.owner===owner?snapshot.contentRevision:active?.contentRevision||0;
 const inherited=inheritedState.owner===owner?inheritedState.items:[];
 const memoryOpen=memoryOpenOwner===owner;
 const memoryLoading=memoryLoadingOwner===owner;
 const replyState=reply.owner===owner?reply.state:'idle';
 const replyError=reply.owner===owner?reply.error:'';
 const busy=workflowBusy||pendingOwners.has(owner);

 const nextSnapshotGeneration=useCallback((targetOwner:string)=>{
  const next=(snapshotGenerations.current.get(targetOwner)||0)+1;
  snapshotGenerations.current.set(targetOwner,next);
  return next;
 },[]);

 const applySnapshot=useCallback((
  targetOwner:string,
  value:MessageSnapshot,
  generation:number,
  minimumRevision=0,
  allowEqualWhilePending=false,
 )=>{
  if(activeKey.current!==targetOwner||snapshotGenerations.current.get(targetOwner)!==generation)return false;
  if(!Number.isFinite(value.contentRevision)||value.contentRevision<minimumRevision)return false;
  const current=snapshotRef.current;
  if(current.owner===targetOwner){
   if(value.contentRevision<current.contentRevision)return false;
   if(!allowEqualWhilePending&&sendLocks.current.has(targetOwner)&&value.contentRevision===current.contentRevision)return false;
  }
  const next:OwnedSnapshot={...value,owner:targetOwner};
  snapshotRef.current=next;
  setSnapshot(next);
  return true;
 },[]);

 const refreshRouteMessages=useCallback(async(
  workflow:string,
  instance:string,
  minimumRevision=0,
  allowEqualWhilePending=false,
 )=>{
  const targetOwner=`${workflow}:${instance}`,generation=nextSnapshotGeneration(targetOwner);
  try{
   const value=await api.messageSnapshot(workflow,instance,'local');
   return applySnapshot(targetOwner,value,generation,minimumRevision,allowEqualWhilePending);
  }catch(caught){
   if(activeKey.current===targetOwner)setError(caught instanceof Error?caught.message:String(caught));
   return false;
  }
 },[applySnapshot,nextSnapshotGeneration]);

 const refreshAI=useCallback(async()=>{
  try{setAiStatus(await api.aiStatus())}
  catch(caught){setError(caught instanceof Error?caught.message:String(caught))}
 },[]);

 const loadWorkflows=useCallback(async()=>{
  try{
   const list=await api.workflows();
   setWorkflows(list);
   if(!list.some(workflow=>workflow.id===workflowId)){
    const next=list[0]?.id||'';
    setWorkflowId(next);
    setGraph(null);
    setError('');
    if(!next)localStorage.removeItem('cw.workflow');
   }
  }catch(caught){setError(caught instanceof Error?caught.message:String(caught))}
 },[workflowId]);

 const loadGraph=useCallback(async()=>{
  const request=++graphRequest.current;
  if(!workflowId){setGraph(null);return}
  try{
   const value=await api.graph(workflowId);
   if(request!==graphRequest.current)return;
   setGraph(value);
   setError('');
   localStorage.setItem('cw.workflow',workflowId);
  }catch(caught){
   if(request!==graphRequest.current)return;
   if(caught instanceof ApiError&&caught.status===404){
    localStorage.removeItem('cw.workflow');
    setWorkflowId('');
    setGraph(null);
    setError('');
    return;
   }
   setError(caught instanceof Error?caught.message:String(caught));
  }
 },[workflowId]);

 useEffect(()=>{void loadWorkflows();void refreshAI()},[loadWorkflows,refreshAI]);
 useEffect(()=>{setGraph(null);graphRequest.current++;void loadGraph()},[loadGraph]);
 useEffect(()=>{
  memoryRequest.current++;
  setMemoryOpenOwner('');
  setMemoryLoadingOwner('');
  setReply({owner,state:'idle',error:''});
  setEditingId('');
  setEditDraft('');
  setCopiedId('');
  if(graph&&active&&owner)void refreshRouteMessages(graph.workflowId,active.id,active.contentRevision||0);
 },[owner,graph?.workflowId,active?.id,active?.contentRevision,refreshRouteMessages]);
 useEffect(()=>{const box=messagesRef.current;if(box)box.scrollTop=box.scrollHeight},[messages,replyState]);
 useEffect(()=>{onWorkspaceChange?.({workflowId,graph})},[workflowId,graph,onWorkspaceChange]);
 useEffect(()=>{
  const refresh=()=>void loadGraph();
  const channel=new BroadcastChannel('conversation-workflow');
  channel.addEventListener('message',refresh);
  const receive=(event:MessageEvent)=>{if(event.data?.type==='conversation-workflow-changed')refresh()};
  window.addEventListener('message',receive);
  return()=>{channel.close();window.removeEventListener('message',receive)};
 },[loadGraph]);

 function lockRoute(targetOwner:string){
  if(sendLocks.current.has(targetOwner))return false;
  sendLocks.current.add(targetOwner);
  setPendingOwners(current=>new Set(current).add(targetOwner));
  return true;
 }

 function unlockRoute(targetOwner:string){
  sendLocks.current.delete(targetOwner);
  setPendingOwners(current=>{const next=new Set(current);next.delete(targetOwner);return next});
 }

 async function toggleMemory(){
  const opening=memoryOpenOwner!==owner;
  setMemoryOpenOwner(opening?owner:'');
  if(!opening||inheritedState.owner===owner||memoryLoadingOwner===owner)return;
  const workflow=graph?.workflowId,instance=graph?.activeInstanceId;
  if(!workflow||!instance)return;
  const request=++memoryRequest.current,targetOwner=`${workflow}:${instance}`;
  setMemoryLoadingOwner(targetOwner);
  try{
   const value=await api.messages(workflow,instance,'effective');
   if(request===memoryRequest.current&&activeKey.current===targetOwner)setInheritedState({owner:targetOwner,items:value.filter(message=>message.inherited)});
  }catch(caught){
   if(request===memoryRequest.current&&activeKey.current===targetOwner)setError(caught instanceof Error?caught.message:String(caught));
  }finally{
   if(request===memoryRequest.current&&activeKey.current===targetOwner)setMemoryLoadingOwner('');
  }
 }

 async function create(){
  if(!newName.trim()||!rootTitle.trim())return;
  setWorkflowBusy(true);
  try{
   const workflow=await api.createWorkflow({name:newName.trim(),rootTitle:rootTitle.trim(),rootTopicId:crypto.randomUUID()});
   setCreating(false);
   setWorkflowId(workflow.workflowId);
   await loadWorkflows();
  }catch(caught){setError(caught instanceof Error?caught.message:String(caught))}
  finally{setWorkflowBusy(false)}
 }

 function aiError(caught:unknown){
  if(caught instanceof ApiError){
   switch(caught.code){
    case'aiTimeout':return t('aiTimeout');
    case'aiUnavailable':return t('aiUnavailable');
    case'aiEmptyResponse':return t('aiEmptyResponse');
    case'validationError':return t('validationError');
    case'conflict':return t('contentConflict');
   }
  }
  return t('aiGenericError');
 }

 function beginEdit(message:Message){setEditingId(String(message.id));setEditDraft(message.content)}
 async function copyMessage(message:Message){
  try{await navigator.clipboard.writeText(message.content);setCopiedId(String(message.id))}
  catch{setCopiedId('')}
 }

 async function regenerate(message:Message){
  const content=editDraft.trim(),workflow=graph?.workflowId,instance=graph?.activeInstanceId;
  if(!content||!workflow||!instance)return;
  const targetOwner=`${workflow}:${instance}`,expected=nodeRevision;
  if(!lockRoute(targetOwner))return;
  nextSnapshotGeneration(targetOwner);
  setEditingId('');
  setReply({owner:targetOwner,state:aiStatus?.configured?'thinking':'idle',error:''});
  try{
   const value=await api.regenerate(workflow,instance,message.id,content,expected);
   const generation=nextSnapshotGeneration(targetOwner);
   applySnapshot(targetOwner,value,generation,expected,true);
   if(activeKey.current===targetOwner)setReply({owner:targetOwner,state:'idle',error:''});
  }catch(caught){
   await refreshRouteMessages(workflow,instance,expected,true);
   if(activeKey.current===targetOwner)setReply({owner:targetOwner,state:'error',error:aiError(caught)});
  }finally{unlockRoute(targetOwner)}
 }

 async function send(){
  const text=draft.trim(),workflow=graph?.workflowId,instance=graph?.activeInstanceId;
  if(!text||!workflow||!instance)return;
  const targetOwner=`${workflow}:${instance}`;
  if(!lockRoute(targetOwner))return;
  nextSnapshotGeneration(targetOwner);
  setDraft('');
  setError('');
  setReply({owner:targetOwner,state:aiStatus?.configured?'thinking':'idle',error:''});
  const current=snapshotRef.current;
  const base=current.owner===targetOwner?current:{owner:targetOwner,messages:[],contentRevision:active?.contentRevision||0};
  const optimistic:OwnedSnapshot={...base,messages:[...base.messages,{id:crypto.randomUUID(),role:'user',content:text,inherited:false}]};
  snapshotRef.current=optimistic;
  setSnapshot(optimistic);
  try{
   if(aiStatus?.configured)await api.chat(workflow,instance,text);
   else await api.send(workflow,instance,text);
   await refreshRouteMessages(workflow,instance,base.contentRevision,true);
   if(activeKey.current===targetOwner)setReply({owner:targetOwner,state:'idle',error:''});
  }catch(caught){
   await refreshRouteMessages(workflow,instance,base.contentRevision,true);
   if(activeKey.current===targetOwner)setReply({owner:targetOwner,state:'error',error:aiError(caught)});
  }finally{unlockRoute(targetOwner)}
 }

 function openGraph(){
  if(!graph)return;
  if(onOpenWorkflow){onOpenWorkflow(graph.workflowId);return}
  const url=new URL('/graph',window.location.href);
  url.searchParams.set('workflow',graph.workflowId);
  const popup=window.open(url.href,'_blank','popup=yes,width=1280,height=820,resizable=yes');
  if(popup){try{popup.location.replace(url.href);popup.focus()}catch{/* The opened page already has the target URL. */}}
  else window.location.assign(url.href);
 }

 const lastUserId=([...messages].reverse().find(message=>message.role==='user'&&!message.inherited)?.id);
 const renderMessage=(message:Message,actions=false)=><article key={message.id} className={`message ${message.role}${actions&&message.id===lastUserId?' actionable':''}`}><div>{editingId===String(message.id)?<div className="message-edit"><label>{t('editQuestionLabel')}<textarea value={editDraft} onChange={event=>setEditDraft(event.target.value)}/></label><div><button type="button" onClick={()=>{setEditingId('');setEditDraft('')}}>{t('cancelEdit')}</button><button type="button" className="primary" disabled={!editDraft.trim()||busy} onClick={()=>void regenerate(message)}>{t('saveRegenerate')}</button></div></div>:<>{message.role==='assistant'?<MarkdownMessage content={message.content}/>:message.content}{actions&&message.id===lastUserId&&<div className="message-actions"><button type="button" onClick={()=>beginEdit(message)}>{t('editQuestion')}</button><button type="button" onClick={()=>void copyMessage(message)}>{copiedId===String(message.id)?t('copied'):t('copyMessage')}</button></div>}</>}</div></article>;
 const memoryPanel=active?.parentId?<section className="inherited-memory"><button type="button" aria-expanded={memoryOpen} onClick={()=>void toggleMemory()}><span>{memoryOpen?'▾':'▸'} {t('inheritedMemory')}</span></button>{memoryOpen&&<div className="inherited-memory-body">{memoryLoading?<p>{t('loadingInherited')}</p>:inherited.length?inherited.map(message=>renderMessage(message)):<p>{t('noInherited')}</p>}</div>}</section>:null;
 const stream=<div className="messages" ref={messagesRef}>{memoryPanel}{!messages.length&&replyState==='idle'&&<p className="empty">{workflowId?t('empty'):t('selectWorkflow')}</p>}{messages.map(message=>renderMessage(message,true))}{replyState==='thinking'&&<article className="message assistant reply-thinking" aria-live="polite"><div><span>{t('thinking')}</span><span className="thinking-dots" aria-hidden="true"><i/><i/><i/></span></div></article>}{replyState==='error'&&<article className="message system reply-error" role="alert"><div>{replyError}</div></article>}</div>;

 return <main className="chat-shell">
  <aside className="sidebar">
   <div className="brand">◫ <strong>{t('app')}</strong></div>
   <button className="new-workflow" onClick={()=>setCreating(true)}>＋ {t('newWorkflow')}</button>
   <h2>{t('conversations')}</h2>
   <nav>{workflows.map(workflow=><button className={workflow.id===workflowId?'current':''} key={workflow.id} onClick={()=>setWorkflowId(workflow.id)}>{workflow.name}</button>)}</nav>
   <div className="sidebar-controls"><button className="settings-button" onClick={()=>setSettingsOpen(true)}>⚙ {t('settings')}</button><LanguageSelect/></div>
  </aside>
  <section className="chat">
   <header>
    <div><h1>{active?.title||graph?.name||t('app')}</h1><p>{t('route')}: {graph&&active?routeLabel(graph,active.id):'—'}</p><span className={`ai-status ${aiStatus?.configured?'connected':'local'}`}>{aiStatus?.configured?`${t('aiConnected')} · ${aiStatus.model}`:t('recordOnly')}</span></div>
    <AgentRunWorkspace graph={graph} active={active} contentRevision={nodeRevision} aiStatus={aiStatus} onRunCompleted={async target=>{await refreshRouteMessages(target.workflowId,target.instanceId,target.inputContentRevision)}}/>
    <button className="workflow-launch" disabled={!graph} onClick={openGraph}>⌘ {t('workflow')}</button>
   </header>
   <ErrorBanner message={error} onRetry={()=>{setError('');void loadGraph()}}/>
   {stream}
   <form className="composer" onSubmit={event=>{event.preventDefault();void send()}}>
    <textarea value={draft} onChange={event=>setDraft(event.target.value)} placeholder={t('placeholder')} onKeyDown={event=>{if(event.key==='Enter'&&!event.shiftKey){event.preventDefault();void send()}}}/>
    <button className="primary" disabled={!draft.trim()||busy}>{t('send')}</button>
   </form>
  </section>
  {settingsOpen&&<ModelSettingsDialog onClose={()=>setSettingsOpen(false)} onSaved={refreshAI}/>}
  {creating&&<div className="modal-backdrop"><form className="modal" onSubmit={event=>{event.preventDefault();void create()}}><h2>{t('newWorkflow')}</h2><label>{t('workflowName')}<input autoFocus required value={newName} onChange={event=>setNewName(event.target.value)}/></label><label>{t('rootTitle')}<input required value={rootTitle} onChange={event=>setRootTitle(event.target.value)}/></label><div className="modal-actions"><button type="button" onClick={()=>setCreating(false)}>{t('cancel')}</button><button className="primary" disabled={workflowBusy}>{t('createOpen')}</button></div></form></div>}
 </main>;
}
