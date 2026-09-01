import{useEffect,useMemo,useRef,useState,type MouseEvent}from'react';
import{Background,Controls,Handle,MarkerType,MiniMap,Panel,Position,ReactFlow,useNodesState,type Node,type NodeProps,type ReactFlowInstance,type Viewport}from'@xyflow/react';
import'@xyflow/react/dist/style.css';
import type{Graph,Instance}from'../domain/types';
import{graphEdges,memoryPath}from'../domain/graph';
import type{CanvasPosition}from'../lib/canvasState';

interface ConversationData extends Record<string,unknown>{
 instance:Instance
 active:boolean
 collapsed:boolean
 hasChildren:boolean
 collapseLabel:string
 expandLabel:string
 onSelect:(id:string)=>void
 onOpenCanvas:(id:string)=>void
 onToggleCollapse:(id:string)=>void
}
type ConversationFlowNode=Node<ConversationData,'conversation'>;

function hiddenByCollapsed(node:Instance,map:Map<string,Instance>,collapsed:Set<string>){
 let parent=node.parentId;
 while(parent){if(collapsed.has(parent))return true;parent=map.get(parent)?.parentId||null}
 return false;
}

function layout(graph:Graph,collapsedIds:string[],positions:Record<string,CanvasPosition>,onSelect:(id:string)=>void,onOpenCanvas:(id:string)=>void,onToggleCollapse:(id:string)=>void,labels:{collapse:string;expand:string}):ConversationFlowNode[]{
 const collapsed=new Set(collapsedIds),map=new Map(graph.nodes.map(node=>[node.id,node]));
 const visible=graph.nodes.filter(node=>!hiddenByCollapsed(node,map,collapsed));
 const depths=new Map<string,number>();
 const depth=(node:Instance):number=>{if(depths.has(node.id))return depths.get(node.id)!;const value=node.parentId&&map.has(node.parentId)?depth(map.get(node.parentId)!)+1:0;depths.set(node.id,value);return value};
 const rows=new Map<number,number>();
 return visible.map(node=>{const column=depth(node),row=rows.get(column)||0;rows.set(column,row+1);return{id:node.id,type:'conversation',position:positions[node.id]||{x:40+column*310,y:40+row*130},data:{instance:node,active:node.id===graph.activeInstanceId,collapsed:collapsed.has(node.id),hasChildren:graph.nodes.some(candidate=>candidate.parentId===node.id),collapseLabel:labels.collapse,expandLabel:labels.expand,onSelect,onOpenCanvas,onToggleCollapse}}});
}

export function nodeSubtitle(instance:Instance){return instance.summary?.trim()||''}
export function shouldShowMiniMap(nodeCount:number){return nodeCount>=8}
const SINGLE_CLICK_DELAY_MS=240;
export function useClickArbitration(onSingle:()=>void,onDouble:()=>void){const timer=useRef<ReturnType<typeof setTimeout>|null>(null),handledDouble=useRef(false);const cancel=()=>{if(timer.current!==null){clearTimeout(timer.current);timer.current=null}};useEffect(()=>cancel,[]);return{onClick:(event:MouseEvent)=>{event.stopPropagation();cancel();if(event.detail>=2){handledDouble.current=true;onDouble();return}handledDouble.current=false;timer.current=setTimeout(()=>{timer.current=null;onSingle()},SINGLE_CLICK_DELAY_MS)},onDoubleClick:(event:MouseEvent)=>{event.preventDefault();event.stopPropagation();cancel();if(handledDouble.current){handledDouble.current=false;return}onDouble()}}}

export function ConversationCard({data,selected=false}:{data:ConversationData;selected?:boolean}){
 const node=data.instance,subtitle=nodeSubtitle(node),events=useClickArbitration(()=>data.onSelect(node.id),()=>data.onOpenCanvas(node.id));
 return <div className={`flow-node ${data.active?'is-active':''} ${node.status==='pruned'?'is-pruned':''} ${selected?'is-selected':''}`} data-instance-id={node.id} {...events}>
  {data.hasChildren&&<button type="button" className="node-collapse" aria-label={`${data.collapsed?data.expandLabel:data.collapseLabel}: ${node.title}`} onClick={event=>{event.preventDefault();event.stopPropagation();data.onToggleCollapse(node.id)}}>{data.collapsed?'＋':'−'}</button>}
  <strong>{node.title}</strong>{subtitle&&<span>{subtitle}</span>}
 </div>;
}

