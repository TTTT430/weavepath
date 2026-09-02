import{useEffect,useMemo,useState}from'react';
import{Background,Controls,Handle,MiniMap,Panel,Position,ReactFlow,useNodesState,type Node,type NodeProps,type ReactFlowInstance,type Viewport}from'@xyflow/react';
import type{ConversationTurn,Message,TurnCanvasSnapshot}from'../domain/types';
import type{CanvasPosition}from'../lib/canvasState';

interface CanvasTurn extends ConversationTurn{isRoutePlaceholder?:boolean}
interface TurnData extends Record<string,unknown>{
 turn:CanvasTurn
 collapsed:boolean
 collapseLabel:string
 expandLabel:string
 responseLabel:string
 turnLabel:string
 statusLabels:Record<string,string>
 roleLabels:Record<string,string>
 branchLabel:string
 detailsLabel:string
 emptyBranchLabel:string
 onSelect:(id:string,routeInstanceId:string)=>void
 onToggleCollapse:(id:string)=>void
 onBranch?:(turn:ConversationTurn)=>void
}
type TurnFlowNode=Node<TurnData,'turn'>;

function excerpt(value:string,limit=420){const clean=value.trim();return clean.length>limit?`${clean.slice(0,limit)}…`:clean}

function TurnCard({data,selected=false}:{data:TurnData;selected?:boolean}){
 const{turn}=data;
 const placeholder=!!turn.isRoutePlaceholder,routeId=turn.routeInstanceId||'';
 return <article className={`turn-node ${selected?'is-selected':''} ${data.collapsed?'is-collapsed':''} ${placeholder?'is-route-placeholder':''}`} onClick={()=>data.onSelect(turn.id,routeId)} onDoubleClick={event=>{event.stopPropagation();data.onSelect(turn.id,routeId)}} title={data.detailsLabel}>
  <span className="node-drag-handle" aria-hidden="true">•••</span>
  <button type="button" className="turn-collapse" aria-label={`${data.collapsed?data.expandLabel:data.collapseLabel}: ${turn.sequence}`} onClick={event=>{event.stopPropagation();data.onToggleCollapse(turn.id)}}>{data.collapsed?'＋':'−'}</button>
  {!placeholder&&data.onBranch&&<button type="button" className="turn-branch" aria-label={`${data.branchLabel}: ${turn.sequence}`} title={data.branchLabel} onClick={event=>{event.stopPropagation();data.onSelect(turn.id,routeId);data.onBranch?.(turn)}}>＋</button>}
  <header><strong>{placeholder?(turn.routeTitle||data.turnLabel):`${data.turnLabel} ${turn.sequence}`}</strong>{!placeholder&&turn.routeTitle&&<small className="turn-route-title">{turn.routeTitle}</small>}<span className={`turn-status ${turn.status}`}>{data.statusLabels[turn.status]||turn.status}</span></header>
  <p className="turn-user">{excerpt(turn.userMessage.content)}</p>
  {!data.collapsed&&<div className="turn-responses"><small>{data.responseLabel}: {turn.responses.length}</small>{turn.responses.map(message=><p key={message.id} className={`turn-response ${message.role}`}><small>{data.roleLabels[message.role]||message.role}</small>{excerpt(message.content)}</p>)}</div>}
  <footer className="turn-node-footer"><button type="button" onClick={event=>{event.stopPropagation();data.onSelect(turn.id,routeId)}}>◎ {data.detailsLabel}</button></footer>
 </article>;
}

function TurnNode({data,selected}:NodeProps<TurnFlowNode>){return <><Handle type="target" position={Position.Left}/><TurnCard data={data} selected={selected}/><Handle type="source" position={Position.Right}/></>}
const nodeTypes={turn:TurnNode};

export interface TurnCanvasProps{
 snapshot:TurnCanvasSnapshot
 selectedTurnId:string
 collapsedTurnIds?:string[]
 turnPositions?:Record<string,CanvasPosition>
 initialViewport?:Viewport
 onSelect:(id:string,routeInstanceId:string)=>void
 onToggleCollapse:(id:string)=>void
 onViewportChange?:(viewport:Viewport)=>void
 onNodePositionChange?:(id:string,position:CanvasPosition)=>void
 onBranch?:(turn:ConversationTurn)=>void
 labels:{locate:string;fit:string;collapse:string;expand:string;responses:string;empty:string;emptyBranch:string;turn:string;branch:string;details:string;statusLabels:Record<string,string>;roleLabels:Record<string,string>}
}

function emptyMessage(id:string,content:string):Message{return{id,role:'user',content}}

