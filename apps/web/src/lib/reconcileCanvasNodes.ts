import type {Node} from '@xyflow/react';

// Selecting one card must not invalidate every other card's memoized data.
export function reconcileCanvasNodes<T extends Node>(previous:T[],next:T[]):T[]{
 const byId=new Map(previous.map(node=>[node.id,node]));
 let unchanged=previous.length===next.length;
 const result=next.map((node,index)=>{
  const old=byId.get(node.id);
  if(old&&old.selected===node.selected&&old.position.x===node.position.x&&old.position.y===node.position.y&&
   Object.keys(old.data).length===Object.keys(node.data).length&&Object.keys(node.data).every(key=>old.data[key]===node.data[key])){
   if(previous[index]!==old)unchanged=false;
   return old;
  }
  unchanged=false;return node;
 });
 return unchanged?previous:result;
}
