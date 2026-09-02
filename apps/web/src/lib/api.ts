import type {AgentMemoryRouteNode,AgentRun,AgentRunEvents,AgentToolSpec,ApiErrorPayload,Artifact,BranchComparison,CreateAgentRunInput,Dataset,DatasetCase,Experiment,AISettings,AISettingsInput,AIStatus,AIValidation,Graph,Message,MessageSnapshot,PrunePlan,Route,TurnCanvasSnapshot,WorkflowSummary} from '../domain/types';
const BASE='/api/v1';
export class ApiError extends Error {
 constructor(message:string,public status:number,public code?:string,public runId?:string|number){super(message);this.name='ApiError'}
}
async function request<T>(path:string,init?:RequestInit):Promise<T>{const response=await fetch(BASE+path,{...init,headers:{Accept:'application/json',...(init?.body?{'Content-Type':'application/json'}:{}),...init?.headers}});const data=await response.json().catch(()=>({}))as ApiErrorPayload;if(!response.ok)throw new ApiError(data.message||data.error||`HTTP ${response.status}`,response.status,data.code,data.runId);return data as T}
const enc=encodeURIComponent;
function normalizeTools(value:unknown):AgentToolSpec[]|undefined{return Array.isArray(value)?value.flatMap(item=>{if(!item||typeof item!=='object')return[];const x=item as Record<string,unknown>;return typeof x.name==='string'&&typeof x.version==='string'?[{name:x.name,version:x.version,...(typeof x.description==='string'?{description:x.description}:{})}]:[]}):undefined}
function normalizeMemoryRoute(value:unknown):AgentMemoryRouteNode[]|undefined{return Array.isArray(value)?value.flatMap(item=>{if(!item||typeof item!=='object')return[];const x=item as Record<string,unknown>;return typeof x.instanceId==='string'&&typeof x.topicId==='string'&&typeof x.title==='string'?[{instanceId:x.instanceId,topicId:x.topicId,title:x.title}]:[]}):undefined}
function normalizeRun(value:unknown):AgentRun{const x=(value&&typeof value==='object'?value:{})as Record<string,unknown>;return{runId:(x.runId??x.id??'')as string|number,workflowId:String(x.workflowId??''),instanceId:String(x.instanceId??''),status:String(x.status??'queued')as AgentRun['status'],inputContentRevision:Number(x.inputContentRevision??x.expectedContentRevision??0),contextSha256:typeof x.contextSha256==='string'?x.contextSha256:undefined,modelSnapshot:x.modelSnapshot,memoryRoute:normalizeMemoryRoute(x.memoryRoute),acceptedKnowledge:Array.isArray(x.acceptedKnowledge)?x.acceptedKnowledge as AgentRun['acceptedKnowledge']:undefined,availableTools:normalizeTools(x.availableTools),objective:String(x.objective??''),constraints:Array.isArray(x.constraints)?x.constraints.map(String):[],deliverables:Array.isArray(x.deliverables)?x.deliverables.map(String):[],acceptanceChecks:Array.isArray(x.acceptanceChecks)?x.acceptanceChecks.map(String):[],finalMessageId:(x.finalMessageId??null)as string|number|null,finalAnswer:typeof x.finalAnswer==='string'?x.finalAnswer:null,errorCode:typeof x.errorCode==='string'?x.errorCode:null,createdAt:typeof x.createdAt==='string'?x.createdAt:undefined,updatedAt:typeof x.updatedAt==='string'?x.updatedAt:undefined,steps:Array.isArray(x.steps)?x.steps:undefined,toolCalls:Array.isArray(x.toolCalls)?x.toolCalls:undefined,toolResults:Array.isArray(x.toolResults)?x.toolResults:undefined,metrics:x.metrics&&typeof x.metrics==='object'?x.metrics as AgentRun['metrics']:undefined}}
export const api={
 aiStatus:()=>request<AIStatus>('/ai/status'),
 aiSettings:()=>request<AISettings>('/ai/settings'),
 saveAISettings:(body:AISettingsInput)=>request<AISettings>('/ai/settings',{method:'PUT',body:JSON.stringify(body)}),
 resetAISettings:()=>request<AISettings>('/ai/settings',{method:'DELETE'}),
 validateAISettings:(body:AISettingsInput)=>request<AIValidation>('/ai/settings/validate',{method:'POST',body:JSON.stringify(body)}),
 workflows:()=>request<{workflows:Graph[]}>('/workflows').then(x=>x.workflows.map(g=>({id:g.workflowId,name:g.name,activeInstanceId:g.activeInstanceId||undefined}))),
 createWorkflow:(body:{name:string;rootTitle:string;rootTopicId:string})=>request<Graph>('/workflows',{method:'POST',body:JSON.stringify(body)}),
 graph:(w:string)=>request<Graph>(`/workflows/${enc(w)}/graph`),
 messages:(w:string,i:string,scope:'local'|'effective'='local')=>request<{messages:Message[]}>(`/workflows/${enc(w)}/instances/${enc(i)}/messages?scope=${scope}`).then(x=>x.messages),
 messageSnapshot:(w:string,i:string,scope:'local'|'effective'='local')=>request<MessageSnapshot>(`/workflows/${enc(w)}/instances/${enc(i)}/messages?scope=${scope}`),
 turns:(w:string,i:string)=>request<TurnCanvasSnapshot>(`/workflows/${enc(w)}/instances/${enc(i)}/turn-tree`),
 regenerate:(w:string,i:string,messageId:string|number,content:string,expectedRevision:number)=>request<MessageSnapshot>(`/workflows/${enc(w)}/instances/${enc(i)}/messages/${enc(String(messageId))}/regenerate`,{method:'POST',body:JSON.stringify({content,expectedRevision})}),
 send:(w:string,i:string,content:string)=>request<Message>(`/workflows/${enc(w)}/instances/${enc(i)}/messages`,{method:'POST',body:JSON.stringify({role:'user',content})}),
 chat:(w:string,i:string,content:string)=>request<{userMessage:Message;assistantMessage:Message}>(`/workflows/${enc(w)}/instances/${enc(i)}/chat`,{method:'POST',body:JSON.stringify({content})}),
 fork:(w:string,i:string,body:{title?:string;topicId?:string;initialMessage?:string;anchorMessageId?:string|number;expectedContentRevision?:number;idempotencyKey?:string})=>request<ForkResponse>(`/workflows/${enc(w)}/instances/${enc(i)}/fork`,{method:'POST',body:JSON.stringify(body)}),
 forkChat:(w:string,i:string,body:{title?:string;topicId?:string;initialMessage?:string;anchorMessageId?:string|number;expectedContentRevision?:number;idempotencyKey?:string})=>request<ForkChatResponse>(`/workflows/${enc(w)}/instances/${enc(i)}/fork-chat`,{method:'POST',body:JSON.stringify(body)}),
 renameInstance:(w:string,i:string,title:string,expectedRevision:number)=>request<{node:{id:string;title:string};graphRevision:number;eventRevision:number}>(`/workflows/${enc(w)}/instances/${enc(i)}`,{method:'PATCH',body:JSON.stringify({title,expectedRevision})}),
 activate:(w:string,i:string)=>request<{activeInstanceId:string}>(`/workflows/${enc(w)}/instances/${enc(i)}/activate`,{method:'POST',body:'{}'}),
 prunePlan:(w:string,i:string,allowRoot=false)=>request<PrunePlan>(`/workflows/${enc(w)}/instances/${enc(i)}/prune-plan`,{method:'POST',body:JSON.stringify({allowRoot})}),
 pruneCommit:(w:string,i:string,plan:PrunePlan)=>request<{prunedInstanceIds:string[];activeInstanceId?:string}>(`/workflows/${enc(w)}/instances/${enc(i)}/prune-commit`,{method:'POST',body:JSON.stringify({allowRoot:!!plan.rootRemoval,expectedRevision:plan.graphRevision,idempotencyKey:crypto.randomUUID()})}),
 routes:(w:string,t:string)=>request<{routes:Route[]}>(`/workflows/${enc(w)}/topics/${enc(t)}/routes`).then(x=>x.routes)
 ,createAgentRun:(w:string,i:string,body:CreateAgentRunInput)=>request<unknown>(`/workflows/${enc(w)}/instances/${enc(i)}/runs`,{method:'POST',body:JSON.stringify(body)}).then(normalizeRun)
 ,agentRuns:(w:string,i:string)=>request<{runs?:unknown[]} | unknown[]>(`/workflows/${enc(w)}/instances/${enc(i)}/runs`).then(x=>(Array.isArray(x)?x:x.runs||[]).map(normalizeRun))
 ,agentRun:(id:string|number)=>request<unknown>(`/runs/${enc(String(id))}`).then(normalizeRun)
 ,agentRunEvents:(id:string|number,afterSequence=0,limit=100)=>request<AgentRunEvents>(`/runs/${enc(String(id))}/events?afterSequence=${afterSequence}&limit=${limit}`)
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
