import{describe,expect,it,vi}from'vitest';
import{loadCanvasState,updateTurnCanvasState,updateWorkflowCanvasState}from'./canvasState';

function storage(seed:string|null=null){let value=seed;return{getItem:vi.fn(()=>value),setItem:vi.fn((_key:string,next:string)=>{value=next}),value:()=>value}}

describe('workspace canvas state',()=>{
 it('persists independent workflow and turn camera, collapse and position state',()=>{
  const store=storage();
  updateWorkflowCanvasState('wf',{selectedId:'b',viewport:{x:12,y:30,zoom:1.4},collapsedNodeIds:['a'],positions:{a:{x:40,y:50},b:{x:400,y:70}}},store);
  updateTurnCanvasState('wf','b',{selectedTurnId:'turn-2',viewport:{x:-8,y:9,zoom:.8},collapsedTurnIds:['turn-1'],positions:{'turn-2':{x:320,y:90}}},store);
  expect(loadCanvasState('wf',store)).toEqual({
   workflow:{selectedId:'b',viewport:{x:12,y:30,zoom:1.4},collapsedNodeIds:['a'],positions:{a:{x:40,y:50},b:{x:400,y:70}}},
   turns:{b:{selectedTurnId:'turn-2',viewport:{x:-8,y:9,zoom:.8},collapsedTurnIds:['turn-1'],positions:{'turn-2':{x:320,y:90}}}},
  });
 });

 it('drops corrupt camera and position values instead of breaking the workspace',()=>{
  const store=storage(JSON.stringify({workflow:{selectedId:'a',viewport:{x:0,y:0,zoom:99},positions:{a:{x:'bad',y:1}}},turns:{a:{positions:{t:{x:1,y:2}}}}}));
  expect(loadCanvasState('wf',store)).toEqual({workflow:{selectedId:'a',collapsedNodeIds:[],positions:{}},turns:{a:{collapsedTurnIds:[],positions:{t:{x:1,y:2}}}}});
 });
});
