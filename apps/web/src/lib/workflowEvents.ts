/** Cross-surface lifecycle event for a conversation request.
 *
 * `phase` is intentionally optional so older callers and cached browser
 * bundles can continue to consume the original graph-refresh event.  New
 * callers include the phase and request content, allowing the chat and canvas
 * surfaces to show the same in-progress/failed state before an assistant
 * message has been committed.
 */
export interface WorkflowChangedEvent{
 type:'conversation-workflow-changed';
 workflowId:string;
 instanceId?:string;
 phase?:'started'|'completed'|'failed'|'cancelled';
 requestId?:string;
 /** Identifies the surface that emitted the event so it can ignore its own echo. */
 senderId?:string;
 /** Millisecond timestamp used to discard lifecycle events queued before a surface mounted. */
 sentAt?:number;
 content?:string;
 error?:string;
}
interface ChannelLike{postMessage:(value:WorkflowChangedEvent)=>void;close:()=>void}
export function notifyWorkflowChanged(event:WorkflowChangedEvent,deps?:{createChannel?:()=>ChannelLike;opener?:Pick<Window,'postMessage'>|null;origin?:string}){const create=deps?.createChannel||(()=>new BroadcastChannel('conversation-workflow'));const channel=create();channel.postMessage(event);channel.close();const opener=deps?.opener===undefined?window.opener:deps.opener;if(opener&&opener!==window)opener.postMessage(event,deps?.origin||location.origin)}
