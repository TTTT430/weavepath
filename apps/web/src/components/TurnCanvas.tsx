import{useEffect,useMemo,useState}from'react';
import{Background,Controls,Handle,MarkerType,MiniMap,Panel,Position,ReactFlow,useNodesState,type Node,type NodeProps,type ReactFlowInstance,type Viewport}from'@xyflow/react';
import type{ConversationTurn,TurnCanvasSnapshot}from'../domain/types';
import type{CanvasPosition}from'../lib/canvasState';

interface TurnData extends Record<string,unknown>{
 turn:ConversationTurn
 collapsed:boolean
 collapseLabel:string
 expandLabel:string
 responseLabel:string
 turnLabel:string
 statusLabels:Record<string,string>
 roleLabels:Record<string,string>
 onSelect:(id:string)=>void
 onToggleCollapse:(id:string)=>void
}
type TurnFlowNode=Node<TurnData,'turn'>;

function excerpt(value:string,limit=220){const clean=value.replace(/\s+/g,' ').trim();return clean.length>limit?`${clean.slice(0,limit)}…`:clean}

function TurnCard({data,selected=false}:{data:TurnData;selected?:boolean}){
 const{turn}=data;
 return <article className={`turn-node ${selected?'is-selected':''} ${data.collapsed?'is-collapsed':''}`} onClick={()=>data.onSelect(turn.id)}>
  <button type="button" className="turn-collapse" aria-label={`${data.collapsed?data.expandLabel:data.collapseLabel}: ${turn.sequence}`} onClick={event=>{event.stopPropagation();data.onToggleCollapse(turn.id)}}>{data.collapsed?'＋':'−'}</button>
  <header><strong>{data.turnLabel} {turn.sequence}</strong><span className={`turn-status ${turn.status}`}>{data.statusLabels[turn.status]||turn.status}</span></header>
  <p className="turn-user">{excerpt(turn.userMessage.content)}</p>
  {!data.collapsed&&<div className="turn-responses"><small>{data.responseLabel}: {turn.responses.length}</small>{turn.responses.map(message=><p key={message.id} className={`turn-response ${message.role}`}><small>{data.roleLabels[message.role]||message.role}</small>{excerpt(message.content)}</p>)}</div>}
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
 onSelect:(id:string)=>void
 onToggleCollapse:(id:string)=>void
 onViewportChange?:(viewport:Viewport)=>void
 onNodePositionChange?:(id:string,position:CanvasPosition)=>void
 labels:{locate:string;fit:string;collapse:string;expand:string;responses:string;empty:string;turn:string;statusLabels:Record<string,string>;roleLabels:Record<string,string>}
}

export function TurnCanvas({snapshot,selectedTurnId,collapsedTurnIds=[],turnPositions={},initialViewport,onSelect,onToggleCollapse,onViewportChange,onNodePositionChange,labels}:TurnCanvasProps){
 const[instance,setInstance]=useState<ReactFlowInstance<TurnFlowNode>|null>(null),collapsed=new Set(collapsedTurnIds);
 const calculated=useMemo<TurnFlowNode[]>(()=>snapshot.turns.map((turn,index)=>({id:turn.id,type:'turn',position:turnPositions[turn.id]||{x:48+index*360,y:90},selected:turn.id===selectedTurnId,data:{turn,collapsed:collapsed.has(turn.id),collapseLabel:labels.collapse,expandLabel:labels.expand,responseLabel:labels.responses,turnLabel:labels.turn,statusLabels:labels.statusLabels,roleLabels:labels.roleLabels,onSelect,onToggleCollapse}})),[snapshot.turns,selectedTurnId,collapsedTurnIds,turnPositions,labels,onSelect,onToggleCollapse]);
 const[nodes,setNodes,onNodesChange]=useNodesState<TurnFlowNode>(calculated);
 useEffect(()=>setNodes(calculated),[calculated,setNodes]);
 const edges=useMemo(()=>nodes.slice(1).map((node,index)=>({id:`turn-edge-${nodes[index].id}-${node.id}`,source:nodes[index].id,target:node.id,type:'smoothstep',markerEnd:{type:MarkerType.ArrowClosed}})),[nodes]);
 const locate=()=>{const selected=nodes.filter(node=>node.id===selectedTurnId);void instance?.fitView({nodes:selected.length?selected:nodes,duration:220,padding:.6})};
 const fit=()=>void instance?.fitView({nodes,duration:220,padding:.2});
 return <div className="turn-canvas">
  {!nodes.length&&<p className="turn-canvas-empty">{labels.empty}</p>}
  <ReactFlow<TurnFlowNode> nodes={nodes} edges={edges} nodeTypes={nodeTypes} fitView={!initialViewport} defaultViewport={initialViewport} nodesDraggable elementsSelectable={false} nodesConnectable={false} onlyRenderVisibleElements onNodesChange={onNodesChange} onNodeDragStop={(_event,node)=>onNodePositionChange?.(node.id,node.position)} onInit={setInstance} onMoveEnd={(_event,viewport)=>onViewportChange?.(viewport)}>
   <Background gap={16} size={1}/>{nodes.length>=8&&<MiniMap/>}<Controls/>
   <Panel position="top-right" className="canvas-tools"><button type="button" onClick={locate} disabled={!nodes.length} aria-label={labels.locate}>◎</button><button type="button" onClick={fit} disabled={!nodes.length} aria-label={labels.fit}>↔</button></Panel>
  </ReactFlow>
 </div>;
}
