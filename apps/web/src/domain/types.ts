export type Status='active'|'pruned'|'creating'|'error';
export interface WorkflowSummary {id:string;name:string;activeInstanceId?:string}
export interface Instance {id:string;workflowId?:string;parentId:string|null;topicId:string;title:string;summary?:string;status:Status;provider?:string;contentRevision?:number}
export interface Graph {workflowId:string;name:string;rootInstanceId:string;activeInstanceId:string|null;graphRevision:number;eventRevision:number;nodes:Instance[]}
export interface Message {id:string|number;role:'user'|'assistant'|'system'|'tool';content:string;createdAt?:string;inherited?:boolean;contentRevision?:number}
export interface MessageSnapshot {messages:Message[];contentRevision:number;eventRevision?:number}
export interface TurnMemoryRouteNode {instanceId:string;title:string}
export type ConversationTurnStatus='completed'|'pending'|'running'|'failed'|'interrupted';
export interface ConversationTurn {id:string;sequence:number;anchorMessageId:number;userMessage:Message;responses:Message[];status:ConversationTurnStatus}
export interface TurnCanvasSnapshot {workflowId:string;instanceId:string;scope:'local';contentRevision:number;eventRevision:number;memoryRoute:TurnMemoryRouteNode[];inheritedMessageCount:number;checkpointAnchor:Record<string,unknown>|null;preamble:Message[];turns:ConversationTurn[];eventExtensions:unknown[]}
export interface Route {id:string;topicId:string;title:string;memoryRoute:string[];status:Status}
export interface PrunePlan {graphRevision:number;nodes:Array<{id:string;title?:string}>;rootRemoval?:boolean}
export interface ApiErrorPayload {message?:string;error?:string;code?:string;runId?:string|number}
export interface AIStatus {configured:boolean;provider:string;model:string|null;reason?:string|null}
export interface AISettings extends AIStatus {baseUrl:string|null;timeoutSeconds:number;systemPrompt:string;hasApiKey:boolean;source:string;persistence:'memory'|'local'}
export interface AISettingsInput {baseUrl:string;model:string;apiKey?:string;timeoutSeconds:number;systemPrompt?:string;persistence:'memory'|'local';clearApiKey?:boolean}
export interface AIValidation {ok:boolean;modelCount:number;selectedModelAvailable:boolean;models:string[]}
export type AgentRunStatus='queued'|'running'|'completed'|'failed'|'interrupted';
export interface AgentToolSpec {name:string;version:string;description?:string}
export interface AgentMemoryRouteNode {instanceId:string;topicId:string;title:string}
export interface AgentRun {runId:string|number;workflowId:string;instanceId:string;status:AgentRunStatus;inputContentRevision:number;contextSha256?:string;modelSnapshot?:unknown;memoryRoute?:AgentMemoryRouteNode[];availableTools?:AgentToolSpec[];objective:string;constraints:string[];deliverables:string[];acceptanceChecks:string[];finalMessageId?:string|number|null;finalAnswer?:string|null;errorCode?:string|null;createdAt?:string;updatedAt?:string;steps?:unknown[];toolCalls?:unknown[];toolResults?:unknown[]}
export interface AgentRunEvent {sequence:number;type:string;payload:unknown;createdAt?:string}
export interface AgentRunEvents {runId:string|number;events:AgentRunEvent[];nextAfterSequence:number|null}
export interface CreateAgentRunInput {objective:string;constraints:string[];deliverables:string[];acceptanceChecks:string[];expectedContentRevision:number;idempotencyKey:string}
