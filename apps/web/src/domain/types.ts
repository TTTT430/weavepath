export type Status='active'|'pruned'|'creating'|'error';
export interface WorkflowSummary {id:string;name:string;activeInstanceId?:string}
export interface Instance {id:string;workflowId?:string;parentId:string|null;topicId:string;title:string;summary?:string;status:Status;provider?:string;contentRevision?:number}
export interface Graph {workflowId:string;name:string;rootInstanceId:string;activeInstanceId:string|null;graphRevision:number;eventRevision:number;nodes:Instance[]}
export interface Message {id:string|number;role:'user'|'assistant'|'system'|'tool';content:string;createdAt?:string;inherited?:boolean;contentRevision?:number}
export interface MessageSnapshot {messages:Message[];contentRevision:number;eventRevision?:number}
export interface Route {id:string;topicId:string;title:string;memoryRoute:string[];status:Status}
export interface PrunePlan {graphRevision:number;nodes:Array<{id:string;title?:string}>;rootRemoval?:boolean}
export interface ApiErrorPayload {message?:string;error?:string;code?:string}
export interface AIStatus {configured:boolean;provider:string;model:string|null;reason?:string|null}
export interface AISettings extends AIStatus {baseUrl:string|null;timeoutSeconds:number;systemPrompt:string;hasApiKey:boolean;source:string;persistence:'memory'|'local'}
export interface AISettingsInput {baseUrl:string;model:string;apiKey?:string;timeoutSeconds:number;systemPrompt?:string;persistence:'memory'|'local';clearApiKey?:boolean}
export interface AIValidation {ok:boolean;modelCount:number;selectedModelAvailable:boolean;models:string[]}
