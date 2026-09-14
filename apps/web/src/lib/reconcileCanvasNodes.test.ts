import {expect,it} from 'vitest';
import {reconcileCanvasNodes} from './reconcileCanvasNodes';

it('preserves 298 unaffected nodes when selection moves across a 300 card canvas',()=>{
 const nodes=Array.from({length:300},(_,i)=>({id:String(i),position:{x:i*365,y:0},selected:i===0,data:{title:String(i)}}));
 const next=nodes.map((node,i)=>({...node,selected:i===1,data:{...node.data}}));
 const result=reconcileCanvasNodes(nodes,next);
 expect(result[0]).not.toBe(nodes[0]);expect(result[1]).not.toBe(nodes[1]);
 expect(result.slice(2).every((node,i)=>node===nodes[i+2])).toBe(true);
 expect(reconcileCanvasNodes(result,next)).toBe(result);
});
