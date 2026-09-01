export interface CanvasViewport {x:number;y:number;zoom:number}
export interface CanvasPosition {x:number;y:number}

export interface WorkflowCanvasState {
 selectedId?:string
 viewport?:CanvasViewport
 collapsedNodeIds:string[]
 positions:Record<string,CanvasPosition>
}

export interface TurnCanvasState {
 selectedTurnId?:string
 viewport?:CanvasViewport
 collapsedTurnIds:string[]
 positions:Record<string,CanvasPosition>
}

export interface PersistedCanvasState {
 workflow:WorkflowCanvasState
 turns:Record<string,TurnCanvasState>
}

const PREFIX='weavepath.canvas.v1:';
const DEFAULT_WORKFLOW:WorkflowCanvasState={collapsedNodeIds:[],positions:{}};

function viewport(value:unknown):CanvasViewport|undefined{
 if(!value||typeof value!=='object')return undefined;
 const item=value as Record<string,unknown>,x=Number(item.x),y=Number(item.y),zoom=Number(item.zoom);
 if(!Number.isFinite(x)||!Number.isFinite(y)||!Number.isFinite(zoom)||zoom<.25||zoom>1.5)return undefined;
 return{x,y,zoom};
}

function ids(value:unknown){return Array.isArray(value)?[...new Set(value.filter((item):item is string=>typeof item==='string'&&item.length>0))]:[]}

function positions(value:unknown):Record<string,CanvasPosition>{
 if(!value||typeof value!=='object')return{};
 return Object.fromEntries(Object.entries(value as Record<string,unknown>).flatMap(([id,item])=>{
  if(!id||!item||typeof item!=='object')return[];
  const point=item as Record<string,unknown>,x=Number(point.x),y=Number(point.y);
  return Number.isFinite(x)&&Number.isFinite(y)?[[id,{x,y}]]:[];
 }));
}

function turnState(value:unknown):TurnCanvasState{
 const item=value&&typeof value==='object'?value as Record<string,unknown>:{};
 return{
  ...(typeof item.selectedTurnId==='string'?{selectedTurnId:item.selectedTurnId}:{}),
  ...(viewport(item.viewport)?{viewport:viewport(item.viewport)}:{}),
  collapsedTurnIds:ids(item.collapsedTurnIds),
  positions:positions(item.positions),
 };
}

export function loadCanvasState(workflowId:string,storage:Pick<Storage,'getItem'>=localStorage):PersistedCanvasState{
 if(!workflowId)return{workflow:{...DEFAULT_WORKFLOW},turns:{}};
 try{
  const raw=storage.getItem(PREFIX+workflowId),value=raw?JSON.parse(raw) as Record<string,unknown>:{};
  const workflow=value.workflow&&typeof value.workflow==='object'?value.workflow as Record<string,unknown>:{};
  const rawTurns=value.turns&&typeof value.turns==='object'?value.turns as Record<string,unknown>:{};
  return{
   workflow:{
    ...(typeof workflow.selectedId==='string'?{selectedId:workflow.selectedId}:{}),
    ...(viewport(workflow.viewport)?{viewport:viewport(workflow.viewport)}:{}),
    collapsedNodeIds:ids(workflow.collapsedNodeIds),
    positions:positions(workflow.positions),
   },
   turns:Object.fromEntries(Object.entries(rawTurns).map(([key,item])=>[key,turnState(item)])),
  };
 }catch{return{workflow:{...DEFAULT_WORKFLOW},turns:{}}}
}

export function saveCanvasState(workflowId:string,state:PersistedCanvasState,storage:Pick<Storage,'setItem'>=localStorage){
 if(workflowId)storage.setItem(PREFIX+workflowId,JSON.stringify(state));
}

export function updateWorkflowCanvasState(workflowId:string,patch:Partial<WorkflowCanvasState>,storage:Pick<Storage,'getItem'|'setItem'>=localStorage){
 const state=loadCanvasState(workflowId,storage);
 const next={...state,workflow:{...state.workflow,...patch}};
 saveCanvasState(workflowId,next,storage);
 return next;
}

export function updateTurnCanvasState(workflowId:string,instanceId:string,patch:Partial<TurnCanvasState>,storage:Pick<Storage,'getItem'|'setItem'>=localStorage){
 const state=loadCanvasState(workflowId,storage),current=state.turns[instanceId]||{collapsedTurnIds:[],positions:{}};
 const next={...state,turns:{...state.turns,[instanceId]:{...current,...patch}}};
 saveCanvasState(workflowId,next,storage);
 return next;
}
