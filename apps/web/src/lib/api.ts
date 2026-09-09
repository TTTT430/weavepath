import type {AgentApprovalRequest,AgentMemoryRouteNode,AgentRun,AgentRunEvents,AgentRunMetrics,AgentToolSpec,ApiErrorPayload,Artifact,AttachmentSearchResult,AttachmentSearchStatus,AttachmentUploadSession,BranchComparison,ConnectionDiagnostics,ContextPreview,CreateAgentRunInput,Dataset,DatasetCase,Experiment,AISettings,AISettingsInput,AIStatus,AIValidation,Graph,Message,MessageSnapshot,PrunePlan,ReasoningEffort,RetryAgentRunInput,Route,TurnCanvasSnapshot,UploadedAttachment,WorkflowSummary} from '../domain/types';
const BASE='/api/v1';
export class ApiError extends Error {
 constructor(message:string,public status:number,public code?:string,public runId?:string|number,public diagnostics?:ConnectionDiagnostics){super(message);this.name='ApiError'}
}
export type ChatStreamEvent={requestId?:string;userMessage?:Message;assistantMessage?:Message;delta?:string;code?:string;error?:string;replayed?:boolean;phase?:'connecting'|'waiting'|'receiving'|'reconnecting';attempt?:number;maxAttempts?:number;delayMs?:number;networkRoute?:'direct'|'system'}
async function request<T>(path:string,init?:RequestInit):Promise<T>{const response=await fetch(BASE+path,{...init,headers:{Accept:'application/json',...(init?.body?{'Content-Type':'application/json'}:{}),...init?.headers}});const data=await response.json().catch(()=>({}))as ApiErrorPayload;if(!response.ok)throw new ApiError(data.message||data.error||`HTTP ${response.status}`,response.status,data.code,data.runId,data.diagnostics);return data as T}
const enc=encodeURIComponent;
export const ATTACHMENT_CHUNK_SIZE=4*1024*1024;
export const RESUMABLE_UPLOAD_THRESHOLD=4*1024*1024;
export interface AttachmentUploadProgress {uploadedBytes:number;totalBytes:number;uploadedChunks:number;totalChunks:number;resumedChunks:number}
async function rawRequest<T>(path:string,init:RequestInit):Promise<T>{const response=await fetch(BASE+path,{...init,headers:{Accept:'application/json',...init.headers}});const data=await response.json().catch(()=>({}))as ApiErrorPayload;if(!response.ok)throw new ApiError(data.message||data.error||`HTTP ${response.status}`,response.status,data.code);return data as T}
async function retryRawRequest<T>(path:string,init:RequestInit):Promise<T>{let last:unknown;for(let attempt=0;attempt<3;attempt++){try{return await rawRequest<T>(path,init)}catch(error){last=error;if(error instanceof ApiError&&error.status<500)throw error;if(attempt<2)await new Promise(resolve=>setTimeout(resolve,150*(2**attempt)))}}throw last}
function uploadResumeStorageKey(w:string,i:string,file:File){return`weavepath.attachment-upload.v1:${enc(w)}:${enc(i)}:${enc(file.name)}:${file.size}:${file.lastModified||0}`}
function readUploadClientKey(key:string){try{return localStorage.getItem(key)}catch{return null}}
function writeUploadClientKey(key:string,value:string|null){try{if(value)localStorage.setItem(key,value);else localStorage.removeItem(key)}catch{/* Resumption is optional when storage is unavailable. */}}
function normalizeTools(value:unknown):AgentToolSpec[]|undefined{return Array.isArray(value)?value.flatMap(item=>{if(!item||typeof item!=='object')return[];const x=item as Record<string,unknown>;return typeof x.name==='string'&&typeof x.version==='string'?[{name:x.name,version:x.version,...(typeof x.description==='string'?{description:x.description}:{})}]:[]}):undefined}
function normalizeMemoryRoute(value:unknown):AgentMemoryRouteNode[]|undefined{return Array.isArray(value)?value.flatMap(item=>{if(!item||typeof item!=='object')return[];const x=item as Record<string,unknown>;return typeof x.instanceId==='string'&&typeof x.topicId==='string'&&typeof x.title==='string'?[{instanceId:x.instanceId,topicId:x.topicId,title:x.title}]:[]}):undefined}
function normalizeApprovals(value:unknown):AgentApprovalRequest[]|undefined{return Array.isArray(value)?value.flatMap(item=>{if(!item||typeof item!=='object')return[];const x=item as Record<string,unknown>,tool=x.tool&&typeof x.tool==='object'?x.tool as Record<string,unknown>:{},approvalId=x.approvalId??x.id,toolName=x.toolName??tool.name;if(typeof approvalId!=='string'||typeof toolName!=='string')return[];const rawStatus=String(x.status??'pending'),status=rawStatus==='approved'||rawStatus==='rejected'?rawStatus:'pending';return[{approvalId,runId:(x.runId??undefined)as string|number|undefined,toolCallId:typeof x.toolCallId==='string'?x.toolCallId:undefined,toolName,toolVersion:typeof x.toolVersion==='string'?x.toolVersion:typeof tool.version==='string'?tool.version:undefined,arguments:x.arguments??x.toolArguments,sideEffect:typeof x.sideEffect==='boolean'||typeof x.sideEffect==='string'?x.sideEffect:undefined,status,createdAt:typeof x.createdAt==='string'?x.createdAt:undefined,decidedAt:typeof x.decidedAt==='string'?x.decidedAt:null}]}):undefined}
function optionalNumber(value:unknown):number|null|undefined{return value===null?null:typeof value==='number'&&Number.isFinite(value)?value:undefined}
function normalizeMetrics(value:unknown):AgentRunMetrics|undefined{if(!value||typeof value!=='object')return undefined;const x=value as Record<string,unknown>,rawStatus=x.cacheStatus;const cacheStatus=rawStatus==='reported'||rawStatus==='not_reported'||rawStatus==='unsupported'||rawStatus==='invalid'?rawStatus:rawStatus==='unavailable'?'not_reported':undefined;return{durationMs:optionalNumber(x.durationMs)??null,modelStepCount:optionalNumber(x.modelStepCount)??0,toolCallCount:optionalNumber(x.toolCallCount)??0,toolDurationMs:optionalNumber(x.toolDurationMs)??0,inputTokens:optionalNumber(x.inputTokens)??null,outputTokens:optionalNumber(x.outputTokens)??null,estimatedCost:optionalNumber(x.estimatedCost)??null,cachedInputTokens:optionalNumber(x.cachedInputTokens),uncachedInputTokens:optionalNumber(x.uncachedInputTokens??x.cacheMissTokens),cacheReuseRatio:optionalNumber(x.cacheReuseRatio??x.cacheHitRate),cacheCoverage:optionalNumber(x.cacheCoverage),cacheStatus}}
function normalizeStatus(value:unknown):AgentRun['status']{const raw=String(value??'queued');if(raw==='waiting_approval')return'awaiting_approval';return(['queued','running','awaiting_approval','cancelling','cancelled','completed','failed','interrupted']as const).includes(raw as never)?raw as AgentRun['status']:'unknown'}
function normalizeRun(value:unknown):AgentRun{const x=(value&&typeof value==='object'?value:{})as Record<string,unknown>;return{runId:(x.runId??x.id??'')as string|number,workflowId:String(x.workflowId??''),instanceId:String(x.instanceId??''),status:normalizeStatus(x.status),inputContentRevision:Number(x.inputContentRevision??x.expectedContentRevision??0),attemptNumber:optionalNumber(x.attemptNumber??x.attempt)??undefined,rootRunId:(x.rootRunId??undefined)as string|number|undefined,parentRunId:(x.parentRunId??null)as string|number|null,contextSha256:typeof x.contextSha256==='string'?x.contextSha256:undefined,modelSnapshot:x.modelSnapshot,memoryRoute:normalizeMemoryRoute(x.memoryRoute),acceptedKnowledge:Array.isArray(x.acceptedKnowledge)?x.acceptedKnowledge as AgentRun['acceptedKnowledge']:undefined,availableTools:normalizeTools(x.availableTools),approvalRequests:normalizeApprovals(x.approvalRequests??x.approvals),artifacts:Array.isArray(x.artifacts)?x.artifacts as Artifact[]:undefined,objective:String(x.objective??''),constraints:Array.isArray(x.constraints)?x.constraints.map(String):[],deliverables:Array.isArray(x.deliverables)?x.deliverables.map(String):[],acceptanceChecks:Array.isArray(x.acceptanceChecks)?x.acceptanceChecks.map(String):[],finalMessageId:(x.finalMessageId??null)as string|number|null,finalAnswer:typeof x.finalAnswer==='string'?x.finalAnswer:null,errorCode:typeof x.errorCode==='string'?x.errorCode:null,createdAt:typeof x.createdAt==='string'?x.createdAt:undefined,updatedAt:typeof x.updatedAt==='string'?x.updatedAt:undefined,steps:Array.isArray(x.steps)?x.steps:undefined,toolCalls:Array.isArray(x.toolCalls)?x.toolCalls:undefined,toolResults:Array.isArray(x.toolResults)?x.toolResults:undefined,metrics:normalizeMetrics(x.metrics)}}
export const api={
 aiStatus:()=>request<AIStatus>('/ai/status'),
 aiSettings:()=>request<AISettings>('/ai/settings'),
 saveAISettings:(body:AISettingsInput)=>request<AISettings>('/ai/settings',{method:'PUT',body:JSON.stringify(body)}),
 resetAISettings:()=>request<AISettings>('/ai/settings',{method:'DELETE'}),
 validateAISettings:(body:AISettingsInput)=>request<AIValidation>('/ai/settings/validate',{method:'POST',body:JSON.stringify(body)}),
 aiModels:()=>request<{models:string[];count:number}>('/ai/models'),
 switchAIModel:(model:string,reasoningEffort:ReasoningEffort|null=null)=>request<AISettings>('/ai/settings/model',{method:'PATCH',body:JSON.stringify({model,reasoningEffort})}),
 workflows:()=>request<{workflows:Graph[]}>('/workflows').then(x=>x.workflows.map(g=>({id:g.workflowId,name:g.name,activeInstanceId:g.activeInstanceId||undefined}))),
 createWorkflow:(body:{name?:string;rootTitle?:string;rootTopicId?:string})=>request<Graph>('/workflows',{method:'POST',body:JSON.stringify(body)}),
 graph:(w:string)=>request<Graph>(`/workflows/${enc(w)}/graph`),
 messages:(w:string,i:string,scope:'local'|'effective'='local')=>request<{messages:Message[]}>(`/workflows/${enc(w)}/instances/${enc(i)}/messages?scope=${scope}`).then(x=>x.messages),
 messageSnapshot:(w:string,i:string,scope:'local'|'effective'='local')=>request<MessageSnapshot>(`/workflows/${enc(w)}/instances/${enc(i)}/messages?scope=${scope}`),
 attachments:(w:string,i:string,scope:'local'|'route'='route')=>request<{attachments:UploadedAttachment[]}>(`/workflows/${enc(w)}/instances/${enc(i)}/attachments?scope=${scope}`).then(x=>x.attachments),
 searchAttachments:(w:string,i:string,query:string,limit=20)=>request<{results:AttachmentSearchResult[]}>(`/workflows/${enc(w)}/instances/${enc(i)}/attachments/search?q=${enc(query)}&limit=${limit}`).then(x=>x.results),
 attachmentSearchStatus:(w:string,i:string)=>request<AttachmentSearchStatus>(`/workflows/${enc(w)}/instances/${enc(i)}/attachments/search-status`),
 attachment:(w:string,i:string,id:string)=>request<UploadedAttachment>(`/workflows/${enc(w)}/instances/${enc(i)}/attachments/${enc(id)}`),
 uploadAttachment:async(w:string,i:string,file:File,onProgress?:(progress:AttachmentUploadProgress)=>void)=>{
  if(file.size>RESUMABLE_UPLOAD_THRESHOLD){
   const resumeKey=uploadResumeStorageKey(w,i,file),clientKey=readUploadClientKey(resumeKey)||crypto.randomUUID();
   writeUploadClientKey(resumeKey,clientKey);
   const base=`/workflows/${enc(w)}/instances/${enc(i)}/attachment-uploads`;
   const session=await request<AttachmentUploadSession>(base,{method:'POST',body:JSON.stringify({clientKey,name:file.name,mimeType:file.type||'application/octet-stream',size:file.size,chunkSize:ATTACHMENT_CHUNK_SIZE})});
   const uploadedIndexes=new Set(session.receivedChunks);
   let uploadedChunks=uploadedIndexes.size;
   const uploadedBytes=()=>[...uploadedIndexes].reduce((total,index)=>total+Math.max(0,Math.min(session.chunkSize,file.size-index*session.chunkSize)),0);
   const report=()=>onProgress?.({uploadedBytes:uploadedBytes(),totalBytes:file.size,uploadedChunks,totalChunks:session.totalChunks,resumedChunks:session.receivedChunks.length});
   report();
   if(session.status==='completed'&&session.attachmentId){
    for(let index=0;index<session.totalChunks;index++)uploadedIndexes.add(index);uploadedChunks=session.totalChunks;report();
    const attachment=await request<UploadedAttachment>(`/workflows/${enc(w)}/instances/${enc(i)}/attachments/${enc(session.attachmentId)}`);writeUploadClientKey(resumeKey,null);return attachment;
   }
   for(const index of session.missingChunks){
    const start=index*session.chunkSize,end=Math.min(file.size,start+session.chunkSize),chunk=file.slice(start,end);
    await retryRawRequest<AttachmentUploadSession>(`${base}/${enc(session.uploadId)}/chunks/${index}`,{method:'PUT',headers:{'Content-Type':'application/octet-stream'},body:chunk});
    uploadedIndexes.add(index);uploadedChunks=uploadedIndexes.size;report();
   }
   const attachment=await request<UploadedAttachment>(`${base}/${enc(session.uploadId)}/complete`,{method:'POST'});writeUploadClientKey(resumeKey,null);return attachment;
  }
  onProgress?.({uploadedBytes:0,totalBytes:file.size,uploadedChunks:0,totalChunks:1,resumedChunks:0});
  const path=`${BASE}/workflows/${enc(w)}/instances/${enc(i)}/attachments?name=${enc(file.name)}&mimeType=${enc(file.type||'text/plain')}`;
  const response=await fetch(path,{method:'POST',headers:{Accept:'application/json','Content-Type':file.type||'application/octet-stream'},body:file});
  const data=await response.json().catch(()=>({}))as ApiErrorPayload;
  if(!response.ok)throw new ApiError(data.message||data.error||`HTTP ${response.status}`,response.status,data.code);
  onProgress?.({uploadedBytes:file.size,totalBytes:file.size,uploadedChunks:1,totalChunks:1,resumedChunks:0});
  return data as UploadedAttachment;
 },
 attachmentUpload:(w:string,i:string,id:string)=>request<AttachmentUploadSession>(`/workflows/${enc(w)}/instances/${enc(i)}/attachment-uploads/${enc(id)}`),
 cancelAttachmentUpload:(w:string,i:string,id:string)=>request<{ok:boolean;uploadId:string}>(`/workflows/${enc(w)}/instances/${enc(i)}/attachment-uploads/${enc(id)}`,{method:'DELETE'}),
 reparseAttachment:(w:string,i:string,id:string)=>request<UploadedAttachment>(`/workflows/${enc(w)}/instances/${enc(i)}/attachments/${enc(id)}/reparse`,{method:'POST'}),
 deleteAttachment:(w:string,i:string,id:string)=>request<{ok:boolean;attachmentId:string}>(`/workflows/${enc(w)}/instances/${enc(i)}/attachments/${enc(id)}`,{method:'DELETE'}),
 contextPreview:(w:string,i:string,maxChars=120000)=>request<ContextPreview>(`/workflows/${enc(w)}/instances/${enc(i)}/context-preview?maxChars=${maxChars}`),
 hostCapabilities:()=>request<{adapter:string;capabilities:Record<string,unknown>}>('/host/capabilities'),
 turns:(w:string,i:string)=>request<TurnCanvasSnapshot>(`/workflows/${enc(w)}/instances/${enc(i)}/turn-tree`),
 regenerate:(w:string,i:string,messageId:string|number,content:string,expectedRevision:number)=>request<MessageSnapshot>(`/workflows/${enc(w)}/instances/${enc(i)}/messages/${enc(String(messageId))}/regenerate`,{method:'POST',body:JSON.stringify({content,expectedRevision})}),
 send:(w:string,i:string,content:string)=>request<Message>(`/workflows/${enc(w)}/instances/${enc(i)}/messages`,{method:'POST',body:JSON.stringify({role:'user',content})}),
 chat:(w:string,i:string,content:string,idempotencyKey?:string)=>request<{userMessage:Message;assistantMessage:Message}>(`/workflows/${enc(w)}/instances/${enc(i)}/chat`,{method:'POST',body:JSON.stringify({content,...(idempotencyKey?{idempotencyKey}:{})})}),
 chatStream:async(w:string,i:string,content:string,idempotencyKey:string,onEvent:(event:string,data:ChatStreamEvent)=>void,signal?:AbortSignal)=>{
  const response=await fetch(`${BASE}/workflows/${enc(w)}/instances/${enc(i)}/chat/stream`,{method:'POST',signal,headers:{Accept:'text/event-stream','Content-Type':'application/json'},body:JSON.stringify({content,idempotencyKey})});
  if(!response.ok){const data=await response.json().catch(()=>({}))as ApiErrorPayload;throw new ApiError(data.message||data.error||`HTTP ${response.status}`,response.status,data.code,data.runId,data.diagnostics)}
  if(!response.body)throw new ApiError('Streaming response is unavailable',502,'aiUnavailable');
  const reader=response.body.getReader(),decoder=new TextDecoder();let buffer='';
  const consume=(chunk:string)=>{buffer+=chunk;const frames=buffer.split(/\r?\n\r?\n/);buffer=frames.pop()||'';for(const frame of frames){let event='message',payload='';for(const line of frame.split(/\r?\n/)){if(line.startsWith('event:'))event=line.slice(6).trim();else if(line.startsWith('data:'))payload+=line.slice(5).trim()}if(payload){try{onEvent(event,JSON.parse(payload)as ChatStreamEvent)}catch{/* Ignore malformed provider frames. */}}}};
  try{while(true){const part=await reader.read();if(part.done)break;consume(decoder.decode(part.value,{stream:true}))}consume(decoder.decode())}finally{reader.releaseLock()}
 },
 cancelChat:(w:string,i:string,requestId:string)=>request<{ok:boolean;requestId:string;cancelled:boolean}>(`/workflows/${enc(w)}/instances/${enc(i)}/chat/${enc(requestId)}/cancel`,{method:'POST'}),
 fork:(w:string,i:string,body:{title?:string;topicId?:string;initialMessage?:string;anchorMessageId?:string|number;expectedContentRevision?:number;idempotencyKey?:string})=>request<ForkResponse>(`/workflows/${enc(w)}/instances/${enc(i)}/fork`,{method:'POST',body:JSON.stringify(body)}),
 forkChat:(w:string,i:string,body:{title?:string;topicId?:string;initialMessage?:string;anchorMessageId?:string|number;expectedContentRevision?:number;idempotencyKey?:string})=>request<ForkChatResponse>(`/workflows/${enc(w)}/instances/${enc(i)}/fork-chat`,{method:'POST',body:JSON.stringify(body)}),
 renameInstance:(w:string,i:string,title:string,expectedRevision:number)=>request<{node:{id:string;title:string};graphRevision:number;eventRevision:number}>(`/workflows/${enc(w)}/instances/${enc(i)}`,{method:'PATCH',body:JSON.stringify({title,expectedRevision})}),
 renameWorkflow:(w:string,name:string,expectedRevision:number)=>request<{workflowId:string;name:string;graphRevision:number;eventRevision:number}>(`/workflows/${enc(w)}`,{method:'PATCH',body:JSON.stringify({name,expectedRevision})}),
 activate:(w:string,i:string)=>request<{activeInstanceId:string}>(`/workflows/${enc(w)}/instances/${enc(i)}/activate`,{method:'POST',body:'{}'}),
 prunePlan:(w:string,i:string,allowRoot=false)=>request<PrunePlan>(`/workflows/${enc(w)}/instances/${enc(i)}/prune-plan`,{method:'POST',body:JSON.stringify({allowRoot})}),
 pruneCommit:(w:string,i:string,plan:PrunePlan)=>request<{prunedInstanceIds:string[];activeInstanceId?:string}>(`/workflows/${enc(w)}/instances/${enc(i)}/prune-commit`,{method:'POST',body:JSON.stringify({allowRoot:!!plan.rootRemoval,expectedRevision:plan.graphRevision,idempotencyKey:crypto.randomUUID()})}),
 routes:(w:string,t:string)=>request<{routes:Route[]}>(`/workflows/${enc(w)}/topics/${enc(t)}/routes`).then(x=>x.routes)
 ,createAgentRun:(w:string,i:string,body:CreateAgentRunInput)=>request<unknown>(`/workflows/${enc(w)}/instances/${enc(i)}/runs`,{method:'POST',body:JSON.stringify(body)}).then(normalizeRun)
 ,agentRuns:(w:string,i:string)=>request<{runs?:unknown[]} | unknown[]>(`/workflows/${enc(w)}/instances/${enc(i)}/runs`).then(x=>(Array.isArray(x)?x:x.runs||[]).map(normalizeRun))
 ,agentRun:(id:string|number)=>request<unknown>(`/runs/${enc(String(id))}`).then(normalizeRun)
 ,agentRunEvents:(id:string|number,afterSequence=0,limit=100)=>request<AgentRunEvents>(`/runs/${enc(String(id))}/events?afterSequence=${afterSequence}&limit=${limit}`)
 ,cancelAgentRun:(id:string|number)=>request<unknown>(`/runs/${enc(String(id))}/cancel`,{method:'POST'}).then(normalizeRun)
 ,retryAgentRun:(id:string|number,body:RetryAgentRunInput={})=>request<unknown>(`/runs/${enc(String(id))}/retry`,{method:'POST',body:JSON.stringify(body)}).then(normalizeRun)
 ,decideAgentApproval:(id:string|number,approvalId:string,decision:'approved'|'rejected')=>request<unknown>(`/runs/${enc(String(id))}/approvals/${enc(approvalId)}/decision`,{method:'POST',body:JSON.stringify({decision})}).then(normalizeRun)
 ,artifacts:(w:string)=>request<{artifacts:Artifact[]}>(`/workflows/${enc(w)}/artifacts`).then(x=>x.artifacts)
 ,artifact:(w:string,id:string)=>request<Artifact>(`/workflows/${enc(w)}/artifacts/${enc(id)}`)
 ,createArtifact:(w:string,body:{name:string;kind:string;mimeType:string;content?:string;instanceId?:string;runId?:string|number;metadata?:Record<string,unknown>})=>request<Artifact>(`/workflows/${enc(w)}/artifacts`,{method:'POST',body:JSON.stringify(body)})
 ,compareBranches:(w:string,instanceIds:string[])=>request<BranchComparison>(`/workflows/${enc(w)}/comparisons`,{method:'POST',body:JSON.stringify({instanceIds})})
 ,mergeKnowledge:(w:string,body:{targetInstanceId:string;sourceInstanceIds:string[];items:Array<{sourceInstanceId:string;sourceRunId?:string|number;kind:'conclusion'|'decision'|'fact'|'constraint';title:string;content:string}>;artifactIds:string[]})=>request<{mergeId:string;transcriptsMerged:false}>(`/workflows/${enc(w)}/knowledge-merges`,{method:'POST',body:JSON.stringify(body)})
 ,datasets:(w:string)=>request<{datasets:Dataset[]}>(`/workflows/${enc(w)}/datasets`).then(x=>x.datasets)
 ,createDataset:(w:string,body:{name:string;description:string;cases:DatasetCase[]})=>request<Dataset>(`/workflows/${enc(w)}/datasets`,{method:'POST',body:JSON.stringify(body)})
 ,experiments:(w:string)=>request<{experiments:Experiment[]}>(`/workflows/${enc(w)}/experiments`).then(x=>x.experiments)
 ,createExperiment:(w:string,body:{name:string;datasetId:string;instanceIds:string[];runIds:Array<string|number>;metric:string;notes:string})=>request<Experiment>(`/workflows/${enc(w)}/experiments`,{method:'POST',body:JSON.stringify(body)})
};
interface ForkResponse {node:{id:string};graphRevision:number}
interface ForkChatResponse extends ForkResponse {replyStatus:'completed'|'recorded'|'failed';replyErrorCode?:string|null;assistantMessage?:Message|null}
