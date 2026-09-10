import{useCallback,useEffect,useMemo,useRef,useState}from'react';
import type{AIStatus,ChatResponseDetails,Graph,Message,MessageSnapshot,UploadedAttachment,WorkflowSummary}from'../domain/types';
import{api,ApiError}from'../lib/api';
import{useI18n}from'../lib/i18n';
import{routeLabel}from'../domain/graph';
import{LanguageSelect}from'../components/LanguageSelect';
import{ErrorBanner}from'../components/ErrorBanner';
import{ModelSettingsDialog}from'../components/ModelSettingsDialog';
import{MarkdownMessage}from'../components/MarkdownMessage';
import{AgentRunWorkspace}from'../components/AgentRunWorkspace';
import{AppIcon}from'../components/AppIcon';
import{ActivityStatus}from'../components/ActivityStatus';
import{ComposerModelPicker}from'../components/ComposerModelPicker';
import{ChatUserMessage}from'../components/ChatUserMessage';
import{AttachmentManager}from'../components/AttachmentManager';
import{MAX_ATTACHMENTS,MAX_ATTACHMENT_BYTES,MAX_COMPOSER_CONTENT,formatFileSize,parseChatMessage,serializeChatMessage,supportsAttachment,type ChatAttachment}from'../lib/chatAttachments';
import{notifyWorkflowChanged,type WorkflowChangedEvent}from'../lib/workflowEvents';

type ReplyState='idle'|'thinking'|'error'|'cancelled';
type ReplyPhase='connecting'|'waiting'|'receiving'|'reconnecting';
interface OwnedSnapshot extends MessageSnapshot {owner:string}
interface ReplyRetryTarget {messageId?:Message['id'];content?:string}
interface OwnedReply {owner:string;state:ReplyState;error:string;retry?:ReplyRetryTarget;phase?:ReplyPhase;attempt?:number;startedAt?:number}
interface OwnedMessages {owner:string;items:Message[]}
interface LifecycleRequest {requestId:string;startedAt:number}
const PINNED_WORKFLOWS_KEY='weavepath.pinned-workflows.v1';

function composerAttachment(uploaded:UploadedAttachment):ChatAttachment{
 return{id:uploaded.attachmentId,attachmentId:uploaded.attachmentId,name:uploaded.name,mimeType:uploaded.mimeType,size:uploaded.size,sha256:uploaded.sha256,contextCharacters:uploaded.contextCharacters,contextTruncated:uploaded.contextTruncated,parseStatus:uploaded.parseStatus,parser:uploaded.parser,parseErrorCode:uploaded.parseErrorCode,parseError:uploaded.parseError};
}

function loadPinnedWorkflowIds(){
 try{
  const value=JSON.parse(localStorage.getItem(PINNED_WORKFLOWS_KEY)||'[]');
  return Array.isArray(value)?value.filter((id):id is string=>typeof id==='string'):[];
 }catch{return[]}
}

function elapsedLabel(milliseconds:number){
 const seconds=Math.max(0,Math.floor(milliseconds/1000)),minutes=Math.floor(seconds/60),remaining=seconds%60;
 return minutes?`${minutes}:${String(remaining).padStart(2,'0')}`:`${seconds}s`;
}

function responseDuration(milliseconds:number,locale:'zh-CN'|'en'){
 const seconds=Math.max(0,Math.round(milliseconds/1000));
 if(locale==='zh-CN'){
  if(seconds<1)return'用时不足 1 秒';
  const hours=Math.floor(seconds/3600),minutes=Math.floor(seconds%3600/60),rest=seconds%60;
  return`用时 ${hours?`${hours} 小时 `:''}${minutes?`${minutes} 分钟 `:''}${rest||(!hours&&!minutes)?`${rest} 秒`:''}`.trim();
 }
 if(seconds<1)return'Took less than 1 second';
 const hours=Math.floor(seconds/3600),minutes=Math.floor(seconds%3600/60),rest=seconds%60;
 return`Took ${hours?`${hours}h `:''}${minutes?`${minutes}m `:''}${rest||(!hours&&!minutes)?`${rest}s`:''}`.trim();
}

export interface ChatPageProps{
 onOpenWorkflow?:(workflowId:string)=>void
 onWorkspaceChange?:(context:{workflowId:string;graph:Graph|null})=>void
 activeConversationSignal?:{workflowId:string;instanceId:string;revision:number}|null
}

