import{cleanup,fireEvent,render,screen,waitFor,within}from'@testing-library/react';
import{afterEach,beforeEach,describe,expect,it,vi}from'vitest';
import{I18nProvider}from'../lib/i18n';
import{ChatPage}from'./ChatPage';
import{ApiError}from'../lib/api';
import{parseChatMessage}from'../lib/chatAttachments';

const apiMock=vi.hoisted(()=>({
 workflows:vi.fn(),graph:vi.fn(),messages:vi.fn(),messageSnapshot:vi.fn(),regenerate:vi.fn(),agentRuns:vi.fn(),createAgentRun:vi.fn(),agentRun:vi.fn(),agentRunEvents:vi.fn(),aiStatus:vi.fn(),aiSettings:vi.fn(),saveAISettings:vi.fn(),resetAISettings:vi.fn(),validateAISettings:vi.fn(),aiModels:vi.fn(),switchAIModel:vi.fn(),send:vi.fn(),chat:vi.fn(),
 chatStream:undefined as ReturnType<typeof vi.fn>|undefined,cancelChat:undefined as ReturnType<typeof vi.fn>|undefined,
 createWorkflow:vi.fn(),renameWorkflow:vi.fn(),fork:vi.fn(),activate:vi.fn(),prunePlan:vi.fn(),pruneCommit:vi.fn(),routes:vi.fn()
}));

vi.mock('../lib/api',()=>({
 api:apiMock,
 ApiError:class ApiError extends Error{constructor(message:string,public status:number,public code?:string,public runId?:string|number){super(message)}}
}));

const graph={
 workflowId:'wf-1',name:'研究项目',rootInstanceId:'root',activeInstanceId:'root',graphRevision:0,eventRevision:0,
 nodes:[{id:'root',parentId:null,topicId:'topic-root',title:'数据集构建',status:'active' as const}]
};

function renderChat(){return render(<I18nProvider><ChatPage/></I18nProvider>)}

beforeEach(()=>{
 localStorage.clear();localStorage.setItem('cw.locale','zh-CN');localStorage.setItem('cw.workflow','wf-1');
 apiMock.chatStream=undefined;apiMock.cancelChat=undefined;
 apiMock.workflows.mockResolvedValue([{id:'wf-1',name:'研究项目',activeInstanceId:'root'}]);
 apiMock.graph.mockResolvedValue(graph);apiMock.messages.mockResolvedValue([]);apiMock.send.mockResolvedValue({id:'u1',role:'user',content:'测试消息'});
 apiMock.messageSnapshot.mockImplementation(async(w:string,i:string,scope:string)=>({messages:await apiMock.messages(w,i,scope),contentRevision:1}));apiMock.regenerate.mockResolvedValue({messages:[],contentRevision:2});apiMock.agentRuns.mockResolvedValue([]);apiMock.agentRun.mockResolvedValue({});apiMock.agentRunEvents.mockResolvedValue({runId:'',events:[],nextAfterSequence:null});
 apiMock.aiSettings.mockResolvedValue({configured:false,provider:'openai-compatible',baseUrl:null,model:null,systemPrompt:'',hasApiKey:false,source:'none',persistence:'memory'});
 apiMock.aiModels.mockResolvedValue({models:[],count:0});apiMock.switchAIModel.mockResolvedValue({configured:true,provider:'openai-compatible',model:'test-model',reasoningEffort:null});
 apiMock.chat.mockResolvedValue({userMessage:{id:'u1',role:'user',content:'测试消息'},assistantMessage:{id:'a1',role:'assistant',content:'助手回复'}});
 apiMock.renameWorkflow.mockResolvedValue({workflowId:'wf-1',name:'新项目名称',graphRevision:1,eventRevision:1});
});

afterEach(()=>{cleanup();vi.clearAllMocks()});

