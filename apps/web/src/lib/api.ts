import type {AISettings,AISettingsInput,AIStatus,AIValidation,Graph,Message,MessageSnapshot,PrunePlan,Route,WorkflowSummary} from '../domain/types';
const BASE='/api/v1';
export class ApiError extends Error {constructor(message:string,public status:number,public code?:string){super(message)}}
async function request<T>(path:string,init?:RequestInit):Promise<T>{const response=await fetch(BASE+path,{...init,headers:{Accept:'application/json',...(init?.body?{'Content-Type':'application/json'}:{}),...init?.headers}}); const data=await response.json().catch(()=>({})); if(!response.ok)throw new ApiError(data.message||data.error||`HTTP ${response.status}`,response.status,data.code); return data as T}
const enc=encodeURIComponent;
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
 regenerate:(w:string,i:string,messageId:string|number,content:string,expectedRevision:number)=>request<MessageSnapshot>(`/workflows/${enc(w)}/instances/${enc(i)}/messages/${enc(String(messageId))}/regenerate`,{method:'POST',body:JSON.stringify({content,expectedRevision})}),
 send:(w:string,i:string,content:string)=>request<Message>(`/workflows/${enc(w)}/instances/${enc(i)}/messages`,{method:'POST',body:JSON.stringify({role:'user',content})}),
 chat:(w:string,i:string,content:string)=>request<{userMessage:Message;assistantMessage:Message}>(`/workflows/${enc(w)}/instances/${enc(i)}/chat`,{method:'POST',body:JSON.stringify({content})}),
 fork:(w:string,i:string,body:{title:string;topicId?:string;initialMessage?:string})=>request<ForkResponse>(`/workflows/${enc(w)}/instances/${enc(i)}/fork`,{method:'POST',body:JSON.stringify(body)}),
 activate:(w:string,i:string)=>request<{activeInstanceId:string}>(`/workflows/${enc(w)}/instances/${enc(i)}/activate`,{method:'POST',body:'{}'}),
 prunePlan:(w:string,i:string,allowRoot=false)=>request<PrunePlan>(`/workflows/${enc(w)}/instances/${enc(i)}/prune-plan`,{method:'POST',body:JSON.stringify({allowRoot})}),
 pruneCommit:(w:string,i:string,plan:PrunePlan)=>request<{prunedInstanceIds:string[];activeInstanceId?:string}>(`/workflows/${enc(w)}/instances/${enc(i)}/prune-commit`,{method:'POST',body:JSON.stringify({allowRoot:!!plan.rootRemoval,expectedRevision:plan.graphRevision,idempotencyKey:crypto.randomUUID()})}),
 routes:(w:string,t:string)=>request<{routes:Route[]}>(`/workflows/${enc(w)}/topics/${enc(t)}/routes`).then(x=>x.routes)
};
interface ForkResponse {node:{id:string};graphRevision:number}