export function ChatPage({onOpenWorkflow,onWorkspaceChange,activeConversationSignal}:ChatPageProps={}){
 const{t,locale}=useI18n();
 const[settingsOpen,setSettingsOpen]=useState(false);
 const[workflows,setWorkflows]=useState<WorkflowSummary[]>([]);
 const[workflowId,setWorkflowId]=useState(localStorage.getItem('cw.workflow')||'');
 const[graph,setGraph]=useState<Graph|null>(null);
 const[snapshot,setSnapshot]=useState<OwnedSnapshot>({owner:'',messages:[],contentRevision:0});
 const[inheritedState,setInheritedState]=useState<OwnedMessages>({owner:'',items:[]});
 const[memoryOpenOwner,setMemoryOpenOwner]=useState('');
 const[memoryLoadingOwner,setMemoryLoadingOwner]=useState('');
 const[draft,setDraft]=useState('');
 const[attachments,setAttachments]=useState<ChatAttachment[]>([]);
 const[attachmentError,setAttachmentError]=useState('');
 const[attachmentBusy,setAttachmentBusy]=useState(false);
 const[attachmentProgress,setAttachmentProgress]=useState<{name:string;uploadedBytes:number;totalBytes:number;resumedChunks:number}|null>(null);
 const[attachmentManagerOpen,setAttachmentManagerOpen]=useState(false);
 const[attachmentManagerTarget,setAttachmentManagerTarget]=useState<{attachmentId:string;chunkOrdinal:number}|null>(null);
 const[error,setError]=useState('');
 const[workflowBusy,setWorkflowBusy]=useState(false);
 const[pendingOwners,setPendingOwners]=useState<Set<string>>(()=>new Set());
 const[creating,setCreating]=useState(false);
 const[newName,setNewName]=useState('');
 const[rootTitle,setRootTitle]=useState('');
 const[pinnedWorkflowIds,setPinnedWorkflowIds]=useState<string[]>(loadPinnedWorkflowIds);
 const[renamingWorkflowId,setRenamingWorkflowId]=useState('');
 const[workflowNameDraft,setWorkflowNameDraft]=useState('');
 const[aiStatus,setAiStatus]=useState<AIStatus|null>(null);
 const[reply,setReply]=useState<OwnedReply>({owner:'',state:'idle',error:''});
 const[replyClock,setReplyClock]=useState(Date.now());
 const[streamingText,setStreamingText]=useState('');
 const[editingId,setEditingId]=useState('');
 const[editDraft,setEditDraft]=useState('');
 const[copiedId,setCopiedId]=useState('');
 const messagesRef=useRef<HTMLDivElement>(null);
 const attachmentInputRef=useRef<HTMLInputElement>(null);
 const workflowRequest=useRef(0),graphRequest=useRef(0),memoryRequest=useRef(0);
 const graphRef=useRef<Graph|null>(null),activeRouteIdRef=useRef(''),workflowIdRef=useRef(workflowId);
 const sendLocks=useRef<Set<string>>(new Set());
 const snapshotGenerations=useRef<Map<string,number>>(new Map());
 const snapshotRef=useRef(snapshot),activeKey=useRef('');
 const streamControllers=useRef<Map<string,AbortController>>(new Map());
 const streamRequests=useRef<Map<string,string>>(new Map());
 const lifecycleRequests=useRef<Map<string,LifecycleRequest>>(new Map());
 const surfaceId=useRef(crypto.randomUUID());
 const composerOwner=useRef('');
 const mountedAt=useRef(Date.now());
 const cancelledRequests=useRef<Set<string>>(new Set());
 const processedActivationRevision=useRef(0);
 const active=useMemo(()=>graph?.nodes.find(node=>node.id===graph.activeInstanceId),[graph]);
 const activeRouteId=graph?.activeRouteInstanceId||graph?.activeInstanceId||'';
 const owner=activeRouteId&&graph?`${graph.workflowId}:${activeRouteId}`:'';
 activeKey.current=owner;
 graphRef.current=graph;
 activeRouteIdRef.current=activeRouteId;
 workflowIdRef.current=workflowId;
 snapshotRef.current=snapshot;
 const messages=snapshot.owner===owner?snapshot.messages:[];
 const nodeRevision=snapshot.owner===owner?snapshot.contentRevision:graph?.activeRouteContentRevision??active?.contentRevision??0;
 const activeRoute=active&&activeRouteId?{...active,id:activeRouteId,title:graph?.activeRouteTitle||active.title,contentRevision:nodeRevision}:active;
 const inherited=inheritedState.owner===owner?inheritedState.items:[];
 const memoryOpen=memoryOpenOwner===owner;
 const memoryLoading=memoryLoadingOwner===owner;
 const attachmentsReady=attachments.every(file=>!file.parseStatus||file.parseStatus==='ready');
 const replyState=reply.owner===owner?reply.state:'idle';
 const replyError=reply.owner===owner?reply.error:'';
 const replyPhase=reply.owner===owner?reply.phase:undefined;
 const replyStartedAt=reply.owner===owner?reply.startedAt:undefined;
 const busy=workflowBusy||pendingOwners.has(owner);
 const canStop=replyState==='thinking'&&streamRequests.current.has(owner)&&typeof api.cancelChat==='function';
 const orderedWorkflows=useMemo(()=>{
  const pinned=new Set(pinnedWorkflowIds);
  return workflows.map((workflow,index)=>({workflow,index})).sort((left,right)=>Number(pinned.has(right.workflow.id))-Number(pinned.has(left.workflow.id))||left.index-right.index).map(item=>item.workflow);
 },[pinnedWorkflowIds,workflows]);

 const notifyPeerSurfaces=useCallback((event:Omit<WorkflowChangedEvent,'senderId'|'sentAt'>)=>{
  notifyWorkflowChanged({...event,senderId:surfaceId.current,sentAt:Date.now()});
 },[]);

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
  const request=++workflowRequest.current;
  try{
   const list=await api.workflows();
   if(request!==workflowRequest.current)return;
   setWorkflows(list);
   if(!list.some(workflow=>workflow.id===workflowId)){
    const next=list[0]?.id||'';
    setWorkflowId(next);
    setGraph(null);
    setError('');
    if(!next)localStorage.removeItem('cw.workflow');
   }
  }catch(caught){if(request===workflowRequest.current)setError(caught instanceof Error?caught.message:String(caught))}
 },[workflowId]);

 const loadGraph=useCallback(async(targetWorkflowId=workflowId)=>{
  const request=++graphRequest.current;
  if(!targetWorkflowId){if(!workflowIdRef.current)setGraph(null);return}
  try{
   const value=await api.graph(targetWorkflowId);
   if(request!==graphRequest.current||targetWorkflowId!==workflowIdRef.current)return;
   setGraph(value);
   setError('');
   localStorage.setItem('cw.workflow',targetWorkflowId);
  }catch(caught){
   if(request!==graphRequest.current||targetWorkflowId!==workflowIdRef.current)return;
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

 const handleWorkflowEvent=useCallback((event:MessageEvent|{data?:unknown})=>{
  // `window.postMessage` can loop back to the sender in embedded/test
  // surfaces. Ignore that echo; BroadcastChannel events (used by the real
  // two-surface workspace) do not carry a `source` field.
  if('source' in event&&event.source===window)return;
  const value=event.data;
  if(!value||typeof value!=='object')return;
  const change=value as WorkflowChangedEvent;
  if(change.senderId===surfaceId.current)return;
  if(change.phase&&change.sentAt&&change.sentAt<mountedAt.current)return;
  if(change.type!=='conversation-workflow-changed')return;
  // Structural events also invalidate the workflow summary list. The graph
  // carries conversation titles, but the sidebar label comes from
  // `api.workflows()`, so refreshing only the graph leaves a renamed workflow
  // visibly stale until the page is reloaded.
  const isCurrentWorkflow=!change.workflowId||change.workflowId===workflowId;
  if(!change.phase){
   void loadWorkflows();
   // The workflow list is global, so a rename in another open workflow still
   // belongs in the sidebar. Its graph and messages must not replace the
   // currently open workflow, however.
   if(!isCurrentWorkflow)return;
   void loadGraph();
   return;
  }
  if(!isCurrentWorkflow)return;
  // A start event is emitted before the model has committed new graph
  // metadata. Terminal events refresh the graph once for content revisions
  // and automatic titles; in-flight polling below is messages-only.
  if(change.phase!=='started')void loadGraph();
  const targetInstance=change.instanceId||'';
  if(!targetInstance||!graphRef.current)return;
  const targetOwner=`${workflowId}:${targetInstance}`;
  const requestId=change.requestId||'';
  const eventTime=change.sentAt||Date.now();
  if(change.phase==='started'){
   const current=lifecycleRequests.current.get(targetOwner);
   if(current&&eventTime<current.startedAt)return;
   lifecycleRequests.current.set(targetOwner,{requestId,startedAt:eventTime});
   if(targetInstance!==activeRouteIdRef.current)return;
   setReply({owner:targetOwner,state:'thinking',error:'',retry:change.content?{content:change.content}:undefined,phase:'connecting',attempt:1,startedAt:eventTime});
   void refreshRouteMessages(workflowId,targetInstance,0,true);
   return;
  }
  const current=lifecycleRequests.current.get(targetOwner);
  const matches=!!current&&current.requestId===requestId&&eventTime>=current.startedAt;
  if(matches)lifecycleRequests.current.delete(targetOwner);
  if(targetInstance===activeRouteIdRef.current)void refreshRouteMessages(workflowId,targetInstance,0,true);
  // A terminal event only owns the state created by its matching start.
  // This prevents request A from clearing request B on the same route.
  if(!matches||targetInstance!==activeRouteIdRef.current)return;
  if(change.phase==='completed'){
   setStreamingText('');setReply({owner:targetOwner,state:'idle',error:''});
  }else if(change.phase==='failed'){
   setReply(current=>({owner:targetOwner,state:'error',error:change.error||t('aiGenericError'),retry:change.content?{content:change.content}:current.owner===targetOwner?current.retry:undefined}));
  }else if(change.phase==='cancelled'){
   setStreamingText('');setReply({owner:targetOwner,state:'cancelled',error:''});
  }
 },[loadGraph,loadWorkflows,refreshRouteMessages,t,workflowId]);

 useEffect(()=>{void loadWorkflows();void refreshAI()},[loadWorkflows,refreshAI]);
 useEffect(()=>{setGraph(null);graphRequest.current++;void loadGraph()},[loadGraph]);
 useEffect(()=>{
  if(!activeConversationSignal||activeConversationSignal.revision<=processedActivationRevision.current)return;
  processedActivationRevision.current=activeConversationSignal.revision;
  if(activeConversationSignal.workflowId!==workflowIdRef.current){
   setWorkflowId(activeConversationSignal.workflowId);
   setGraph(null);
   setError('');
   localStorage.setItem('cw.workflow',activeConversationSignal.workflowId);
   return;
  }
  // Canvas and Chat are siblings in the same React tree. This direct signal
  // makes route activation deterministic; BroadcastChannel remains only for
  // additional windows/surfaces and is no longer the sole synchronization path.
  void loadGraph(activeConversationSignal.workflowId);
 },[activeConversationSignal?.revision,activeConversationSignal?.workflowId,loadGraph]);
 useEffect(()=>{
  memoryRequest.current++;
  setMemoryOpenOwner('');
  setMemoryLoadingOwner('');
  const requestPending=!!owner&&(sendLocks.current.has(owner)||lifecycleRequests.current.has(owner));
  setReply(current=>requestPending&&current.owner===owner&&current.state==='thinking'?current:{owner,state:requestPending?'thinking':'idle',error:'',...(requestPending?{phase:'connecting' as const,attempt:1,startedAt:Date.now()}: {})});
  setStreamingText('');
  setEditingId('');
  setEditDraft('');
  setCopiedId('');
  if(composerOwner.current!==owner){
   composerOwner.current=owner;
   setAttachments([]);
   setAttachmentError('');
  }
  if(graph&&activeRouteId&&owner)void refreshRouteMessages(graph.workflowId,activeRouteId,graph.activeRouteContentRevision||0);
 },[owner,graph?.workflowId,activeRouteId,graph?.activeRouteContentRevision,refreshRouteMessages]);
 // A canvas request is durable before the model finishes.  Polling while a
 // request is in flight makes that user turn (and its pending status) appear
 // in the chat surface without waiting for the assistant response.
 useEffect(()=>{
  // Local sends already own the request lifecycle and refresh on completion.
  // Poll only when another surface (for example Turn Canvas) started the
  // request; otherwise the poll can race the local optimistic snapshot.
  if(!owner||replyState!=='thinking'||!graph||sendLocks.current.has(owner))return;
  const timer=window.setInterval(()=>{
   void refreshRouteMessages(graph.workflowId,activeRouteId,0,true);
  },650);
  return()=>window.clearInterval(timer);
 },[activeRouteId,graph,owner,refreshRouteMessages,replyState]);
 useEffect(()=>()=>{
  for(const controller of streamControllers.current.values())controller.abort();
  streamControllers.current.clear();
  streamRequests.current.clear();
 },[]);
 useEffect(()=>{const box=messagesRef.current;if(box)box.scrollTop=box.scrollHeight},[messages,replyState]);
 useEffect(()=>{
  if(replyState!=='thinking')return;
  setReplyClock(Date.now());
  const timer=window.setInterval(()=>setReplyClock(Date.now()),1000);
  return()=>window.clearInterval(timer);
 },[replyState,replyStartedAt]);
 useEffect(()=>{onWorkspaceChange?.({workflowId,graph})},[workflowId,graph,onWorkspaceChange]);
 useEffect(()=>{
  const channel=new BroadcastChannel('conversation-workflow');
  channel.addEventListener('message',handleWorkflowEvent);
  window.addEventListener('message',handleWorkflowEvent);
  return()=>{channel.close();window.removeEventListener('message',handleWorkflowEvent)};
 },[handleWorkflowEvent]);

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
  const workflow=graph?.workflowId,instance=activeRouteId;
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
  setWorkflowBusy(true);
  try{
   const workflow=await api.createWorkflow({...(newName.trim()?{name:newName.trim()}:{}),...(rootTitle.trim()?{rootTitle:rootTitle.trim()}:{}),rootTopicId:crypto.randomUUID()});
   setCreating(false);
   setWorkflowId(workflow.workflowId);
   await loadWorkflows();
  }catch(caught){setError(caught instanceof Error?caught.message:String(caught))}
  finally{setWorkflowBusy(false)}
 }

 function togglePinnedWorkflow(id:string){
  setPinnedWorkflowIds(current=>{
   const next=current.includes(id)?current.filter(item=>item!==id):[...current,id];
   localStorage.setItem(PINNED_WORKFLOWS_KEY,JSON.stringify(next));
   return next;
  });
 }

 function beginWorkflowRename(workflow:WorkflowSummary){
  if(workflowBusy)return;
  setRenamingWorkflowId(workflow.id);setWorkflowNameDraft(workflow.name);
 }

 function cancelWorkflowRename(){setRenamingWorkflowId('');setWorkflowNameDraft('')}

 async function renameSidebarWorkflow(workflow:WorkflowSummary){
  const name=workflowNameDraft.trim();
  if(!name||workflowBusy)return;
  setWorkflowBusy(true);setError('');
  try{
   const currentGraph=workflow.id===graph?.workflowId?graph:await api.graph(workflow.id);
   const result=await api.renameWorkflow(workflow.id,name,currentGraph.graphRevision);
   setWorkflows(current=>current.map(item=>item.id===workflow.id?{...item,name}:item));
   setGraph(current=>current?.workflowId===workflow.id?{...current,name,graphRevision:result.graphRevision,eventRevision:result.eventRevision}:current);
   cancelWorkflowRename();
   notifyPeerSurfaces({type:'conversation-workflow-changed',workflowId:workflow.id});
   await loadWorkflows();
   if(workflow.id===workflowIdRef.current)await loadGraph(workflow.id);
  }catch(caught){
   if(caught instanceof ApiError&&caught.status===409){await loadWorkflows();if(workflow.id===workflowIdRef.current)await loadGraph(workflow.id);setError(t('renameConflict'))}
   else setError(caught instanceof Error?caught.message:String(caught));
  }finally{setWorkflowBusy(false)}
 }

 function aiError(caught:unknown){
  if(caught instanceof ApiError){
   switch(caught.code){
    case'aiTimeout':return t('aiTimeout');
    case'aiConnectionFailed':return t('aiConnectionFailed');
    case'chatInterrupted':return t('chatInterrupted');
    case'aiUnavailable':return t('aiUnavailable');
    case'aiContextTooLarge':return t('aiContextTooLarge');
    case'reasoningEffortUnsupported':return t('reasoningEffortUnsupported');
    case'aiEmptyResponse':return t('aiEmptyResponse');
    case'validationError':return t('validationError');
    case'conflict':return t('contentConflict');
   }
  }
  return t('aiGenericError');
 }

 async function addAttachments(selected:File[]){
  if(!selected.length)return;
  setAttachmentError('');
  if(attachments.length+selected.length>MAX_ATTACHMENTS){setAttachmentError(t('attachmentLimit'));return}
  const workflow=graph?.workflowId,instance=activeRouteId;
  if(!workflow||!instance)return;
  const targetOwner=`${workflow}:${instance}`;
  const next=[...attachments];
  setAttachmentBusy(true);
  try{
   for(const file of selected){
    if(file.size>MAX_ATTACHMENT_BYTES){setAttachmentError(t('attachmentTooLarge'));return}
    if(!supportsAttachment(file.name,file.type)){setAttachmentError(t('attachmentUnsupported'));return}
    setAttachmentProgress({name:file.name,uploadedBytes:0,totalBytes:file.size,resumedChunks:0});
    let uploaded=await api.uploadAttachment(workflow,instance,file,progress=>{
     if(activeKey.current===targetOwner)setAttachmentProgress({name:file.name,uploadedBytes:progress.uploadedBytes,totalBytes:progress.totalBytes,resumedChunks:progress.resumedChunks});
    }),item=composerAttachment(uploaded);
    if(activeKey.current!==targetOwner)return;
    if(serializeChatMessage(draft,[...next,item]).length>MAX_COMPOSER_CONTENT){void api.deleteAttachment(workflow,instance,uploaded.attachmentId).catch(()=>undefined);setAttachmentError(t('attachmentContentLimit'));return}
    next.push(item);
    setAttachments([...next]);
    while(uploaded.parseStatus==='processing'&&activeKey.current===targetOwner){
     await new Promise(resolve=>setTimeout(resolve,300));
     uploaded=await api.attachment(workflow,instance,uploaded.attachmentId);item=composerAttachment(uploaded);
     const index=next.findIndex(existing=>existing.id===item.id);
     if(index>=0){next[index]=item;setAttachments([...next])}
    }
    if(uploaded.parseStatus!=='ready')setAttachmentError(uploaded.parseErrorCode==='attachmentOcrUnavailable'?t('imageOcrUnavailable'):t('attachmentParseFailed'));
   }
  }catch(caught){setAttachmentError(caught instanceof ApiError&&caught.code==='attachmentTooLarge'?t('attachmentTooLarge'):caught instanceof ApiError&&caught.code==='attachmentUnsupported'?t('attachmentUnsupported'):caught instanceof ApiError&&caught.code==='attachmentUnreadable'?t('attachmentReadingFailed'):t('attachmentUploadFailed'))}
  finally{setAttachmentBusy(false);setAttachmentProgress(null)}
 }

 function removeAttachment(id:string){
  const file=attachments.find(item=>item.id===id),workflow=graph?.workflowId,instance=activeRouteId;
  setAttachments(current=>current.filter(item=>item.id!==id));setAttachmentError('');
  if(file?.attachmentId&&workflow&&instance)void api.deleteAttachment(workflow,instance,file.attachmentId).catch(()=>undefined);
 }
 function beginEdit(message:Message){setEditingId(String(message.id));setEditDraft(parseChatMessage(message.content).prompt)}
 async function copyMessage(message:Message){
  const content=message.role==='user'?parseChatMessage(message.content).prompt:message.content;
  try{await navigator.clipboard.writeText(content);setCopiedId(String(message.id))}
  catch{setCopiedId('')}
 }

 async function regenerate(message:Message){
  const stored=parseChatMessage(message.content),content=serializeChatMessage(editDraft,stored.attachments),workflow=graph?.workflowId,instance=activeRouteId;
  if(!content||!workflow||!instance)return;
  const targetOwner=`${workflow}:${instance}`,expected=nodeRevision;
  if(!lockRoute(targetOwner))return;
  nextSnapshotGeneration(targetOwner);
  setEditingId('');
  const retry={messageId:message.id,content};
  setReply({owner:targetOwner,state:aiStatus?.configured?'thinking':'idle',error:'',retry,phase:'reconnecting',attempt:1,startedAt:Date.now()});
  try{
   const value=await api.regenerate(workflow,instance,message.id,content,expected);
   const generation=nextSnapshotGeneration(targetOwner);
   applySnapshot(targetOwner,value,generation,expected,true);
   if(activeKey.current===targetOwner)setReply({owner:targetOwner,state:'idle',error:''});
  }catch(caught){
   await refreshRouteMessages(workflow,instance,expected,true);
   if(activeKey.current===targetOwner)setReply({owner:targetOwner,state:'error',error:aiError(caught),retry});
 }finally{unlockRoute(targetOwner)}
 }

 async function retryAnswer(){
  const workflow=graph?.workflowId,instance=activeRouteId;
  if(reply.owner!==owner||reply.state!=='error'||!workflow||!instance)return;
  const targetOwner=`${workflow}:${instance}`;
  if(!lockRoute(targetOwner))return;
  let retry=reply.retry;
  setReply({owner:targetOwner,state:'thinking',error:'',retry,phase:'reconnecting',attempt:1,startedAt:Date.now()});setStreamingText('');
  try{
   // Refresh first: a failed stream may have durably appended the user
   // message after the optimistic snapshot was rendered. Using the stale
   // revision makes the retry look like a no-op (the API correctly returns
   // 409), so always derive the anchor from the current route projection.
   const refreshedCurrent=await refreshRouteMessages(workflow,instance,0,true);
   if(!refreshedCurrent)throw new ApiError(t('aiGenericError'),409,'conflict');
   const refreshed=snapshotRef.current;
   const localUsers=refreshed.messages.filter(item=>item.role==='user'&&!item.inherited);
   const latest=(retry?.messageId!==undefined?localUsers.find(item=>String(item.id)===String(retry?.messageId)):undefined)
    ||(retry?.content?([...localUsers].reverse().find(item=>item.content===retry?.content)):undefined)
    ||localUsers.at(-1);
   const content=retry?.content?.trim()||latest?.content.trim();
   if(!latest||!content)throw new ApiError(t('aiGenericError'),409,'conflict');
   retry={messageId:latest.id,content};
   const expected=refreshed.contentRevision;
   const value=await api.regenerate(workflow,instance,latest.id,content,expected);
   const generation=nextSnapshotGeneration(targetOwner);applySnapshot(targetOwner,value,generation,expected,true);
   if(activeKey.current===targetOwner)setReply({owner:targetOwner,state:'idle',error:''});
  }catch(caught){
   if(activeKey.current===targetOwner)setReply({owner:targetOwner,state:'error',error:aiError(caught),retry});
  }finally{unlockRoute(targetOwner)}
 }

 async function stopGenerating(){
  const requestId=streamRequests.current.get(owner),controller=streamControllers.current.get(owner);
  const workflow=graph?.workflowId,instance=activeRouteId,targetOwner=owner;
  if(!requestId||!controller||!workflow||!instance||typeof api.cancelChat!=='function')return;
  try{
   const result=await api.cancelChat(workflow,instance,requestId);
   // The request may have completed while cancellation was in flight. Never
   // abort or announce cancellation for a different/newer owner request.
   if(streamRequests.current.get(targetOwner)!==requestId){
    await refreshRouteMessages(workflow,instance,0,true);
    return;
   }
   if(!result.cancelled){
    // Server did not confirm cancellation. Keep waiting and only reconcile
    // durable messages; claiming "cancelled" here would be a false terminal.
    await refreshRouteMessages(workflow,instance,0,true);
    return;
   }
   cancelledRequests.current.add(requestId);
   controller.abort();
   if(activeKey.current===targetOwner){
    setStreamingText('');
    setReply({owner:targetOwner,state:'cancelled',error:''});
   }
   notifyPeerSurfaces({type:'conversation-workflow-changed',workflowId:workflow,instanceId:instance,phase:'cancelled',requestId});
   await refreshRouteMessages(workflow,instance,0,true);
  }catch{
   // A failed cancellation request leaves the underlying generation running.
   // Refresh what is durable, but do not abort locally or publish a false
   // cancelled state.
   await refreshRouteMessages(workflow,instance,0,true);
  }
 }

 async function send(){
  const text=serializeChatMessage(draft,attachments),workflow=graph?.workflowId,instance=activeRouteId;
  if(!text||!workflow||!instance||attachmentBusy)return;
  if(attachments.some(file=>file.parseStatus&&file.parseStatus!=='ready')){setAttachmentError(t('attachmentNotReady'));return}
  if(text.length>MAX_COMPOSER_CONTENT){setAttachmentError(t('attachmentContentLimit'));return}
  const targetOwner=`${workflow}:${instance}`;
  if(!lockRoute(targetOwner))return;
  const requestId=crypto.randomUUID();
  nextSnapshotGeneration(targetOwner);
  setDraft('');
  setAttachments([]);
  setAttachmentError('');
  setError('');
  setReply({owner:targetOwner,state:aiStatus?.configured?'thinking':'idle',error:'',phase:'connecting',attempt:1,startedAt:Date.now()});
  setStreamingText('');
  const current=snapshotRef.current;
  const base=current.owner===targetOwner?current:{owner:targetOwner,messages:[],contentRevision:active?.contentRevision||0};
  const optimistic:OwnedSnapshot={...base,messages:[...base.messages,{id:crypto.randomUUID(),role:'user',content:text,inherited:false}]};
  snapshotRef.current=optimistic;
  setSnapshot(optimistic);
  // Let the other mounted surface enter its pending state immediately.  The
  // subsequent polling/revision refresh replaces the optimistic message with
  // the durable record once the backend has appended it.
  notifyPeerSurfaces({type:'conversation-workflow-changed',workflowId:workflow,instanceId:instance,phase:'started',requestId,content:text});
  let delivered: {userMessage?:Message;assistantMessage?:Message}|undefined;
  try{
   if(aiStatus?.configured){
    if(typeof api.chatStream==='function'){
     const controller=new AbortController();
     streamRequests.current.set(targetOwner,requestId);
     streamControllers.current.set(targetOwner,controller);
     let terminal:'completed'|'failed'|'cancelled'|null=null;let streamError:ApiError|null=null,lastSequence=0;
     const consumeStreamEvent=(event:string,data:import('../lib/api').ChatStreamEvent)=>{
      if(typeof data.sequence==='number'){
       if(data.sequence<=lastSequence)return;
       lastSequence=data.sequence;
      }
      if(event==='connection.status'&&data.phase){
       if(activeKey.current===targetOwner)setReply(current=>current.owner===targetOwner&&current.state==='thinking'?{...current,phase:data.phase,attempt:data.attempt}:current);
       return;
      }
      if(event==='message.reset'){
       if(activeKey.current===targetOwner)setStreamingText('');
       return;
      }
      if(event==='message.delta'){
       if(activeKey.current===targetOwner){setReply(current=>current.owner===targetOwner&&current.state==='thinking'?{...current,phase:'receiving'}:current);setStreamingText(current=>current+(data.delta||''));}
       return;
      }
      if(event==='message.completed'){
       terminal='completed';
       delivered={userMessage:data.userMessage,assistantMessage:data.assistantMessage};
      }
      if(event==='message.cancelled')terminal='cancelled';
      if(event==='message.failed'){terminal='failed';streamError=new ApiError(data.error||t('aiGenericError'),502,data.code)}
     };
     const recoverEvents=async()=>{
      const recovered=await api.chatEvents(workflow,instance,requestId,lastSequence);
      for(const item of recovered.events)consumeStreamEvent(item.type,{...item.payload,sequence:item.sequence});
      if(recovered.status==='completed')terminal='completed';
      else if(recovered.status==='cancelled')terminal='cancelled';
      else if(recovered.status==='failed'){
       terminal='failed';
       if(!streamError)streamError=new ApiError(t('aiGenericError'),502,recovered.errorCode||'chatInterrupted');
      }
      return recovered;
     };
     let attempt=1;
     while(attempt<=3){
      terminal=null;streamError=null;
      try{
       await api.chatStream(workflow,instance,text,requestId,consumeStreamEvent,controller.signal);
      }catch(caught){
       if(controller.signal.aborted)throw caught;
       if(activeKey.current===targetOwner)setReply(current=>current.owner===targetOwner&&current.state==='thinking'?{...current,phase:'reconnecting',attempt}:current);
       try{
        let recovered=await recoverEvents();
        // A dropped browser stream does not imply a dropped model request.
        // Continue from the durable server journal without imposing a model
        // response timeout. Cancellation remains available while polling.
        while(recovered.status==='started'&&!controller.signal.aborted){
         await new Promise(resolve=>setTimeout(resolve,500));
         recovered=await recoverEvents();
        }
       }catch(recoveryError){
        if(attempt>=3)throw caught instanceof Error?caught:recoveryError;
       }
      }
      if(terminal==='completed'||terminal==='cancelled')break;
      if(terminal==='failed'&&(streamError as ApiError|null)?.code!=='chatInterrupted')throw streamError;
      if(terminal===null){
       try{await recoverEvents()}catch{/* The reconnect below uses the same idempotency key. */}
       if(terminal==='completed'||terminal==='cancelled')break;
       if(terminal==='failed'&&(streamError as ApiError|null)?.code!=='chatInterrupted')throw streamError;
      }
      if(attempt>=3)throw streamError||new ApiError(t('aiGenericError'),502,'aiEmptyResponse');
      attempt+=1;
      lastSequence=0;terminal=null;streamError=null;
      if(activeKey.current===targetOwner){setStreamingText('');setReply(current=>current.owner===targetOwner&&current.state==='thinking'?{...current,phase:'reconnecting',attempt}:current)}
      await new Promise(resolve=>setTimeout(resolve,200*attempt));
     }
     const locallyCancelled=cancelledRequests.current.has(requestId);
     if(terminal==='cancelled'||locallyCancelled){
      cancelledRequests.current.delete(requestId);
      await refreshRouteMessages(workflow,instance,base.contentRevision,true);
      if(activeKey.current===targetOwner){setStreamingText('');setReply({owner:targetOwner,state:'cancelled',error:''})}
      if(!locallyCancelled)notifyPeerSurfaces({type:'conversation-workflow-changed',workflowId:workflow,instanceId:instance,phase:'cancelled',requestId});
      return;
     }
     if(terminal==='failed')throw streamError||new ApiError(t('aiGenericError'),502,'aiUnavailable');
     if(terminal!=='completed')throw new ApiError(t('aiGenericError'),502,'aiEmptyResponse');
    }else delivered=await api.chat(workflow,instance,text,requestId);
   }else delivered={userMessage:await api.send(workflow,instance,text)};
   await refreshRouteMessages(workflow,instance,base.contentRevision,true);
   // If the write succeeded but a concurrent read raced the commit, retain
   // the optimistic user message until the next revision refresh supplies the
   // durable row. This closes the tiny gap between send acknowledgement and
   // message projection on the other surface.
   const projected=snapshotRef.current;
   const assistantMessage=delivered?.assistantMessage;
   if(activeKey.current===targetOwner&&(!projected.messages.some(item=>item.role==='user'&&item.content===text)||assistantMessage&&!projected.messages.some(item=>item.role==='assistant'&&item.id===assistantMessage.id))){
    const fallbackMessages=[...projected.messages];
    if(!fallbackMessages.some(item=>item.role==='user'&&item.content===text))fallbackMessages.push(delivered?.userMessage||{id:crypto.randomUUID(),role:'user',content:text,inherited:false});
    if(assistantMessage&&!fallbackMessages.some(item=>item.id===assistantMessage.id))fallbackMessages.push(assistantMessage);
    const fallback:OwnedSnapshot={...projected,owner:targetOwner,messages:fallbackMessages};
    snapshotRef.current=fallback;setSnapshot(fallback);
   }
   // A previously untitled branch can receive its generated title on the first
   // message. Refresh graph metadata only while this route is still active;
   // loadGraph's request generation continues to reject stale graph responses.
   if(activeKey.current===targetOwner)await loadGraph();
   notifyPeerSurfaces({type:'conversation-workflow-changed',workflowId:workflow,instanceId:instance,phase:'completed',requestId});
   if(activeKey.current===targetOwner)setReply({owner:targetOwner,state:'idle',error:''});
  }catch(caught){
   await refreshRouteMessages(workflow,instance,base.contentRevision,true);
   const wasCancelled=cancelledRequests.current.delete(requestId);
   const message=aiError(caught);
   if(activeKey.current===targetOwner&&!wasCancelled){setStreamingText('');setReply({owner:targetOwner,state:'error',error:message,retry:{content:text}});}
   if(!wasCancelled)notifyPeerSurfaces({type:'conversation-workflow-changed',workflowId:workflow,instanceId:instance,phase:'failed',requestId,content:text,error:message});
  }finally{
   if(streamRequests.current.get(targetOwner)===requestId){
    streamRequests.current.delete(targetOwner);
    streamControllers.current.delete(targetOwner);
   }
   unlockRoute(targetOwner);
  }
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
 const renderMessage=(message:Message,actions=false)=>{
 const copied=copiedId===String(message.id);
  const responseDetails=(details:ChatResponseDetails)=>{
   const available=details.cacheStatus==='reported';
   const value=(number:number|null|undefined,percent=false)=>available&&number!=null
    ?percent?`${Math.round(number*1000)/10}%`:number.toLocaleString(locale)
    :t('unavailable');
   return <details className="message-response-details"><summary aria-label={t('replyDetails')}><AppIcon name="clock"/><span>{responseDuration(details.durationMs,locale)}</span><AppIcon name="chevronRight" className="response-details-chevron"/></summary><div className="response-details-grid"><div><small>{t('cachedTokens')}</small><strong>{value(details.cachedInputTokens)}</strong></div><div><small>{t('cacheMissTokens')}</small><strong>{value(details.uncachedInputTokens)}</strong></div><div><small>{t('cacheHitRate')}</small><strong>{value(details.cacheReuseRatio,true)}</strong></div><div><small>{t('cacheCoverage')}</small><strong>{value(details.cacheCoverage,true)}</strong></div></div>{details.compactionPlan&&<section className="response-compaction-plan"><div className="response-retrieval-title"><AppIcon name="layers" size={14}/><div><strong>{t('contextCompaction')}</strong><small>{t('contextCompacted')}</small></div></div><dl><div><dt>{t('originalMessages')}</dt><dd>{details.compactionPlan.originalMessages.toLocaleString(locale)}</dd></div><div><dt>{t('compactedMessages')}</dt><dd>{details.compactionPlan.compactedMessages.toLocaleString(locale)}</dd></div><div><dt>{t('retainedMessages')}</dt><dd>{details.compactionPlan.retainedMessages.toLocaleString(locale)}</dd></div><div><dt>{t('compressionRatio')}</dt><dd>{Math.round(details.compactionPlan.compressionRatio*100)}%</dd></div></dl>{details.compactionPlan.budgetExceededByProtectedTail&&<p>{t('protectedTailOverflow')}</p>}</section>}{details.retrievalPlan&&<section className="response-retrieval-plan"><div className="response-retrieval-title"><AppIcon name="search" size={14}/><div><strong>{t('retrievalPlan')}</strong><small>{t('autoRetrieved')}</small></div></div><dl><div><dt>{t('candidateChunks')}</dt><dd>{details.retrievalPlan.candidateCount.toLocaleString(locale)}</dd></div><div><dt>{t('selectedChunks')}</dt><dd>{details.retrievalPlan.selectedChunks.toLocaleString(locale)}</dd></div><div><dt>{t('selectedCharacters')}</dt><dd>{details.retrievalPlan.selectedCharacters.toLocaleString(locale)}</dd></div></dl><p>{details.retrievalPlan.truncated?t('retrievalTruncated'):t('retrievalComplete')}</p></section>}{!!details.sources?.length&&<section className="response-evidence"><strong>{t('evidenceUsed')}</strong><div>{details.sources.map((source,index)=><button type="button" key={`${source.attachmentId||source.name}-${source.chunkOrdinal}-${index}`} disabled={!source.attachmentId} title={t('openEvidence')} onClick={()=>{if(!source.attachmentId)return;setAttachmentManagerTarget({attachmentId:source.attachmentId,chunkOrdinal:source.chunkOrdinal});setAttachmentManagerOpen(true)}}><AppIcon name="attachment" size={13}/><span>{source.name||t('attachmentLibrary')} · {source.locator}</span>{source.retrievalMode==='automatic'&&<small>{locale==='zh-CN'?'自动':'Auto'}</small>}</button>)}</div></section>}</details>;
  };
  const stored=message.role==='user'?parseChatMessage(message.content):null;
  return <article key={message.id} className={`message ${message.role}${actions&&message.id===lastUserId?' actionable':''}`}><div>{editingId===String(message.id)?<div className="message-edit"><label>{t('editQuestionLabel')}<textarea value={editDraft} onChange={event=>setEditDraft(event.target.value)}/></label>{!!stored?.attachments.length&&<div className="message-attachment-list">{stored.attachments.map(file=><span className="message-attachment" key={file.id}><AppIcon name="attachment" size={14}/><span>{file.name}</span><small>{formatFileSize(file.size)}</small></span>)}</div>}<div><button type="button" onClick={()=>{setEditingId('');setEditDraft('')}}>{t('cancelEdit')}</button><button type="button" className="primary" disabled={(!editDraft.trim()&&!stored?.attachments.length)||busy} onClick={()=>void regenerate(message)}>{t('saveRegenerate')}</button></div></div>:<>{message.role==='assistant'?<><MarkdownMessage content={message.content}/>{message.responseDetails&&responseDetails(message.responseDetails)}</>:<ChatUserMessage content={message.content}/>} {actions&&message.id===lastUserId&&<div className="message-actions" aria-label={t('messageActions')}><button type="button" className="message-action-button" aria-label={t('editQuestion')} title={t('editQuestion')} onClick={()=>beginEdit(message)}><AppIcon name="edit" className="message-action-icon"/></button><button type="button" className={`message-action-button${copied?' is-copied':''}`} aria-label={copied?t('copied'):t('copyMessage')} title={copied?t('copied'):t('copyMessage')} onClick={()=>void copyMessage(message)}><AppIcon name={copied?'check':'copy'} className="message-action-icon"/></button></div>}</>}</div></article>;
 };
 const memoryPanel=(active?.parentId||activeRouteId!==graph?.activeInstanceId)?<section className="inherited-memory"><button type="button" aria-expanded={memoryOpen} onClick={()=>void toggleMemory()}><AppIcon name={memoryOpen?'chevronDown':'chevronRight'}/><span>{t('inheritedMemory')}</span></button>{memoryOpen&&<div className="inherited-memory-body">{memoryLoading?<p>{t('loadingInherited')}</p>:inherited.length?inherited.map(message=>renderMessage(message)):<p>{t('noInherited')}</p>}</div>}</section>:null;
 const progressText=replyPhase==='connecting'?t('connecting'):replyPhase==='waiting'?t('waitingForModel'):replyPhase==='receiving'?t('receivingResponse'):replyPhase==='reconnecting'?`${t('reconnecting')}${reply.attempt&&reply.attempt>1?` (${reply.attempt}/3)`:''}`:t('thinking');
 const stream=<div className="messages" ref={messagesRef}>{memoryPanel}{!messages.length&&replyState==='idle'&&<p className="empty">{workflowId?t('empty'):t('selectWorkflow')}</p>}{messages.map(message=>renderMessage(message,true))}{replyState==='thinking'&&<article className="message assistant reply-thinking"><div>{streamingText&&<MarkdownMessage content={streamingText}/>}<ActivityStatus label={progressText} detail={`${t('elapsed')} ${elapsedLabel(replyClock-(replyStartedAt||replyClock))}`} phase={replyPhase}>{canStop&&<button type="button" className="stop-generating" onClick={()=>void stopGenerating()}>{t('stopGenerating')}</button>}</ActivityStatus></div></article>}{replyState==='error'&&<article className="message system reply-error"><ActivityStatus label={replyError} tone="error"><button type="button" onClick={()=>void retryAnswer()} disabled={busy}><AppIcon name="retry" size={14}/><span>{t('retryAnswer')}</span></button></ActivityStatus></article>}{replyState==='cancelled'&&<article className="message system reply-cancelled"><ActivityStatus label={t('cancelled')} tone="muted" compact/></article>}</div>;

 return <main className="chat-shell">
  <aside className="sidebar">
   <div className="brand">◫ <strong>{t('app')}</strong></div>
   <button className="new-workflow" aria-label={t('newWorkflow')} title={t('newWorkflow')} onClick={()=>setCreating(true)}><AppIcon name="plus"/><span>{t('newWorkflow')}</span></button>
   <h2>{t('conversations')}</h2>
   <nav>{orderedWorkflows.map(workflow=>{
    const pinned=pinnedWorkflowIds.includes(workflow.id),renaming=renamingWorkflowId===workflow.id;
    return <div className={`workflow-sidebar-item${workflow.id===workflowId?' current':''}${pinned?' is-pinned':''}`} key={workflow.id}>
     {renaming?<form className="workflow-sidebar-rename" onSubmit={event=>{event.preventDefault();void renameSidebarWorkflow(workflow)}}><input autoFocus aria-label={`${t('renameWorkflow')}: ${workflow.name}`} value={workflowNameDraft} maxLength={240} onChange={event=>setWorkflowNameDraft(event.target.value)} onKeyDown={event=>{if(event.key==='Escape'){event.preventDefault();cancelWorkflowRename()}}}/><button type="submit" className="workflow-sidebar-icon" aria-label={t('save')} title={t('save')} disabled={workflowBusy||!workflowNameDraft.trim()}><AppIcon name="check"/></button><button type="button" className="workflow-sidebar-icon" aria-label={t('cancel')} title={t('cancel')} onClick={cancelWorkflowRename}><AppIcon name="close"/></button></form>:<><button className="workflow-sidebar-select" aria-current={workflow.id===workflowId?'page':undefined} onClick={()=>setWorkflowId(workflow.id)}>{workflow.name}</button><span className="workflow-sidebar-actions"><button type="button" className="workflow-sidebar-icon" aria-label={`${pinned?t('unpinWorkflow'):t('pinWorkflow')}: ${workflow.name}`} title={pinned?t('unpinWorkflow'):t('pinWorkflow')} aria-pressed={pinned} onClick={()=>togglePinnedWorkflow(workflow.id)}><AppIcon name="pin"/></button><button type="button" className="workflow-sidebar-icon" aria-label={`${t('renameWorkflow')}: ${workflow.name}`} title={t('renameWorkflow')} onClick={()=>beginWorkflowRename(workflow)}><AppIcon name="edit"/></button></span></>}
    </div>;
   })}</nav>
   <div className="sidebar-controls"><button className="settings-button" aria-label={t('settings')} title={t('settings')} onClick={()=>setSettingsOpen(true)}><AppIcon name="settings"/><span>{t('settings')}</span></button><LanguageSelect/></div>
  </aside>
  <section className="chat">
   <header>
    <div><h1>{active?.title||graph?.name||t('app')}</h1><p>{t('route')}: {graph&&active?routeLabel(graph,active.id):'—'}</p><span className={`ai-status ${aiStatus?.configured?'connected':'local'}`}>{aiStatus?.configured?`${t('aiConnected')} · ${aiStatus.model}`:t('recordOnly')}</span></div>
    <AgentRunWorkspace graph={graph} active={activeRoute} contentRevision={nodeRevision} aiStatus={aiStatus} onRunCompleted={async target=>{await refreshRouteMessages(target.workflowId,target.instanceId,target.inputContentRevision)}}/>
    <button className="workflow-launch" disabled={!graph} onClick={openGraph}><AppIcon name="workflow"/><span>{t('workflow')}</span></button>
   </header>
   <ErrorBanner message={error} onRetry={()=>{setError('');void loadGraph()}}/>
   {stream}
   <form className="composer" onSubmit={event=>{event.preventDefault();void send()}}>
    <textarea value={draft} onChange={event=>setDraft(event.target.value)} placeholder={t('placeholder')} onKeyDown={event=>{if(event.key==='Enter'&&!event.shiftKey){event.preventDefault();void send()}}}/>
    {!!attachments.length&&<div className="composer-attachments" aria-label={t('attachedFiles')}>{attachments.map(file=><span className={`composer-attachment${file.parseStatus==='failed'?' failed':file.parseStatus==='processing'?' processing':''}`} key={file.id}><AppIcon name={file.parseStatus==='failed'?'warning':'attachment'} size={14}/><span title={file.name}>{file.name}</span><small>{file.parseStatus==='processing'?t('parseProcessing'):file.parseStatus==='failed'?t('parseFailed'):formatFileSize(file.size)}</small><button type="button" aria-label={`${t('removeAttachment')}: ${file.name}`} title={t('removeAttachment')} onClick={()=>removeAttachment(file.id)}><AppIcon name="close" size={12}/></button></span>)}</div>}
    {attachmentProgress&&<div className="composer-upload-progress" role="status"><span><AppIcon name="attachment" size={13}/><strong title={attachmentProgress.name}>{attachmentProgress.name}</strong><small>{t(attachmentProgress.resumedChunks?'resumingUpload':'uploadingFiles')} · {Math.min(100,Math.round(attachmentProgress.uploadedBytes/Math.max(1,attachmentProgress.totalBytes)*100))}%</small></span><progress max={attachmentProgress.totalBytes} value={attachmentProgress.uploadedBytes}/></div>}
    {attachmentError&&<div className="composer-attachment-error" role="alert"><AppIcon name="warning" size={14}/><span>{attachmentError}</span></div>}
    <div className="composer-toolbar">
     <input ref={attachmentInputRef} className="composer-file-input" type="file" multiple accept="text/*,image/*,.md,.markdown,.json,.jsonl,.csv,.tsv,.yaml,.yml,.xml,.html,.css,.js,.jsx,.ts,.tsx,.py,.java,.c,.h,.cpp,.hpp,.cs,.go,.rs,.rb,.php,.sh,.ps1,.sql,.toml,.ini,.cfg,.log,.tex,.r,.pdf,.docx,.xlsx,.pptx" onChange={event=>{void addAttachments(Array.from(event.target.files||[]));event.target.value=''}}/>
     <button type="button" className="composer-attach-button" aria-label={attachmentBusy?t('uploadingFiles'):t('attachFiles')} title={attachmentBusy?t('uploadingFiles'):t('attachFiles')} disabled={busy||attachmentBusy||attachments.length>=MAX_ATTACHMENTS} onClick={()=>attachmentInputRef.current?.click()}>{attachmentBusy?<span className="composer-model-spinner"/>:<AppIcon name="plus" size={17}/>}</button>
     <button type="button" className="composer-attach-button" aria-label={t('attachmentLibrary')} title={t('attachmentLibrary')} disabled={!graph||!activeRouteId} onClick={()=>{setAttachmentManagerTarget(null);setAttachmentManagerOpen(true)}}><AppIcon name="attachment" size={16}/></button>
     <span className="composer-toolbar-spacer"/>
     <ComposerModelPicker status={aiStatus} disabled={replyState==='thinking'} onChanged={setAiStatus} onOpenSettings={()=>setSettingsOpen(true)}/>
     <button className="primary" disabled={(!draft.trim()&&!attachments.length)||busy||attachmentBusy||!attachmentsReady}>{t('send')}</button>
    </div>
   </form>
  </section>
  {settingsOpen&&<ModelSettingsDialog onClose={()=>setSettingsOpen(false)} onSaved={refreshAI}/>}
  {attachmentManagerOpen&&graph&&activeRouteId&&<AttachmentManager workflowId={graph.workflowId} instanceId={activeRouteId} initialAttachmentId={attachmentManagerTarget?.attachmentId} initialChunkOrdinal={attachmentManagerTarget?.chunkOrdinal} onClose={()=>{setAttachmentManagerOpen(false);setAttachmentManagerTarget(null)}}/>}
  {creating&&<div className="modal-backdrop"><form className="modal" onSubmit={event=>{event.preventDefault();void create()}}><h2>{t('newWorkflow')}</h2><label>{t('workflowName')}<input autoFocus value={newName} onChange={event=>setNewName(event.target.value)}/></label><label>{t('rootTitle')}<input value={rootTitle} onChange={event=>setRootTitle(event.target.value)}/></label><p className="auto-name-hint">{t('autoNameHint')}</p><div className="modal-actions"><button type="button" onClick={()=>setCreating(false)}>{t('cancel')}</button><button className="primary" disabled={workflowBusy}>{t('createOpen')}</button></div></form></div>}
 </main>;
}
