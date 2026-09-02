import{cleanup,fireEvent,render,screen,waitFor,within}from'@testing-library/react';
import{afterEach,beforeEach,describe,expect,it,vi}from'vitest';
import{I18nProvider}from'../lib/i18n';
import{ApiError}from'../lib/api';
import{WorkspaceCanvas}from'./WorkspaceCanvas';

const apiMock=vi.hoisted(()=>({
 graph:vi.fn(),turns:vi.fn(),messages:vi.fn(),routes:vi.fn(),activate:vi.fn(),fork:vi.fn(),forkChat:vi.fn(),renameInstance:vi.fn(),prunePlan:vi.fn(),pruneCommit:vi.fn(),aiStatus:vi.fn(),chat:vi.fn(),send:vi.fn(),
}));

vi.mock('../lib/api',()=>({
 api:apiMock,
 ApiError:class ApiError extends Error{constructor(message:string,public status:number,public code?:string){super(message)}},
}));

vi.mock('../components/WorkflowGraph',()=>({
 useClickArbitration:(onSelect:()=>void,onOpen:()=>void)=>({onClick:onSelect,onDoubleClick:onOpen}),
 WorkflowGraph:(props:any)=><div data-testid="workflow-graph" data-selected={props.selectedId} data-viewport={JSON.stringify(props.initialViewport??null)} data-focus-id={props.focusRequest?.id||''} data-focus-revision={props.focusRequest?.revision??''}>
 <button type="button" onDoubleClick={()=>props.onOpenCanvas('leaf')}>open-leaf-canvas</button>
 <button type="button" onClick={()=>props.onSelect('leaf')}>select-leaf</button>
  <button type="button" onClick={()=>props.onBranch('leaf')}>quick-branch-leaf</button>
  <button type="button" onClick={()=>props.onToggleCollapse('root')}>collapse-root</button>
 </div>,
}));

vi.mock('../components/TurnCanvas',()=>({
 TurnCanvas:(props:any)=><div data-testid="turn-canvas" data-selected={props.selectedTurnId}>
  {props.snapshot.turns.map((turn:any)=><span key={turn.id}><button type="button" onClick={()=>props.onSelect(turn.id)}>{turn.userMessage.content}</button><button type="button" aria-label={`card-branch-${turn.id}`} onClick={()=>props.onBranch(turn)}>↗</button></span>)}
 </div>,
}));

const graph={
 workflowId:'wf',name:'研究工作流',rootInstanceId:'root',activeInstanceId:'root',graphRevision:3,eventRevision:11,
 nodes:[
  {id:'root',parentId:null,topicId:'topic-root',title:'数据集',status:'active' as const,contentRevision:2},
  {id:'child',parentId:'root',topicId:'topic-child',title:'情感分析',status:'active' as const,contentRevision:5},
  {id:'leaf',parentId:'child',topicId:'topic-leaf',title:'大模型实验',status:'active' as const,contentRevision:4},
 ],
};
const snapshot={
 workflowId:'wf',instanceId:'leaf',ownerInstanceId:'leaf',activeRouteInstanceId:'leaf',scope:'local' as const,contentRevision:9,eventRevision:11,
 memoryRoute:[{instanceId:'root',title:'数据集'},{instanceId:'child',title:'情感分析'},{instanceId:'leaf',title:'大模型实验'}],
 routeContentRevisions:{leaf:9},routeMemoryRoutes:{leaf:[{instanceId:'root',title:'数据集'},{instanceId:'child',title:'情感分析'},{instanceId:'leaf',title:'大模型实验'}]},routeInheritedMessageCounts:{leaf:6},routeTitles:{leaf:'大模型实验'},
 inheritedMessageCount:6,checkpointAnchor:{kind:'localUserTurn'},preamble:[],eventExtensions:[],
 turns:[{id:'turn-77',sequence:1,anchorMessageId:77,routeInstanceId:'leaf',routeTitle:'大模型实验',parentTurnId:null,userMessage:{id:707,role:'user' as const,content:'当前节点问题'},responses:[{id:708,role:'assistant' as const,content:'当前节点回答'}],status:'completed' as const}],
 inheritedMessages:[{id:'secret',role:'user',content:'不应显示的父节点正文'}],
};
const forkedSnapshot={...snapshot,routeContentRevisions:{leaf:9,forked:2},routeMemoryRoutes:{...snapshot.routeMemoryRoutes,forked:[...snapshot.memoryRoute,{instanceId:'forked',title:'模块 B'}]},routeInheritedMessageCounts:{leaf:6,forked:8},routeTitles:{leaf:'大模型实验',forked:'模块 B'},turns:[...snapshot.turns,{id:'turn-88',sequence:1,anchorMessageId:88,routeInstanceId:'forked',routeTitle:'模块 B',parentTurnId:'turn-77',userMessage:{id:808,role:'user' as const,content:'测试模块 B'},responses:[{id:809,role:'assistant' as const,content:'模块 B 回答'}],status:'completed' as const}]};

