import{act,cleanup,render,waitFor}from'@testing-library/react';
import{afterEach,describe,expect,it,vi}from'vitest';
import type{Graph}from'../domain/types';
import{WorkflowGraph}from'./WorkflowGraph';

const flowMock=vi.hoisted(()=>({fitView:vi.fn()}));

vi.mock('@xyflow/react',async()=>{
 const React=await vi.importActual<typeof import('react')>('react');
 return{
  Background:()=>null,Controls:()=>null,Handle:()=>null,MiniMap:()=>null,
  Panel:(props:any)=>React.createElement(React.Fragment,null,props.children),
  ReactFlow:(props:any)=>{React.useEffect(()=>props.onInit?.({fitView:flowMock.fitView}),[props.onInit]);return React.createElement('div',{'data-testid':'react-flow'},props.children)},
  useNodesState:(initial:any[])=>{const[nodes,setNodes]=React.useState(initial);return[nodes,setNodes,vi.fn()]},
  MarkerType:{ArrowClosed:'arrow-closed'},Position:{Left:'left',Right:'right'},
 };
});

const root={id:'root',parentId:null,topicId:'topic-root',title:'数据集',status:'active' as const};
const child={id:'child',parentId:'root',topicId:'topic-child',title:'模块 B',status:'active' as const};
const baseGraph:Graph={workflowId:'wf',name:'研究工作流',rootInstanceId:'root',activeInstanceId:'root',graphRevision:1,eventRevision:1,nodes:[root]};
const graphWithChild:Graph={...baseGraph,graphRevision:2,nodes:[root,child]};
const handlers={collapsedNodeIds:[],nodePositions:{},onSelect:vi.fn(),onOpenCanvas:vi.fn(),onToggleCollapse:vi.fn(),onViewportChange:vi.fn(),onNodePositionChange:vi.fn(),labels:{locate:'定位',fit:'适应',collapse:'折叠',expand:'展开'}};

afterEach(()=>{cleanup();vi.clearAllMocks()});

describe('workflow focus request',()=>{
 it('waits for the new child, focuses it once, and does not steal focus again on later graph renders',async()=>{
  const focusRequest={id:'child',revision:2};
  const{rerender}=render(<WorkflowGraph graph={baseGraph} selectedId="root" focusRequest={focusRequest}{...handlers}/>);
  expect(flowMock.fitView).not.toHaveBeenCalled();
  rerender(<WorkflowGraph graph={graphWithChild} selectedId="child" focusRequest={focusRequest}{...handlers}/>);
  await waitFor(()=>expect(flowMock.fitView).toHaveBeenCalledTimes(1));
  expect(flowMock.fitView).toHaveBeenCalledWith({nodes:[expect.objectContaining({id:'child'})],duration:220,padding:.7});
  await act(async()=>{rerender(<WorkflowGraph graph={{...graphWithChild,eventRevision:3}} selectedId="root" focusRequest={focusRequest}{...handlers}/>);await Promise.resolve()});
  expect(flowMock.fitView).toHaveBeenCalledTimes(1);
 });
});