export function canvasTurns(snapshot:TurnCanvasSnapshot,emptyBranchLabel:string):CanvasTurn[]{
 const turns:CanvasTurn[]=snapshot.turns.map(turn=>({...turn}));
 const routes=(snapshot.routeNodes||[]).filter(route=>(route as TurnRouteNodeWithStatus).status!=='pruned');
 if(!routes.length)return turns;
 const represented=new Set(turns.map(turn=>turn.routeInstanceId||snapshot.instanceId));
 const turnByAnchor=new Map(turns.map(turn=>[`${turn.routeInstanceId||snapshot.instanceId}:${turn.anchorMessageId}`,turn.id]));
 for(const route of routes){
  if(represented.has(route.routeInstanceId))continue;
  const parentTurnId=route.parentRouteInstanceId&&route.anchorMessageId?turnByAnchor.get(`${route.parentRouteInstanceId}:${route.anchorMessageId}`)||null:null;
  turns.push({id:`route:${route.routeInstanceId}`,sequence:0,anchorMessageId:route.anchorMessageId||0,userMessage:emptyMessage(`route-message:${route.routeInstanceId}`,emptyBranchLabel),responses:[],status:'pending',routeInstanceId:route.routeInstanceId,routeTitle:route.title,parentTurnId,isRoutePlaceholder:true});
 }
 return turns;
}

type TurnRouteNodeWithStatus=TurnCanvasSnapshot['routeNodes'] extends Array<infer T>?T&{status?:string}:{status?:string};

export function TurnCanvas({snapshot,selectedTurnId,collapsedTurnIds=[],turnPositions={},initialViewport,onSelect,onToggleCollapse,onViewportChange,onNodePositionChange,onBranch,labels}:TurnCanvasProps){
 const[instance,setInstance]=useState<ReactFlowInstance<TurnFlowNode>|null>(null),collapsed=new Set(collapsedTurnIds);
 const normalizedTurns=useMemo(()=>{const items=canvasTurns(snapshot,labels.emptyBranch);return items.map((turn,index)=>({...turn,parentTurnId:turn.parentTurnId===undefined?(items[index-1]?.id||null):turn.parentTurnId}))},[snapshot,labels.emptyBranch]);
 const calculated=useMemo<TurnFlowNode[]>(()=>{const map=new Map(normalizedTurns.map(turn=>[turn.id,turn])),depths=new Map<string,number>(),rows=new Map<number,number>();const depth=(turn:CanvasTurn):number=>{if(depths.has(turn.id))return depths.get(turn.id)!;const value=turn.parentTurnId&&map.has(turn.parentTurnId)?depth(map.get(turn.parentTurnId)!)+1:0;depths.set(turn.id,value);return value};return normalizedTurns.map(turn=>{const column=depth(turn),row=rows.get(column)||0;rows.set(column,row+1);return{id:turn.id,type:'turn',position:turnPositions[turn.id]||{x:48+column*365,y:52+row*320},selected:turn.id===selectedTurnId,data:{turn,collapsed:collapsed.has(turn.id),collapseLabel:labels.collapse,expandLabel:labels.expand,responseLabel:labels.responses,turnLabel:labels.turn,branchLabel:labels.branch,detailsLabel:labels.details,emptyBranchLabel:labels.emptyBranch,statusLabels:labels.statusLabels,roleLabels:labels.roleLabels,onSelect,onToggleCollapse,onBranch}}})},[normalizedTurns,selectedTurnId,collapsedTurnIds,turnPositions,labels,onSelect,onToggleCollapse,onBranch]);
 const[nodes,setNodes,onNodesChange]=useNodesState<TurnFlowNode>(calculated);
 useEffect(()=>setNodes(calculated),[calculated,setNodes]);
 const edges=useMemo(()=>normalizedTurns.filter(turn=>turn.parentTurnId).map(turn=>({id:`turn-edge-${turn.parentTurnId}-${turn.id}`,source:turn.parentTurnId!,target:turn.id,type:'default',className:turn.id===selectedTurnId?'is-path-active':''})),[normalizedTurns,selectedTurnId]);
 const locate=()=>{const selected=nodes.filter(node=>node.id===selectedTurnId);void instance?.fitView({nodes:selected.length?selected:nodes,duration:220,padding:.6,maxZoom:1.05})};
 const fit=()=>void instance?.fitView({nodes,duration:220,padding:.24,maxZoom:1});
 return <div className="turn-canvas">
  {!nodes.length&&<p className="turn-canvas-empty">{labels.empty}</p>}
  <ReactFlow<TurnFlowNode> className="synapse-flow" nodes={nodes} edges={edges} nodeTypes={nodeTypes} fitView={!initialViewport} fitViewOptions={{padding:.2,maxZoom:1}} defaultViewport={initialViewport} minZoom={.25} maxZoom={1.5} nodesDraggable elementsSelectable={false} nodesConnectable={false} onlyRenderVisibleElements onNodesChange={onNodesChange} onNodeDragStop={(_event,node)=>onNodePositionChange?.(node.id,node.position)} onInit={setInstance} onMoveEnd={(_event,viewport)=>onViewportChange?.(viewport)}>
   <Background color="var(--canvas-grid-dot)" gap={20} size={1}/>{nodes.length>=8&&<MiniMap/>}<Controls/>
   <Panel position="top-right" className="canvas-tools"><button type="button" onClick={locate} disabled={!nodes.length} aria-label={labels.locate}>◎</button><button type="button" onClick={fit} disabled={!nodes.length} aria-label={labels.fit}>↔</button></Panel>
  </ReactFlow>
 </div>;
}