class FakeBroadcastChannel{
 constructor(_name:string){}
 addEventListener=vi.fn();postMessage=vi.fn();close=vi.fn();
}

function renderCanvas(props:{onContinue?:()=>void}={}){return render(<I18nProvider><WorkspaceCanvas workflowId="wf" {...props}/></I18nProvider>)}
async function openLeafCanvas(){
 await waitFor(()=>expect(screen.getByTestId('workflow-graph')).toHaveAttribute('data-selected','leaf'));
 fireEvent.doubleClick(screen.getByRole('button',{name:'open-leaf-canvas'}));
 await waitFor(()=>expect(apiMock.turns).toHaveBeenCalledWith('wf','leaf'));
 await screen.findByRole('heading',{name:'轮次画布'});
}

beforeEach(()=>{
 localStorage.clear();localStorage.setItem('cw.locale','zh-CN');localStorage.setItem('weavepath.canvas.v2:wf',JSON.stringify({workflow:{selectedId:'leaf',viewport:{x:14,y:-22,zoom:.8},collapsedNodeIds:[],positions:{}},turns:{}}));
 vi.stubGlobal('BroadcastChannel',FakeBroadcastChannel);
 vi.stubGlobal('crypto',{randomUUID:vi.fn(()=> 'fork-idempotency-1')});
 apiMock.graph.mockResolvedValue(graph);apiMock.turns.mockResolvedValue(snapshot);apiMock.routes.mockResolvedValue([]);
 apiMock.activate.mockResolvedValue({activeInstanceId:'leaf'});apiMock.fork.mockResolvedValue({node:{id:'forked'},graphRevision:4});
 apiMock.forkChat.mockResolvedValue({node:{id:'forked'},graphRevision:4,replyStatus:'completed',assistantMessage:{id:809,role:'assistant',content:'模块 B 回答'}});apiMock.aiStatus.mockResolvedValue({configured:true,provider:'fake',model:'test'});apiMock.chat.mockResolvedValue({});apiMock.send.mockResolvedValue({});
 apiMock.renameInstance.mockResolvedValue({node:{id:'leaf',title:'大模型分析'},graphRevision:4,eventRevision:12});
 apiMock.prunePlan.mockResolvedValue(null);apiMock.pruneCommit.mockResolvedValue({prunedInstanceIds:[]});
});

afterEach(()=>{cleanup();vi.clearAllMocks();vi.unstubAllGlobals()});