function ConversationNode({data,selected}:NodeProps<ConversationFlowNode>){return <><Handle type="target" position={Position.Left}/><ConversationCard data={data} selected={selected}/><Handle type="source" position={Position.Right}/></>}
const nodeTypes={conversation:ConversationNode};
export function reactFlowNodePointerProps(onOpenCanvas:(id:string)=>void){return{onNodeClick:()=>{},onNodeDoubleClick:(_event:MouseEvent,node:Node)=>onOpenCanvas(node.id)}}

export interface WorkflowGraphProps{
 graph:Graph
 selectedId:string
 collapsedNodeIds?:string[]
 nodePositions?:Record<string,CanvasPosition>
 initialViewport?:Viewport
 onSelect:(id:string)=>void
 onOpenCanvas:(id:string)=>void
 onToggleCollapse?:(id:string)=>void
 onViewportChange?:(viewport:Viewport)=>void
 onNodePositionChange?:(id:string,position:CanvasPosition)=>void
 focusRequest?:{id:string;revision:number}|null
 labels?:{locate:string;fit:string;collapse:string;expand:string}
}

export function WorkflowGraph({graph,selectedId,collapsedNodeIds=[],nodePositions={},initialViewport,onSelect,onOpenCanvas,onToggleCollapse=()=>{},onViewportChange,onNodePositionChange,focusRequest,labels={locate:'Locate selection',fit:'Fit view',collapse:'Collapse branch',expand:'Expand branch'}}:WorkflowGraphProps){
 const[instance,setInstance]=useState<ReactFlowInstance<ConversationFlowNode>|null>(null);
 const appliedFocus=useRef('');
 const calculated=useMemo(()=>layout(graph,collapsedNodeIds,nodePositions,onSelect,onOpenCanvas,onToggleCollapse,labels).map(node=>({...node,selected:node.id===selectedId})),[graph,selectedId,collapsedNodeIds,nodePositions,onSelect,onOpenCanvas,onToggleCollapse,labels]);
 const[nodes,setNodes,onNodesChange]=useNodesState<ConversationFlowNode>(calculated);
 useEffect(()=>setNodes(calculated),[calculated,setNodes]);
 const visibleIds=useMemo(()=>new Set(nodes.map(node=>node.id)),[nodes]);
 const edges=useMemo(()=>graphEdges(graph).filter(edge=>visibleIds.has(edge.source)&&visibleIds.has(edge.target)).map(edge=>({...edge,type:'smoothstep',markerEnd:{type:MarkerType.ArrowClosed}})),[graph,visibleIds]);
 const wrapperEvents=useMemo(()=>reactFlowNodePointerProps(onOpenCanvas),[onOpenCanvas]);
 const locate=()=>{const selected=nodes.filter(node=>node.id===selectedId);void instance?.fitView({nodes:selected.length?selected:nodes,duration:220,padding:.65})};
 const fit=()=>void instance?.fitView({nodes,duration:220,padding:.2});
 useEffect(()=>{
  if(!instance||!focusRequest)return;
  const key=`${focusRequest.id}:${focusRequest.revision}`;
  if(appliedFocus.current===key)return;
  const target=nodes.filter(node=>node.id===focusRequest.id);
  if(!target.length)return;
  appliedFocus.current=key;
  void instance.fitView({nodes:target,duration:220,padding:.7});
 },[focusRequest,instance,nodes]);
 return <ReactFlow<ConversationFlowNode> nodes={nodes} edges={edges} nodeTypes={nodeTypes} fitView={!initialViewport} defaultViewport={initialViewport} nodesDraggable nodesConnectable={false} elementsSelectable={false} onlyRenderVisibleElements onNodesChange={onNodesChange} onNodeDragStop={(_event,node)=>onNodePositionChange?.(node.id,node.position)} onInit={setInstance} onMoveEnd={(_event,viewport)=>onViewportChange?.(viewport)} {...wrapperEvents}>
  <Background gap={16} size={1}/>{shouldShowMiniMap(nodes.length)&&<MiniMap/>}<Controls/>
  <Panel position="top-right" className="canvas-tools"><button type="button" onClick={locate} disabled={!nodes.length} aria-label={labels.locate}>◎</button><button type="button" onClick={fit} disabled={!nodes.length} aria-label={labels.fit}>↔</button></Panel>
 </ReactFlow>;
}

export function pathTitles(graph:Graph,id:string){return memoryPath(graph,id).map(node=>node.title)}