describe('chat delivery mode',()=>{
 it('switches the configured model from the composer without opening settings',async()=>{apiMock.aiStatus.mockResolvedValue({configured:true,provider:'openai-compatible',model:'test-model',reasoningEffort:null});apiMock.aiModels.mockResolvedValue({models:['test-model','analysis-model'],count:2});apiMock.switchAIModel.mockResolvedValue({configured:true,provider:'openai-compatible',model:'analysis-model',reasoningEffort:null});renderChat();const trigger=await screen.findByRole('button',{name:'切换模型: test-model · 自动'});expect(trigger.closest('.composer')).not.toBeNull();fireEvent.click(trigger);fireEvent.click(await screen.findByRole('option',{name:'analysis-model'}));await waitFor(()=>expect(apiMock.switchAIModel).toHaveBeenCalledWith('analysis-model',null));expect(await screen.findByRole('button',{name:'切换模型: analysis-model · 自动'})).toBeInTheDocument();expect(screen.queryByRole('heading',{name:'模型设置'})).not.toBeInTheDocument()});
 it('changes reasoning effort beside the composer model without changing providers',async()=>{apiMock.aiStatus.mockResolvedValue({configured:true,provider:'openai-compatible',model:'test-model',reasoningEffort:null});apiMock.aiModels.mockResolvedValue({models:['test-model'],count:1});apiMock.switchAIModel.mockResolvedValue({configured:true,provider:'openai-compatible',model:'test-model',reasoningEffort:'high'});renderChat();fireEvent.click(await screen.findByRole('button',{name:'切换模型: test-model · 自动'}));fireEvent.click(screen.getByRole('button',{name:'高'}));await waitFor(()=>expect(apiMock.switchAIModel).toHaveBeenCalledWith('test-model','high'));expect(await screen.findByRole('button',{name:'切换模型: test-model · 高'})).toBeInTheDocument()});
 it('opens model settings from the compact sidebar button without translating conversation titles',async()=>{apiMock.aiStatus.mockResolvedValue({configured:false,provider:'openai-compatible',model:null});renderChat();expect(await screen.findByText('数据集构建')).toBeInTheDocument();expect(screen.queryByRole('button',{name:/继承的路线记忆/})).not.toBeInTheDocument();fireEvent.click(screen.getByRole('button',{name:/设置/}));expect(await screen.findByRole('heading',{name:'模型设置'})).toBeInTheDocument();expect(screen.getByText('数据集构建')).toBeInTheDocument()});
 it('pins workflows locally and keeps pinned conversations first without changing their names',async()=>{
  apiMock.aiStatus.mockResolvedValue({configured:false,provider:'openai-compatible',model:null});
  apiMock.workflows.mockResolvedValue([{id:'wf-1',name:'研究项目',activeInstanceId:'root'},{id:'wf-2',name:'第二项目',activeInstanceId:'other'}]);
  renderChat();const nav=(await screen.findByRole('button',{name:'研究项目'})).closest('nav')!;
  fireEvent.click(within(nav).getByRole('button',{name:'置顶: 第二项目'}));
  await waitFor(()=>expect(within(nav).getAllByRole('button').filter(button=>button.textContent==='研究项目'||button.textContent==='第二项目').map(button=>button.textContent)).toEqual(['第二项目','研究项目']));
  expect(localStorage.getItem('weavepath.pinned-workflows.v1')).toBe('["wf-2"]');
  const pin=within(nav).getByRole('button',{name:'取消置顶: 第二项目'});expect(pin).toHaveAttribute('aria-pressed','true');
 });
 it('renames a workflow from the sidebar with graph revision protection and refreshes the current graph',async()=>{
  apiMock.aiStatus.mockResolvedValue({configured:false,provider:'openai-compatible',model:null});
  apiMock.workflows.mockResolvedValueOnce([{id:'wf-1',name:'研究项目',activeInstanceId:'root'}]).mockResolvedValueOnce([{id:'wf-1',name:'新项目名称',activeInstanceId:'root'}]);
  apiMock.graph.mockResolvedValueOnce(graph).mockResolvedValueOnce({...graph,name:'新项目名称',graphRevision:1,eventRevision:1});
  renderChat();await screen.findByRole('button',{name:'研究项目'});
  fireEvent.click(screen.getByRole('button',{name:'重命名工作流: 研究项目'}));
  fireEvent.change(screen.getByLabelText('重命名工作流: 研究项目'),{target:{value:'新项目名称'}});
  fireEvent.click(screen.getByRole('button',{name:'保存'}));
  await waitFor(()=>expect(apiMock.renameWorkflow).toHaveBeenCalledWith('wf-1','新项目名称',0));
  expect(await screen.findByRole('button',{name:'新项目名称'})).toBeInTheDocument();
  await waitFor(()=>expect(apiMock.graph).toHaveBeenCalledTimes(2));
 });
 it('does not replace the selected graph when a sidebar rename finishes after switching workflows',async()=>{
  apiMock.aiStatus.mockResolvedValue({configured:false,provider:'openai-compatible',model:null});
  const graphTwo={...graph,workflowId:'wf-2',name:'第二项目',activeInstanceId:'other',nodes:[{id:'other',parentId:null,topicId:'topic-other',title:'第二项目节点',status:'active' as const}]};
  let renamed=false,finishRename!:(value:unknown)=>void;
  apiMock.workflows.mockImplementation(async()=>[
   {id:'wf-1',name:renamed?'新项目名称':'研究项目',activeInstanceId:'root'},
   {id:'wf-2',name:'第二项目',activeInstanceId:'other'},
  ]);
  apiMock.graph.mockImplementation(async(id:string)=>id==='wf-2'?graphTwo:graph);
  apiMock.renameWorkflow.mockReturnValue(new Promise(resolve=>{finishRename=resolve}));
  renderChat();await screen.findByRole('button',{name:'研究项目'});
  fireEvent.click(screen.getByRole('button',{name:'重命名工作流: 研究项目'}));
  fireEvent.change(screen.getByLabelText('重命名工作流: 研究项目'),{target:{value:'新项目名称'}});
  fireEvent.click(screen.getByRole('button',{name:'保存'}));
  fireEvent.click(screen.getByRole('button',{name:'第二项目'}));
  expect(await screen.findByText('第二项目节点')).toBeInTheDocument();
  renamed=true;finishRename({workflowId:'wf-1',name:'新项目名称',graphRevision:1,eventRevision:1});
  expect(await screen.findByRole('button',{name:'新项目名称'})).toBeInTheDocument();
  await waitFor(()=>expect(screen.getByText('第二项目节点')).toBeInTheDocument());
  expect(apiMock.graph.mock.calls.map(([id])=>id)).toEqual(['wf-1','wf-2']);
 });
 it('refreshes the workflow sidebar after a structural canvas change',async()=>{
  apiMock.aiStatus.mockResolvedValue({configured:false,provider:'openai-compatible',model:null});
  apiMock.workflows.mockResolvedValueOnce([{id:'wf-1',name:'研究项目',activeInstanceId:'root'}]).mockResolvedValueOnce([{id:'wf-1',name:'已重命名项目',activeInstanceId:'root'}]);
  apiMock.graph.mockResolvedValueOnce(graph).mockResolvedValueOnce({...graph,name:'已重命名项目'});
  renderChat();expect(await screen.findByRole('button',{name:'研究项目'})).toBeInTheDocument();
  window.dispatchEvent(new MessageEvent('message',{data:{type:'conversation-workflow-changed',workflowId:'wf-1',senderId:'canvas',sentAt:Date.now()}}));
  expect(await screen.findByRole('button',{name:'已重命名项目'})).toBeInTheDocument();
  expect(apiMock.workflows).toHaveBeenCalledTimes(2);expect(apiMock.graph).toHaveBeenCalledTimes(2);
 });
 it('does not let an older workflow-list refresh restore a stale canvas name',async()=>{
  apiMock.aiStatus.mockResolvedValue({configured:false,provider:'openai-compatible',model:null});
  let finishStale!:(value:unknown)=>void,finishLatest!:(value:unknown)=>void;
  apiMock.workflows
   .mockResolvedValueOnce([{id:'wf-1',name:'研究项目',activeInstanceId:'root'}])
   .mockReturnValueOnce(new Promise(resolve=>{finishStale=resolve}))
   .mockReturnValueOnce(new Promise(resolve=>{finishLatest=resolve}));
  renderChat();expect(await screen.findByRole('button',{name:'研究项目'})).toBeInTheDocument();
  const change={type:'conversation-workflow-changed',workflowId:'wf-1',senderId:'canvas',sentAt:Date.now()};
  window.dispatchEvent(new MessageEvent('message',{data:change}));window.dispatchEvent(new MessageEvent('message',{data:{...change,sentAt:change.sentAt+1}}));
  finishLatest([{id:'wf-1',name:'最终名称',activeInstanceId:'root'}]);
  expect(await screen.findByRole('button',{name:'最终名称'})).toBeInTheDocument();
  finishStale([{id:'wf-1',name:'过期名称',activeInstanceId:'root'}]);
  await waitFor(()=>expect(screen.queryByRole('button',{name:'过期名称'})).not.toBeInTheDocument());
  expect(screen.getByRole('button',{name:'最终名称'})).toBeInTheDocument();
 });
 it('refreshes a renamed background workflow without replacing the current graph',async()=>{
  apiMock.aiStatus.mockResolvedValue({configured:false,provider:'openai-compatible',model:null});
  apiMock.workflows
   .mockResolvedValueOnce([{id:'wf-1',name:'研究项目',activeInstanceId:'root'},{id:'wf-2',name:'旧名称',activeInstanceId:'other'}])
   .mockResolvedValueOnce([{id:'wf-1',name:'研究项目',activeInstanceId:'root'},{id:'wf-2',name:'后台新名称',activeInstanceId:'other'}]);
  renderChat();expect(await screen.findByRole('button',{name:'旧名称'})).toBeInTheDocument();
  window.dispatchEvent(new MessageEvent('message',{data:{type:'conversation-workflow-changed',workflowId:'wf-2',senderId:'canvas',sentAt:Date.now()}}));
  expect(await screen.findByRole('button',{name:'后台新名称'})).toBeInTheDocument();
  expect(apiMock.graph).toHaveBeenCalledTimes(1);
 });
 it('saves through the message endpoint when AI is not configured',async()=>{
  apiMock.aiStatus.mockResolvedValue({configured:false,provider:'openai-compatible',model:null});
  apiMock.messages.mockResolvedValueOnce([]).mockResolvedValueOnce([{id:'u1',role:'user',content:'测试消息'}]);
  renderChat();
  expect(await screen.findByText('仅记录模式 · 尚未连接 AI')).toBeInTheDocument();
  fireEvent.change(screen.getByRole('textbox'),{target:{value:'测试消息'}});fireEvent.click(screen.getByRole('button',{name:'发送'}));
  await waitFor(()=>expect(apiMock.send).toHaveBeenCalledWith('wf-1','root','测试消息'));
  expect(apiMock.chat).not.toHaveBeenCalled();expect(await screen.findByText('测试消息')).toBeInTheDocument();
 });
 it('attaches a text file from the plus button and binds its contents to the user message',async()=>{
  apiMock.aiStatus.mockResolvedValue({configured:false,provider:'openai-compatible',model:null});
  // Regression: attachments larger than the old 100 KB placeholder limit remain usable.
  const file={name:'notes.md',type:'text/markdown',size:150_000,text:vi.fn().mockResolvedValue('file context')}as unknown as File;
  renderChat();await screen.findByText('仅记录模式 · 尚未连接 AI');
  const input=document.querySelector<HTMLInputElement>('.composer-file-input')!;
  fireEvent.change(input,{target:{files:[file]}});
  expect(await screen.findByText('notes.md')).toBeInTheDocument();
  fireEvent.change(screen.getByRole('textbox'),{target:{value:'请总结附件'}});fireEvent.click(screen.getByRole('button',{name:'发送'}));
  await waitFor(()=>expect(apiMock.send).toHaveBeenCalledTimes(1));
  const stored=String(apiMock.send.mock.calls[0][2]),parsed=parseChatMessage(stored);
  expect(parsed.prompt).toBe('请总结附件');expect(parsed.attachments).toMatchObject([{name:'notes.md',content:'file context'}]);
  expect(screen.queryByText(/WeavePath attachments v1/)).not.toBeInTheDocument();
 });

 it('uses the chat endpoint and renders the assistant reply when AI is configured',async()=>{
  apiMock.aiStatus.mockResolvedValue({configured:true,provider:'openai-compatible',model:'test-model'});
  apiMock.messages.mockResolvedValueOnce([]).mockResolvedValueOnce([
   {id:'u1',role:'user',content:'测试消息'},{id:'a1',role:'assistant',content:'助手回复'}
  ]);
  renderChat();
  expect(await screen.findByText('AI 已配置 · test-model')).toBeInTheDocument();
  fireEvent.change(screen.getByRole('textbox'),{target:{value:'测试消息'}});fireEvent.click(screen.getByRole('button',{name:'发送'}));
  await waitFor(()=>expect(apiMock.chat).toHaveBeenCalledWith('wf-1','root','测试消息',expect.any(String)));
  expect(apiMock.send).not.toHaveBeenCalled();expect(await screen.findByText('助手回复')).toBeInTheDocument();
 });
 it('shows elapsed time below an ordinary reply and expands real cache accounting',async()=>{
  apiMock.aiStatus.mockResolvedValue({configured:true,provider:'openai-compatible',model:'test-model'});
  apiMock.messageSnapshot.mockResolvedValue({messages:[{id:'u1',role:'user',content:'测试消息'},{id:'a1',role:'assistant',content:'助手回复',responseDetails:{durationMs:81320,provider:'openai-compatible',model:'test-model',cachedInputTokens:750,uncachedInputTokens:250,cacheReuseRatio:.75,cacheCoverage:1,cacheStatus:'reported'}}],contentRevision:2});
  renderChat();
  expect(await screen.findByText('助手回复')).toBeInTheDocument();
  const summary=screen.getByLabelText('回复详情'),details=summary.closest('details')!;
  expect(within(details).getByText('用时 1 分钟 21 秒')).toBeInTheDocument();
  fireEvent.click(summary);
  expect(within(details).getByText('750')).toBeInTheDocument();
  expect(within(details).getByText('250')).toBeInTheDocument();
  expect(within(details).getByText('75%')).toBeInTheDocument();
  expect(within(details).getByText('100%')).toBeInTheDocument();
 });
 it('labels cache fields unavailable when an ordinary reply provider omitted them',async()=>{
  apiMock.aiStatus.mockResolvedValue({configured:true,provider:'openai-compatible',model:'test-model'});
  apiMock.messageSnapshot.mockResolvedValue({messages:[{id:'a1',role:'assistant',content:'无缓存统计',responseDetails:{durationMs:400,cacheStatus:'unsupported'}}],contentRevision:1});
  renderChat();await screen.findByText('无缓存统计');fireEvent.click(screen.getByText('用时不足 1 秒'));
  expect(screen.getAllByText('不可用')).toHaveLength(4);
 });
 it('refreshes graph metadata after the first message reveals an automatically generated branch title',async()=>{
  apiMock.aiStatus.mockResolvedValue({configured:false,provider:'openai-compatible',model:null});
  const untitled={...graph,nodes:[{...graph.nodes[0],title:'新分支 1'}]};
  const titled={...graph,graphRevision:1,eventRevision:1,nodes:[{...graph.nodes[0],title:'情感分析数据集'}]};
  apiMock.graph.mockResolvedValueOnce(untitled).mockResolvedValueOnce(titled);
  apiMock.messageSnapshot.mockResolvedValueOnce({messages:[],contentRevision:0}).mockResolvedValueOnce({messages:[{id:'u1',role:'user',content:'研究情感分析数据集'}],contentRevision:1});
  renderChat();
  expect(await screen.findByRole('heading',{name:'新分支 1'})).toBeInTheDocument();
  fireEvent.change(screen.getByRole('textbox'),{target:{value:'研究情感分析数据集'}});fireEvent.click(screen.getByRole('button',{name:'发送'}));
  await waitFor(()=>expect(apiMock.graph).toHaveBeenCalledTimes(2));
  expect(await screen.findByRole('heading',{name:'情感分析数据集'})).toBeInTheDocument();
 });
 it('does not let a late post-send graph refresh overwrite a newer graph request',async()=>{
  apiMock.aiStatus.mockResolvedValue({configured:false,provider:'openai-compatible',model:null});
  const branch={...graph,activeInstanceId:'branch',nodes:[...graph.nodes,{id:'branch',parentId:'root',topicId:'t2',title:'模块B',status:'active' as const}]};
  let staleGraphDone!:(value:unknown)=>void;
  const staleGraph=new Promise(resolve=>{staleGraphDone=resolve});
  apiMock.graph.mockResolvedValueOnce(graph).mockReturnValueOnce(staleGraph).mockResolvedValueOnce(branch);
  apiMock.messageSnapshot.mockResolvedValue({messages:[{id:'u1',role:'user',content:'触发自动命名'}],contentRevision:1});
  renderChat();expect(await screen.findByRole('heading',{name:'数据集构建'})).toBeInTheDocument();
  fireEvent.change(screen.getByRole('textbox'),{target:{value:'触发自动命名'}});fireEvent.click(screen.getByRole('button',{name:'发送'}));
  await waitFor(()=>expect(apiMock.graph).toHaveBeenCalledTimes(2));
  window.dispatchEvent(new MessageEvent('message',{data:{type:'conversation-workflow-changed'}}));
  expect(await screen.findByRole('heading',{name:'模块B'})).toBeInTheDocument();
  staleGraphDone({...graph,nodes:[{...graph.nodes[0],title:'过期自动标题'}]});
  await waitFor(()=>expect(screen.queryByRole('heading',{name:'过期自动标题'})).not.toBeInTheDocument());
  expect(screen.getByRole('heading',{name:'模块B'})).toBeInTheDocument();
 });
 it('keeps the top-level conversation title while reading and writing the selected internal route',async()=>{
  apiMock.aiStatus.mockResolvedValue({configured:false,provider:'openai-compatible',model:null});
  apiMock.graph.mockResolvedValue({...graph,activeRouteInstanceId:'turn-route-b',activeRouteTitle:'LLM数据集',activeRouteContentRevision:4});
  apiMock.messageSnapshot.mockResolvedValueOnce({messages:[{id:'b1',role:'user',content:'内部路线消息'}],contentRevision:4}).mockResolvedValueOnce({messages:[{id:'b1',role:'user',content:'内部路线消息'},{id:'b2',role:'user',content:'继续内部路线'}],contentRevision:5});
  renderChat();
  expect(await screen.findByText('内部路线消息')).toBeInTheDocument();expect(screen.getByRole('heading',{name:'数据集构建'})).toBeInTheDocument();
  expect(apiMock.messageSnapshot).toHaveBeenCalledWith('wf-1','turn-route-b','local');
  fireEvent.change(screen.getByRole('textbox'),{target:{value:'继续内部路线'}});fireEvent.click(screen.getByRole('button',{name:'发送'}));
  await waitFor(()=>expect(apiMock.send).toHaveBeenCalledWith('wf-1','turn-route-b','继续内部路线'));
  expect(await screen.findByText('继续内部路线')).toBeInTheDocument();
 });
 it('follows an exact internal route selected on the canvas while keeping its owner conversation title',async()=>{
  apiMock.aiStatus.mockResolvedValue({configured:false,provider:'openai-compatible',model:null});
  const internalRouteGraph={...graph,activeRouteInstanceId:'turn-route-b',activeRouteTitle:'LLM数据集',activeRouteContentRevision:4};
  apiMock.graph.mockResolvedValueOnce(graph).mockResolvedValueOnce(internalRouteGraph);
  apiMock.messageSnapshot.mockImplementation(async(_workflow:string,instance:string)=>instance==='turn-route-b'
   ?{messages:[{id:'b1',role:'user',content:'内部路线消息'}],contentRevision:4}
   :{messages:[{id:'r1',role:'user',content:'顶层路线消息'}],contentRevision:2});
  renderChat();
  expect(await screen.findByText('顶层路线消息')).toBeInTheDocument();
  window.dispatchEvent(new MessageEvent('message',{data:{type:'conversation-workflow-changed',workflowId:'wf-1',instanceId:'turn-route-b',senderId:'canvas',sentAt:Date.now()}}));
  expect(await screen.findByText('内部路线消息')).toBeInTheDocument();
  expect(screen.queryByText('顶层路线消息')).not.toBeInTheDocument();
  expect(screen.getByRole('heading',{name:'数据集构建'})).toBeInTheDocument();
  expect(apiMock.messageSnapshot).toHaveBeenLastCalledWith('wf-1','turn-route-b','local');
 });
 it('refreshes the exact target route when the mounted canvas sends a direct activation signal',async()=>{
  apiMock.aiStatus.mockResolvedValue({configured:false,provider:'openai-compatible',model:null});
  const branchGraph={...graph,activeInstanceId:'branch',activeRouteInstanceId:'branch',activeRouteTitle:'模块 B',activeRouteContentRevision:3,nodes:[...graph.nodes,{id:'branch',parentId:'root',topicId:'topic-branch',title:'模块 B',status:'active' as const,contentRevision:3}]};
  apiMock.graph.mockResolvedValueOnce(graph).mockResolvedValueOnce(branchGraph);
  apiMock.messageSnapshot.mockImplementation(async(_workflow:string,instance:string)=>instance==='branch'
   ?{messages:[{id:'b1',role:'user',content:'模块 B 独有消息'}],contentRevision:3}
   :{messages:[{id:'r1',role:'user',content:'数据集独有消息'}],contentRevision:1});
  const view=render(<I18nProvider><ChatPage activeConversationSignal={null}/></I18nProvider>);
  expect(await screen.findByText('数据集独有消息')).toBeInTheDocument();
  view.rerender(<I18nProvider><ChatPage activeConversationSignal={{workflowId:'wf-1',instanceId:'branch',revision:1}}/></I18nProvider>);
  expect(await screen.findByRole('heading',{name:'模块 B'})).toBeInTheDocument();
  expect(await screen.findByText('模块 B 独有消息')).toBeInTheDocument();
  expect(screen.queryByText('数据集独有消息')).not.toBeInTheDocument();
  expect(apiMock.messageSnapshot).toHaveBeenLastCalledWith('wf-1','branch','local');
 });
 it('shows the connection phase and elapsed time, renders assistant Markdown, then removes progress',async()=>{apiMock.aiStatus.mockResolvedValue({configured:true,provider:'openai-compatible',model:'test-model'});let finish!:(x:unknown)=>void;apiMock.chat.mockReturnValue(new Promise(resolve=>{finish=resolve}));apiMock.messages.mockResolvedValueOnce([]).mockResolvedValueOnce([{id:'a1',role:'assistant',content:'**完成**'}]);renderChat();await screen.findByText(/AI 已配置/);fireEvent.change(screen.getByRole('textbox'),{target:{value:'开始'}});fireEvent.click(screen.getByRole('button',{name:'发送'}));expect(await screen.findByText('正在连接模型')).toBeInTheDocument();expect(screen.getByText(/已处理 0s/)).toBeInTheDocument();finish({});expect(await screen.findByText('完成')).toHaveProperty('tagName','STRONG');await waitFor(()=>expect(screen.queryByText('正在连接模型')).not.toBeInTheDocument())});
 it('renders provider lifecycle phases as an icon activity row',async()=>{
  apiMock.aiStatus.mockResolvedValue({configured:true,provider:'openai-compatible',model:'test-model'});
  let emit!:(event:string,data:Record<string,unknown>)=>void,finish!:()=>void;
  apiMock.chatStream=vi.fn((_w:string,_i:string,_c:string,_key:string,onEvent:(event:string,data:Record<string,unknown>)=>void)=>new Promise<void>(resolve=>{emit=onEvent;finish=()=>{onEvent('message.completed',{});resolve()}}));
  renderChat();await screen.findByText(/AI 已配置/);
  fireEvent.change(screen.getByRole('textbox'),{target:{value:'状态测试'}});fireEvent.click(screen.getByRole('button',{name:'发送'}));
  const status=await screen.findByRole('status');expect(status).toHaveClass('activity-status');expect(status.querySelector('svg')).not.toBeNull();
  emit('connection.status',{phase:'waiting',attempt:1,maxAttempts:3});expect(await screen.findByText('已连接，等待模型响应')).toBeInTheDocument();
  emit('connection.status',{phase:'reconnecting',attempt:2,maxAttempts:3});expect(await screen.findByText('连接中断，正在自动重连 (2/3)')).toBeInTheDocument();
  emit('message.delta',{delta:'部分回答'});expect(await screen.findByText('正在接收回答')).toBeInTheDocument();expect(screen.getByText('部分回答')).toBeInTheDocument();
  emit('message.reset',{});await waitFor(()=>expect(screen.queryByText('部分回答')).not.toBeInTheDocument());
  emit('message.delta',{delta:'恢复后的回答'});expect(await screen.findByText('恢复后的回答')).toBeInTheDocument();
  finish();await waitFor(()=>expect(screen.queryByRole('status')).not.toBeInTheDocument());
 });
 it('shows a localized inline AI error by error code',async()=>{apiMock.aiStatus.mockResolvedValue({configured:true,provider:'openai-compatible',model:'test-model'});apiMock.chat.mockRejectedValue(new ApiError('raw timeout',503,'aiTimeout'));renderChat();await screen.findByText(/AI 已配置/);fireEvent.change(screen.getByRole('textbox'),{target:{value:'开始'}});fireEvent.click(screen.getByRole('button',{name:'发送'}));const message=await screen.findByText('模型连接超时，已自动重试。');expect(message.closest('.messages')).not.toBeNull();expect(message.closest('[role="alert"]')).not.toBeNull()});
 it('refreshes the route before retrying a failed answer',async()=>{apiMock.aiStatus.mockResolvedValue({configured:true,provider:'openai-compatible',model:'test-model'});apiMock.chat.mockRejectedValueOnce(new ApiError('raw timeout',503,'aiTimeout'));apiMock.messages.mockResolvedValue([{id:'u1',role:'user',content:'开始'}]);apiMock.messageSnapshot.mockResolvedValue({messages:[{id:'u1',role:'user',content:'开始'}],contentRevision:4});apiMock.regenerate.mockResolvedValue({messages:[{id:'u1',role:'user',content:'开始'},{id:'a1',role:'assistant',content:'恢复回答'}],contentRevision:5});renderChat();await screen.findByText(/AI 已配置/);fireEvent.change(screen.getByRole('textbox'),{target:{value:'开始'}});fireEvent.click(screen.getByRole('button',{name:'发送'}));await screen.findByText('模型连接超时，已自动重试。');fireEvent.click(screen.getByRole('button',{name:'重试回答'}));await waitFor(()=>expect(apiMock.regenerate).toHaveBeenCalledWith('wf-1','root','u1','开始',4));expect(await screen.findByText('恢复回答')).toBeInTheDocument()});
 it('recovers the retry target from the latest local user message when a visible cross-surface error has no request content',async()=>{
  apiMock.aiStatus.mockResolvedValue({configured:true,provider:'openai-compatible',model:'test-model'});
  apiMock.messageSnapshot.mockResolvedValue({messages:[{id:'u9',role:'user',content:'画布遗留问题'}],contentRevision:3});
  apiMock.regenerate.mockResolvedValue({messages:[{id:'u9',role:'user',content:'画布遗留问题'},{id:'a9',role:'assistant',content:'重试已恢复'}],contentRevision:4});
  renderChat();await screen.findByRole('heading',{name:'数据集构建'});
  const startedAt=Date.now()+10;
  window.dispatchEvent(new MessageEvent('message',{data:{type:'conversation-workflow-changed',workflowId:'wf-1',instanceId:'root',phase:'started',requestId:'canvas-retry',senderId:'canvas',sentAt:startedAt}}));
  await screen.findByText('正在连接模型');
  window.dispatchEvent(new MessageEvent('message',{data:{type:'conversation-workflow-changed',workflowId:'wf-1',instanceId:'root',phase:'failed',requestId:'canvas-retry',error:'模型响应超时，请重试。',senderId:'canvas',sentAt:startedAt+1}}));
  await screen.findByRole('button',{name:'重试回答'});
  fireEvent.click(screen.getByRole('button',{name:'重试回答'}));
  await waitFor(()=>expect(apiMock.regenerate).toHaveBeenCalledWith('wf-1','root','u9','画布遗留问题',3));
  expect(await screen.findByText('重试已恢复')).toBeInTheDocument();
 });
 it('loads local messages normally and reveals inherited effective memory only on demand',async()=>{apiMock.aiStatus.mockResolvedValue({configured:false,provider:'openai-compatible',model:null});const child={...graph,activeInstanceId:'child',nodes:[...graph.nodes,{id:'child',parentId:'parent',topicId:'child-topic',title:'子节点',status:'active' as const}]};apiMock.graph.mockResolvedValue(child);apiMock.messages.mockImplementation((_w:string,_i:string,scope:string)=>Promise.resolve(scope==='effective'?[{id:'p1',role:'user',content:'父节点记忆',inherited:true},{id:'l1',role:'user',content:'当前节点消息',inherited:false}]:[{id:'l1',role:'user',content:'当前节点消息',inherited:false}]));renderChat();expect(await screen.findByText('当前节点消息')).toBeInTheDocument();expect(screen.queryByText('父节点记忆')).not.toBeInTheDocument();expect(apiMock.messages).toHaveBeenCalledWith('wf-1','child','local');fireEvent.click(screen.getByRole('button',{name:/继承的路线记忆/}));expect(await screen.findByText('父节点记忆')).toBeInTheDocument();expect(apiMock.messages).toHaveBeenCalledWith('wf-1','child','effective');expect(screen.getAllByText('当前节点消息')).toHaveLength(1)});
 it('mirrors a same-route canvas lifecycle without sending the request a second time',async()=>{
  apiMock.aiStatus.mockResolvedValue({configured:true,provider:'openai-compatible',model:'test-model'});
  apiMock.messageSnapshot
   .mockResolvedValueOnce({messages:[],contentRevision:0})
   .mockResolvedValueOnce({messages:[{id:'u1',role:'user',content:'画布问题'}],contentRevision:1})
   .mockResolvedValueOnce({messages:[{id:'u1',role:'user',content:'画布问题'},{id:'a1',role:'assistant',content:'画布回答'}],contentRevision:2});
  renderChat();
  await screen.findByRole('heading',{name:'数据集构建'});
  const canvasStartedAt=Date.now()+10;
  window.dispatchEvent(new MessageEvent('message',{data:{type:'conversation-workflow-changed',workflowId:'wf-1',instanceId:'root',phase:'started',requestId:'canvas-request',content:'画布问题',senderId:'canvas',sentAt:canvasStartedAt}}));
  expect(await screen.findByText('正在连接模型')).toBeInTheDocument();
  expect(await screen.findByText('画布问题')).toBeInTheDocument();
  expect(apiMock.graph).toHaveBeenCalledTimes(1);
  expect(screen.queryByRole('button',{name:'停止生成'})).not.toBeInTheDocument();
  window.dispatchEvent(new MessageEvent('message',{data:{type:'conversation-workflow-changed',workflowId:'wf-1',instanceId:'root',phase:'completed',requestId:'canvas-request',senderId:'canvas',sentAt:canvasStartedAt+1}}));
  expect(await screen.findByText('画布回答')).toBeInTheDocument();
  await waitFor(()=>expect(apiMock.graph).toHaveBeenCalledTimes(2));
  await waitFor(()=>expect(screen.queryByText('正在连接模型')).not.toBeInTheDocument());
  expect(apiMock.chat).not.toHaveBeenCalled();
  expect(apiMock.send).not.toHaveBeenCalled();
 });
 it('keeps a newer same-route lifecycle pending when an older request finishes late',async()=>{
  apiMock.aiStatus.mockResolvedValue({configured:true,provider:'openai-compatible',model:'test-model'});
  renderChat();await screen.findByRole('heading',{name:'数据集构建'});
  const startedAt=Date.now();
  window.dispatchEvent(new MessageEvent('message',{data:{type:'conversation-workflow-changed',workflowId:'wf-1',instanceId:'root',phase:'started',requestId:'old',senderId:'canvas',sentAt:startedAt}}));
  window.dispatchEvent(new MessageEvent('message',{data:{type:'conversation-workflow-changed',workflowId:'wf-1',instanceId:'root',phase:'started',requestId:'new',senderId:'canvas',sentAt:startedAt+1}}));
  expect(await screen.findByText('正在连接模型')).toBeInTheDocument();
  window.dispatchEvent(new MessageEvent('message',{data:{type:'conversation-workflow-changed',workflowId:'wf-1',instanceId:'root',phase:'failed',requestId:'old',error:'过期错误',senderId:'canvas',sentAt:startedAt+2}}));
  await waitFor(()=>expect(screen.queryByText('过期错误')).not.toBeInTheDocument());
  expect(screen.getByText('正在连接模型')).toBeInTheDocument();
  window.dispatchEvent(new MessageEvent('message',{data:{type:'conversation-workflow-changed',workflowId:'wf-1',instanceId:'root',phase:'completed',requestId:'new',senderId:'canvas',sentAt:startedAt+3}}));
  await waitFor(()=>expect(screen.queryByText('正在连接模型')).not.toBeInTheDocument());
 });
 it('uses one stable request id for JSON delivery and its lifecycle events',async()=>{
  apiMock.aiStatus.mockResolvedValue({configured:true,provider:'openai-compatible',model:'test-model'});
  const post=vi.spyOn(BroadcastChannel.prototype,'postMessage');
  renderChat();await screen.findByText(/AI 已配置/);
  fireEvent.change(screen.getByRole('textbox'),{target:{value:'稳定请求'}});fireEvent.click(screen.getByRole('button',{name:'发送'}));
  await waitFor(()=>expect(apiMock.chat).toHaveBeenCalled());
  await waitFor(()=>expect(post.mock.calls.filter(([value])=>(value as {phase?:string}).phase==='completed')).toHaveLength(1));
  const requestId=apiMock.chat.mock.calls[0][3];
  const events=post.mock.calls.map(([value])=>value as {phase?:string;requestId?:string});
  expect(events.find(value=>value.phase==='started')?.requestId).toBe(requestId);
  expect(events.find(value=>value.phase==='completed')?.requestId).toBe(requestId);
  post.mockRestore();
 });
 it('treats an SSE EOF without a terminal event as a failure',async()=>{
  apiMock.aiStatus.mockResolvedValue({configured:true,provider:'openai-compatible',model:'test-model'});
  apiMock.chatStream=vi.fn().mockResolvedValue(undefined);
  renderChat();await screen.findByText(/AI 已配置/);
  fireEvent.change(screen.getByRole('textbox'),{target:{value:'无终态'}});fireEvent.click(screen.getByRole('button',{name:'发送'}));
  expect(await screen.findByText('模型返回了空响应。')).toBeInTheDocument();
 });
 it('records an SSE terminal even after the user switches routes',async()=>{
  apiMock.aiStatus.mockResolvedValue({configured:true,provider:'openai-compatible',model:'test-model'});
  const branch={...graph,activeInstanceId:'branch',nodes:[...graph.nodes,{id:'branch',parentId:'root',topicId:'t2',title:'模块B',status:'active' as const}]};
  apiMock.graph.mockResolvedValueOnce(graph).mockResolvedValue(branch);
  let complete!:()=>void;
  apiMock.chatStream=vi.fn((_w:string,_i:string,_c:string,_key:string,onEvent:(event:string,data:Record<string,unknown>)=>void)=>new Promise<void>(resolve=>{complete=()=>{onEvent('message.completed',{});resolve()}}));
  const post=vi.spyOn(BroadcastChannel.prototype,'postMessage');
  renderChat();await screen.findByRole('heading',{name:'数据集构建'});
  fireEvent.change(screen.getByRole('textbox'),{target:{value:'A流'}});fireEvent.click(screen.getByRole('button',{name:'发送'}));
  await waitFor(()=>expect(apiMock.chatStream).toHaveBeenCalled());
  window.dispatchEvent(new MessageEvent('message',{data:{type:'conversation-workflow-changed'}}));
  expect(await screen.findByRole('heading',{name:'模块B'})).toBeInTheDocument();
  complete();
  await waitFor(()=>expect(post.mock.calls.some(([value])=>(value as {phase?:string}).phase==='completed')).toBe(true));
  expect(post.mock.calls.some(([value])=>(value as {phase?:string}).phase==='failed')).toBe(false);
  post.mockRestore();
 });
 it('does not abort or claim cancellation when the server rejects stop',async()=>{
  apiMock.aiStatus.mockResolvedValue({configured:true,provider:'openai-compatible',model:'test-model'});
  let complete!:()=>void,streamSignal:AbortSignal|undefined;
  apiMock.chatStream=vi.fn((_w:string,_i:string,_c:string,_key:string,onEvent:(event:string,data:Record<string,unknown>)=>void,signal?:AbortSignal)=>new Promise<void>(resolve=>{streamSignal=signal;complete=()=>{onEvent('message.completed',{});resolve()}}));
  apiMock.cancelChat=vi.fn().mockResolvedValue({ok:true,requestId:'unused',cancelled:false});
  renderChat();await screen.findByText(/AI 已配置/);
  fireEvent.change(screen.getByRole('textbox'),{target:{value:'不要假取消'}});fireEvent.click(screen.getByRole('button',{name:'发送'}));
  const stop=await screen.findByRole('button',{name:'停止生成'});fireEvent.click(stop);
  await waitFor(()=>expect(apiMock.cancelChat).toHaveBeenCalled());
  expect(streamSignal?.aborted).toBe(false);expect(screen.queryByText('已停止生成。')).not.toBeInTheDocument();expect(screen.getByText('正在连接模型')).toBeInTheDocument();
  complete();await waitFor(()=>expect(screen.queryByText('正在连接模型')).not.toBeInTheDocument());
 });
 it('does not regenerate when the pre-retry refresh rejects a stale snapshot',async()=>{
  apiMock.aiStatus.mockResolvedValue({configured:true,provider:'openai-compatible',model:'test-model'});apiMock.chat.mockRejectedValueOnce(new ApiError('raw timeout',503,'aiTimeout'));
  apiMock.messageSnapshot
   .mockResolvedValueOnce({messages:[],contentRevision:5})
   .mockResolvedValueOnce({messages:[{id:'u1',role:'user',content:'开始'}],contentRevision:5})
   .mockResolvedValueOnce({messages:[{id:'u1',role:'user',content:'开始'}],contentRevision:4});
  renderChat();await screen.findByText(/AI 已配置/);
  fireEvent.change(screen.getByRole('textbox'),{target:{value:'开始'}});fireEvent.click(screen.getByRole('button',{name:'发送'}));
  await screen.findByText('模型连接超时，已自动重试。');fireEvent.click(screen.getByRole('button',{name:'重试回答'}));
  expect(await screen.findByText('编辑期间对话已发生变化，请重新打开编辑后再试。')).toBeInTheDocument();expect(apiMock.regenerate).not.toHaveBeenCalled();
 });
 it('ignores a late lifecycle failure from the route that was left',async()=>{
  apiMock.aiStatus.mockResolvedValue({configured:true,provider:'openai-compatible',model:'test-model'});
  const branch={...graph,activeInstanceId:'branch',nodes:[...graph.nodes,{id:'branch',parentId:'root',topicId:'t2',title:'模块B',status:'active' as const}]};
  apiMock.graph.mockResolvedValueOnce(graph).mockResolvedValue(branch);
  apiMock.messageSnapshot.mockImplementation(async(_w:string,i:string)=>({messages:i==='branch'?[{id:'b1',role:'user',content:'B消息'}]:[],contentRevision:1}));
  renderChat();
  await screen.findByRole('heading',{name:'数据集构建'});
  window.dispatchEvent(new MessageEvent('message',{data:{type:'conversation-workflow-changed',workflowId:'wf-1',instanceId:'root',phase:'started',content:'A请求',senderId:'canvas',sentAt:Date.now()}}));
  expect(await screen.findByText('正在连接模型')).toBeInTheDocument();
  window.dispatchEvent(new MessageEvent('message',{data:{type:'conversation-workflow-changed',workflowId:'wf-1'}}));
  expect(await screen.findByRole('heading',{name:'模块B'})).toBeInTheDocument();
  expect(await screen.findByText('B消息')).toBeInTheDocument();
  window.dispatchEvent(new MessageEvent('message',{data:{type:'conversation-workflow-changed',workflowId:'wf-1',instanceId:'root',phase:'failed',content:'A请求',error:'A旧错误',senderId:'canvas',sentAt:Date.now()}}));
  await waitFor(()=>expect(screen.queryByText('A旧错误')).not.toBeInTheDocument());
  expect(screen.queryByText('正在连接模型')).not.toBeInTheDocument();
  expect(screen.getByText('B消息')).toBeInTheDocument();
 });
 it('ignores a late message response from the previously active node',async()=>{apiMock.aiStatus.mockResolvedValue({configured:false,provider:'openai-compatible',model:null});const branch={...graph,activeInstanceId:'branch',nodes:[...graph.nodes,{id:'branch',parentId:'root',topicId:'t2',title:'模块B',status:'active' as const}]};let rootDone!:(x:unknown)=>void,branchDone!:(x:unknown)=>void;const rootPromise=new Promise(resolve=>{rootDone=resolve}),branchPromise=new Promise(resolve=>{branchDone=resolve});apiMock.graph.mockResolvedValueOnce(graph).mockResolvedValue(branch);apiMock.messages.mockImplementation((_w:string,i:string)=>i==='root'?rootPromise:branchPromise);renderChat();await screen.findByText('数据集构建');window.dispatchEvent(new MessageEvent('message',{data:{type:'conversation-workflow-changed'}}));expect(await screen.findByText('模块B')).toBeInTheDocument();branchDone([{id:'b',role:'user',content:'B消息'}]);expect(await screen.findByText('B消息')).toBeInTheDocument();rootDone([{id:'a',role:'user',content:'A旧消息'}]);await waitFor(()=>expect(screen.queryByText('A旧消息')).not.toBeInTheDocument())});
 it('hides the previous node snapshot before the next node finishes loading',async()=>{apiMock.aiStatus.mockResolvedValue({configured:false,provider:'openai-compatible',model:null});const branch={...graph,activeInstanceId:'branch',nodes:[...graph.nodes,{id:'branch',parentId:'root',topicId:'t2',title:'模块B',status:'active' as const}]};let branchDone!:(x:unknown)=>void;apiMock.graph.mockResolvedValueOnce(graph).mockResolvedValue(branch);apiMock.messageSnapshot.mockResolvedValueOnce({messages:[{id:'a',role:'user',content:'A 已加载消息'}],contentRevision:3}).mockReturnValueOnce(new Promise(resolve=>{branchDone=resolve}));renderChat();expect(await screen.findByText('A 已加载消息')).toBeInTheDocument();window.dispatchEvent(new MessageEvent('message',{data:{type:'conversation-workflow-changed'}}));expect(await screen.findByText('模块B')).toBeInTheDocument();expect(screen.queryByText('A 已加载消息')).not.toBeInTheDocument();branchDone({messages:[{id:'b',role:'user',content:'B 延迟消息'}],contentRevision:1});expect(await screen.findByText('B 延迟消息')).toBeInTheDocument()});
 it('uses a synchronous send lock to prevent duplicate submissions',async()=>{apiMock.aiStatus.mockResolvedValue({configured:true,provider:'openai-compatible',model:'test-model'});apiMock.chat.mockReturnValue(new Promise(()=>{}));renderChat();await screen.findByText(/AI 已配置/);const box=screen.getByRole('textbox');fireEvent.change(box,{target:{value:'只发送一次'}});const form=box.closest('form')!;fireEvent.submit(form);fireEvent.submit(form);expect(apiMock.chat).toHaveBeenCalledTimes(1);expect(apiMock.chat).toHaveBeenCalledWith('wf-1','root','只发送一次',expect.any(String))});
 it('keeps independent in-flight locks across an A to B to A switch sequence',async()=>{apiMock.aiStatus.mockResolvedValue({configured:true,provider:'openai-compatible',model:'test-model'});const branch={...graph,activeInstanceId:'branch',nodes:[...graph.nodes,{id:'branch',parentId:'root',topicId:'t2',title:'模块B',status:'active' as const}]};apiMock.graph.mockResolvedValueOnce(graph).mockResolvedValueOnce(branch).mockResolvedValue(graph);apiMock.chat.mockReturnValue(new Promise(()=>{}));renderChat();await screen.findByText('数据集构建');let box=screen.getByRole('textbox');fireEvent.change(box,{target:{value:'A请求'}});fireEvent.submit(box.closest('form')!);window.dispatchEvent(new MessageEvent('message',{data:{type:'conversation-workflow-changed'}}));await screen.findByText('模块B');box=screen.getByRole('textbox');fireEvent.change(box,{target:{value:'B请求'}});fireEvent.submit(box.closest('form')!);window.dispatchEvent(new MessageEvent('message',{data:{type:'conversation-workflow-changed'}}));await screen.findByText('数据集构建');box=screen.getByRole('textbox');fireEvent.change(box,{target:{value:'A重复请求'}});fireEvent.submit(box.closest('form')!);expect(apiMock.chat).toHaveBeenCalledTimes(2);expect(apiMock.chat).toHaveBeenNthCalledWith(1,'wf-1','root','A请求',expect.any(String));expect(apiMock.chat).toHaveBeenNthCalledWith(2,'wf-1','branch','B请求',expect.any(String))});
 it('renders last-message edit and copy actions as accessible icon-only buttons',async()=>{
  apiMock.aiStatus.mockResolvedValue({configured:false,provider:'openai-compatible',model:null});
  apiMock.messageSnapshot.mockResolvedValue({messages:[{id:'u1',role:'user',content:'最近问题'}],contentRevision:1});
  const writeText=vi.fn().mockResolvedValue(undefined);Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText}});
  renderChat();expect(await screen.findByText('最近问题')).toBeInTheDocument();
  const actions=screen.getByLabelText('消息操作');
  const edit=screen.getByRole('button',{name:'编辑'}),copy=screen.getByRole('button',{name:'复制'});
  expect(actions).toContainElement(edit);expect(actions).toContainElement(copy);
  expect(edit).toHaveAttribute('title','编辑');expect(copy).toHaveAttribute('title','复制');
  expect(edit).toHaveTextContent('');expect(copy).toHaveTextContent('');
  expect(edit.querySelector('svg')).toHaveAttribute('aria-hidden','true');expect(copy.querySelector('svg')).toHaveAttribute('aria-hidden','true');
  fireEvent.click(copy);await waitFor(()=>expect(copy).toHaveAttribute('aria-label','已复制'));
  expect(copy).toHaveAttribute('title','已复制');expect(writeText).toHaveBeenCalledWith('最近问题');
 });
 it('offers edit and copy only on the last local user message and regenerates once',async()=>{apiMock.aiStatus.mockResolvedValue({configured:true,provider:'openai-compatible',model:'test-model'});apiMock.messages.mockResolvedValue([{id:'u0',role:'user',content:'旧问题'},{id:'a0',role:'assistant',content:'旧回答'},{id:'u1',role:'user',content:'最近问题'}]);apiMock.messageSnapshot.mockImplementation(async()=>({messages:await apiMock.messages(),contentRevision:7}));let finish!:(x:unknown)=>void;apiMock.regenerate.mockReturnValue(new Promise(resolve=>{finish=resolve}));const writeText=vi.fn().mockResolvedValue(undefined);Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText}});renderChat();expect(await screen.findByText('最近问题')).toBeInTheDocument();expect(screen.getAllByRole('button',{name:'编辑'})).toHaveLength(1);fireEvent.click(screen.getByRole('button',{name:'复制'}));await waitFor(()=>expect(writeText).toHaveBeenCalledWith('最近问题'));fireEvent.click(screen.getByRole('button',{name:'编辑'}));const editor=screen.getByRole('textbox',{name:'编辑你的提问'});fireEvent.change(editor,{target:{value:'修改后的问题'}});const save=screen.getByRole('button',{name:'保存并重新生成'});fireEvent.click(save);fireEvent.click(save);expect(apiMock.regenerate).toHaveBeenCalledTimes(1);expect(apiMock.regenerate).toHaveBeenCalledWith('wf-1','root','u1','修改后的问题',7);expect(await screen.findByText('连接中断，正在自动重连')).toBeInTheDocument();finish({messages:[{id:'u1',role:'user',content:'修改后的问题'},{id:'a1',role:'assistant',content:'**新回答**'}],contentRevision:9});expect(await screen.findByText('新回答')).toHaveProperty('tagName','STRONG');expect(screen.queryByText('连接中断，正在自动重连')).not.toBeInTheDocument()});
 it('ignores a regenerate result after switching to another node',async()=>{apiMock.aiStatus.mockResolvedValue({configured:true,provider:'openai-compatible',model:'test-model'});const branch={...graph,activeInstanceId:'branch',nodes:[...graph.nodes,{id:'branch',parentId:'root',topicId:'t2',title:'模块B',status:'active' as const}]};apiMock.graph.mockResolvedValueOnce(graph).mockResolvedValue(branch);apiMock.messageSnapshot.mockImplementation(async(_w:string,i:string)=>({messages:i==='root'?[{id:'u1',role:'user',content:'A问题'}]:[{id:'b1',role:'user',content:'B消息'}],contentRevision:1}));let finish!:(x:unknown)=>void;apiMock.regenerate.mockReturnValue(new Promise(resolve=>{finish=resolve}));renderChat();expect(await screen.findByText('A问题')).toBeInTheDocument();fireEvent.click(screen.getByRole('button',{name:'编辑'}));fireEvent.change(screen.getByRole('textbox',{name:'编辑你的提问'}),{target:{value:'A修改'}});fireEvent.click(screen.getByRole('button',{name:'保存并重新生成'}));window.dispatchEvent(new MessageEvent('message',{data:{type:'conversation-workflow-changed'}}));expect(await screen.findByText('B消息')).toBeInTheDocument();finish({messages:[{id:'u1',role:'user',content:'A修改'}],contentRevision:2});await waitFor(()=>expect(screen.queryByText('A修改')).not.toBeInTheDocument());expect(screen.getByText('B消息')).toBeInTheDocument()});
 it('supports a numeric SQLite message id and keeps original content when regenerate fails',async()=>{apiMock.aiStatus.mockResolvedValue({configured:true,provider:'openai-compatible',model:'test-model'});apiMock.messageSnapshot.mockResolvedValue({messages:[{id:42,role:'user',content:'数字 ID 原问题'}],contentRevision:5});apiMock.regenerate.mockRejectedValue(new ApiError('conflict',409,'conflict'));renderChat();expect(await screen.findByText('数字 ID 原问题')).toBeInTheDocument();fireEvent.click(screen.getByRole('button',{name:'编辑'}));fireEvent.change(screen.getByRole('textbox',{name:'编辑你的提问'}),{target:{value:'不应本地写入'}});fireEvent.click(screen.getByRole('button',{name:'保存并重新生成'}));await waitFor(()=>expect(apiMock.regenerate).toHaveBeenCalledWith('wf-1','root',42,'不应本地写入',5));expect(screen.getByText('数字 ID 原问题')).toBeInTheDocument();expect(screen.queryByText('不应本地写入')).not.toBeInTheDocument();expect(screen.getByRole('alert')).toHaveTextContent('编辑期间对话已发生变化，请重新打开编辑后再试。')});
 it('does not let an older Agent completion refresh overwrite a newer chat snapshot',async()=>{apiMock.aiStatus.mockResolvedValue({configured:false,provider:'openai-compatible',model:null});let staleDone!:(value:unknown)=>void;apiMock.messageSnapshot.mockResolvedValueOnce({messages:[{id:'base',role:'user',content:'基础消息'}],contentRevision:5}).mockReturnValueOnce(new Promise(resolve=>{staleDone=resolve})).mockResolvedValue({messages:[{id:'base',role:'user',content:'基础消息'},{id:'new',role:'user',content:'较新的普通消息'}],contentRevision:7});apiMock.createAgentRun.mockResolvedValue({runId:88,workflowId:'wf-1',instanceId:'root',status:'completed',inputContentRevision:5,objective:'数据集构建',constraints:[],deliverables:[],acceptanceChecks:[],finalAnswer:'Agent 完成'});apiMock.agentRun.mockResolvedValue({runId:88,workflowId:'wf-1',instanceId:'root',status:'completed',inputContentRevision:5,objective:'数据集构建',constraints:[],deliverables:[],acceptanceChecks:[],finalAnswer:'Agent 完成'});renderChat();expect(await screen.findByText('基础消息')).toBeInTheDocument();fireEvent.click(screen.getByRole('button',{name:/交给 Agent/}));fireEvent.click(screen.getByRole('checkbox'));fireEvent.click(screen.getByRole('button',{name:'启动运行'}));await waitFor(()=>expect(apiMock.messageSnapshot).toHaveBeenCalledTimes(2));const composer=screen.getByPlaceholderText('在这条记忆路线中发送消息…');fireEvent.change(composer,{target:{value:'较新的普通消息'}});const send=screen.getByRole('button',{name:'发送'});expect(send).toBeEnabled();fireEvent.click(send);await waitFor(()=>expect(apiMock.send).toHaveBeenCalledWith('wf-1','root','较新的普通消息'));expect(await screen.findByText('较新的普通消息')).toBeInTheDocument();staleDone({messages:[{id:'stale',role:'assistant',content:'过期 Agent 快照'}],contentRevision:6});await waitFor(()=>expect(screen.queryByText('过期 Agent 快照')).not.toBeInTheDocument());expect(screen.getByText('较新的普通消息')).toBeInTheDocument()});
});