describe('native double canvas workspace',()=>{
 it('uses the conversation sidebar to select and locate a workflow node, then opens it on double-click',async()=>{
  renderCanvas();
  const navigation=await screen.findByRole('navigation',{name:'对话'}),leaf=within(navigation).getByRole('button',{name:'大模型实验'}),child=within(navigation).getByRole('button',{name:'情感分析'});
  expect(leaf).toHaveAttribute('aria-current','page');
  fireEvent.click(child);
  await waitFor(()=>expect(screen.getByTestId('workflow-graph')).toHaveAttribute('data-selected','child'));
  expect(screen.getByTestId('workflow-graph')).toHaveAttribute('data-focus-id','child');
  expect(child).toHaveAttribute('aria-current','page');
  expect(apiMock.turns).not.toHaveBeenCalled();
  fireEvent.doubleClick(child);
  await waitFor(()=>expect(apiMock.turns).toHaveBeenCalledWith('wf','child'));
  expect(screen.getByTestId('workflow-layer')).toHaveAttribute('hidden');
 });

 it('switches directly between conversation turn canvases from the sidebar',async()=>{
  const rootSnapshot={...snapshot,instanceId:'root',ownerInstanceId:'root',activeRouteInstanceId:'root',memoryRoute:[{instanceId:'root',title:'数据集'}],routeContentRevisions:{root:2},routeMemoryRoutes:{root:[{instanceId:'root',title:'数据集'}]},routeInheritedMessageCounts:{root:0},routeTitles:{root:'数据集'},inheritedMessageCount:0,turns:[]};
  apiMock.turns.mockImplementation(async(_workflowId:string,instanceId:string)=>instanceId==='root'?rootSnapshot:snapshot);
  renderCanvas();await openLeafCanvas();
  const navigation=screen.getByRole('navigation',{name:'对话'}),root=within(navigation).getByRole('button',{name:'数据集'});
  fireEvent.click(root);
  await waitFor(()=>expect(apiMock.turns).toHaveBeenCalledWith('wf','root'));
  expect(root).toHaveAttribute('aria-current','page');
  expect(screen.getByTestId('workflow-layer')).toHaveAttribute('hidden');
  expect(screen.getByTestId('turn-layer')).not.toHaveAttribute('hidden');
 });

 it('opens Turn Canvas on double-click without activating, and activates only through Continue conversation',async()=>{
  const onContinue=vi.fn();renderCanvas({onContinue});await openLeafCanvas();
  expect(screen.getByTestId('workflow-layer')).toHaveAttribute('hidden');expect(screen.getByTestId('turn-layer')).not.toHaveAttribute('hidden');
  expect(apiMock.activate).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button',{name:'继续对话'}));
  await waitFor(()=>expect(apiMock.activate).toHaveBeenCalledOnce());
  expect(apiMock.activate).toHaveBeenCalledWith('wf','leaf');expect(onContinue).toHaveBeenCalledOnce();
 });

 it('keeps the workflow layer hidden and explains an outdated backend when Turn API is unavailable',async()=>{
  apiMock.turns.mockRejectedValueOnce(new ApiError('Not Found',404,'notFound'));renderCanvas();
  await waitFor(()=>expect(screen.getByTestId('workflow-graph')).toHaveAttribute('data-selected','leaf'));
  fireEvent.doubleClick(screen.getByRole('button',{name:'open-leaf-canvas'}));
  expect(await screen.findByRole('alert')).toHaveTextContent('后端版本过旧');
  expect(screen.getByTestId('workflow-layer')).toHaveAttribute('hidden');expect(screen.getByTestId('turn-layer')).not.toHaveAttribute('hidden');
 });

 it('restores workflow selection and viewport through the breadcrumb and selects a collapsed ancestor',async()=>{
  renderCanvas();await openLeafCanvas();
  fireEvent.click(screen.getByRole('button',{name:'研究工作流'}));
  const workflow=screen.getByTestId('workflow-graph');
  expect(screen.getByTestId('workflow-layer')).not.toHaveAttribute('hidden');expect(screen.getByTestId('turn-layer')).toHaveAttribute('hidden');
  expect(workflow).toHaveAttribute('data-selected','leaf');expect(workflow).toHaveAttribute('data-viewport',JSON.stringify({x:14,y:-22,zoom:.8}));
  fireEvent.click(screen.getByRole('button',{name:'collapse-root'}));
  await waitFor(()=>expect(workflow).toHaveAttribute('data-selected','root'));
 });

 it('shows only local turns plus route/checkpoint metadata, never inherited transcript text',async()=>{
  renderCanvas();await openLeafCanvas();
  expect(screen.getAllByText('当前节点问题')).toHaveLength(2);expect(screen.getByText('当前节点回答')).toBeInTheDocument();
  expect(screen.getByText('继承消息数: 6')).toBeInTheDocument();expect(screen.getAllByText('数据集')).toHaveLength(2);expect(screen.getAllByText('情感分析')).toHaveLength(2);
  expect(screen.queryByText('不应显示的父节点正文')).not.toBeInTheDocument();expect(apiMock.messages).not.toHaveBeenCalled();
 });

 it('continues the selected conversation directly from Turn Canvas and refreshes the shared transcript',async()=>{
  const updated={...snapshot,contentRevision:11,turns:[...snapshot.turns,{id:'turn-78',sequence:2,anchorMessageId:78,userMessage:{id:709,role:'user' as const,content:'直接从画布提问'},responses:[{id:710,role:'assistant' as const,content:'画布同步回答'}],status:'completed' as const}]};
  apiMock.turns.mockResolvedValueOnce(snapshot).mockResolvedValueOnce(updated);
  renderCanvas();await openLeafCanvas();
  fireEvent.change(screen.getByLabelText('画布对话输入'),{target:{value:'直接从画布提问'}});fireEvent.click(screen.getByRole('button',{name:'发送'}));
  await waitFor(()=>expect(apiMock.chat).toHaveBeenCalledWith('wf','leaf','直接从画布提问'));
  expect(await screen.findByText('直接从画布提问')).toBeInTheDocument();expect(apiMock.turns).toHaveBeenCalledTimes(2);
 });

 it('creates an empty branch directly from the card with the exact anchor and an automatic name',async()=>{
  const emptyForked={...forkedSnapshot,turns:snapshot.turns};
  apiMock.turns.mockResolvedValueOnce(snapshot).mockResolvedValueOnce(emptyForked);
 renderCanvas();await openLeafCanvas();
  fireEvent.click(await screen.findByRole('button',{name:'card-branch-turn-77'}));
  await waitFor(()=>expect(apiMock.forkChat).toHaveBeenCalledOnce());
  expect(apiMock.forkChat).toHaveBeenCalledWith('wf','leaf',{anchorMessageId:77,expectedContentRevision:9,idempotencyKey:'fork-idempotency-1'});
  await waitFor(()=>expect(apiMock.turns).toHaveBeenCalledTimes(2));
  expect(screen.getByTestId('workflow-graph')).not.toHaveAttribute('data-focus-id','forked');
  expect(screen.getByTestId('turn-canvas')).toHaveAttribute('data-selected','route:forked');
  expect(screen.getByRole('navigation',{name:'工作流画布'})).toHaveTextContent('大模型实验');expect(screen.getByRole('heading',{name:'轮次画布'})).toBeInTheDocument();
 });

 it('refreshes the Turn snapshot after a fork revision conflict',async()=>{
  apiMock.forkChat.mockRejectedValueOnce(new ApiError('stale',409,'conflict'));
  renderCanvas();await openLeafCanvas();fireEvent.click(await screen.findByRole('button',{name:'card-branch-turn-77'}));
  await waitFor(()=>expect(apiMock.turns).toHaveBeenCalledTimes(2));
  expect(screen.getByRole('alert')).toHaveTextContent('创建分支前对话内容已变化');
 });

 it('explains an outdated backend when exact turn fork is unavailable and unlocks retry',async()=>{
  apiMock.forkChat.mockRejectedValueOnce(new ApiError('HTTP 404',404,'notFound'));
  renderCanvas();await openLeafCanvas();fireEvent.click(await screen.findByRole('button',{name:'card-branch-turn-77'}));
  expect(await screen.findByRole('alert')).toHaveTextContent('后端版本过旧');
  expect(screen.getByRole('button',{name:'card-branch-turn-77'})).toBeEnabled();
 });

 it('refreshes the workflow graph after a head-fork revision conflict',async()=>{
  apiMock.fork.mockRejectedValueOnce(new ApiError('stale',409,'conflict'));
  renderCanvas();await waitFor(()=>expect(screen.getByTestId('workflow-graph')).toHaveAttribute('data-selected','leaf'));
  fireEvent.click(screen.getByRole('button',{name:'quick-branch-leaf'}));
  await waitFor(()=>expect(apiMock.graph).toHaveBeenCalledTimes(2));
  expect(apiMock.fork).toHaveBeenCalledWith('wf','leaf',{expectedContentRevision:4,idempotencyKey:'fork-idempotency-1'});
  expect(screen.getByRole('alert')).toHaveTextContent('最新画布数据已刷新');
 });

 it('renames the selected conversation from the inspector with revision protection',async()=>{
  renderCanvas();await waitFor(()=>expect(screen.getByTestId('workflow-graph')).toHaveAttribute('data-selected','leaf'));
  fireEvent.click(screen.getByRole('button',{name:'重命名'}));fireEvent.change(screen.getByLabelText('对话名称'),{target:{value:'大模型分析'}});fireEvent.click(screen.getByRole('button',{name:'保存'}));
  await waitFor(()=>expect(apiMock.renameInstance).toHaveBeenCalledWith('wf','leaf','大模型分析',3));
 });
});
