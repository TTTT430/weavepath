export type Status='active'|'pruned'|'creating'|'error';
export interface WorkflowSummary {id:string;name:string;activeInstanceId?:string}
export interface Instance {id:string;workflowId?:string;parentId:string|null;topicId:string;title:string;titleGenerated?:boolean;summary?:string;status:Status;provider?:string;contentRevision?:number}
export interface Graph {workflowId:string;name:string;rootInstanceId:string;activeInstanceId:string|null;activeRouteInstanceId?:string|null;activeRouteTitle?:string|null;activeRouteContentRevision?:number;graphRevision:number;eventRevision:number;nodes:Instance[]}
export interface Message {id:string|number;role:'user'|'assistant'|'system'|'tool';content:string;createdAt?:string;inherited?:boolean;contentRevision?:number;graphRevision?:number}
export interface MessageSnapshot {messages:Message[];contentRevision:number;eventRevision?:number}
export interface TurnMemoryRouteNode {instanceId:string;title:string}
export interface TurnRouteNode {routeInstanceId:string;title:string;titleGenerated?:boolean;parentRouteInstanceId:string|null;anchorMessageId:number|null;checkpointAnchor:Record<string,unknown>|null;contentRevision:number;memoryRoute:TurnMemoryRouteNode[];inheritedMessageCount:number;createdAt?:string;updatedAt?:string}
export type ConversationTurnStatus='completed'|'pending'|'running'|'failed'|'interrupted';
export interface ConversationTurn {id:string;sequence:number;anchorMessageId:number;userMessage:Message;responses:Message[];status:ConversationTurnStatus;routeInstanceId?:string;routeTitle?:string;parentTurnId?:string|null}
export interface TurnCanvasSnapshot {workflowId:string;instanceId:string;ownerInstanceId?:string;activeRouteInstanceId?:string;scope:'local';contentRevision:number;eventRevision:number;memoryRoute:TurnMemoryRouteNode[];inheritedMessageCount:number;checkpointAnchor:Record<string,unknown>|null;preamble:Message[];turns:ConversationTurn[];eventExtensions:unknown[];routeContentRevisions?:Record<string,number>;routeMemoryRoutes?:Record<string,TurnMemoryRouteNode[]>;routeInheritedMessageCounts?:Record<string,number>;routeTitles?:Record<string,string>;routeNodes?:TurnRouteNode[]}
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
export interface KnowledgeItem {knowledgeItemId:string;kind:'conclusion'|'decision'|'fact'|'constraint';title:string;content:string;provenance:Record<string,unknown>}
export interface AgentRunMetrics {durationMs:number|null;modelStepCount:number;toolCallCount:number;toolDurationMs:number;inputTokens:number|null;outputTokens:number|null;estimatedCost:number|null}
export interface AgentRun {runId:string|number;workflowId:string;instanceId:string;status:AgentRunStatus;inputContentRevision:number;contextSha256?:string;modelSnapshot?:unknown;memoryRoute?:AgentMemoryRouteNode[];acceptedKnowledge?:KnowledgeItem[];availableTools?:AgentToolSpec[];objective:string;constraints:string[];deliverables:string[];acceptanceChecks:string[];finalMessageId?:string|number|null;finalAnswer?:string|null;errorCode?:string|null;createdAt?:string;updatedAt?:string;steps?:unknown[];toolCalls?:unknown[];toolResults?:unknown[];metrics?:AgentRunMetrics}
export interface AgentRunEvent {sequence:number;type:string;payload:unknown;createdAt?:string}
export interface AgentRunEvents {runId:string|number;events:AgentRunEvent[];nextAfterSequence:number|null}
export interface CreateAgentRunInput {objective:string;constraints:string[];deliverables:string[];acceptanceChecks:string[];expectedContentRevision:number;idempotencyKey:string}
export interface Artifact {artifactId:string;workflowId:string;instanceId:string|null;runId:string|number|null;name:string;version:number;kind:string;mimeType:string;metadata:Record<string,unknown>;sha256:string;size:number;createdAt:string;content?:string}
export interface ComparisonBranch {instanceId:string;topicId:string;title:string;status:Status;memoryRoute:Array<{instanceId:string;title:string}>;localMessageCounts:Record<string,number>;latestRun:{runId:string|number;status:AgentRunStatus;objective:string;modelSnapshot:unknown;finalAnswer:string|null;errorCode:string|null;createdAt:string}|null;artifacts:Artifact[]}
export interface BranchComparison {workflowId:string;instanceIds:string[];sharedRoute:Array<{instanceId:string;title:string}>;branches:ComparisonBranch[];transcriptsIncluded:false}
export interface DatasetCase {id:string;input:string;expected?:string|null;tags:string[]}
export interface Dataset {datasetId:string;workflowId:string;name:string;version:number;description:string;sha256:string;caseCount:number;createdAt:string;cases?:DatasetCase[]}
export interface Experiment {experimentId:string;workflowId:string;name:string;datasetId:string;instanceIds:string[];runIds:Array<string|number>;metric:string;notes:string;snapshot:{dataset:{datasetId:string;name:string;version:number;sha256:string};instances:Array<{instanceId:string;route:string[]}>;runs:Array<{runId:string|number;instanceId:string;status:AgentRunStatus;objective:string;modelSnapshot:unknown;finalAnswer:string|null;errorCode:string|null}>};createdAt:string}
