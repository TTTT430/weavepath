import{cleanup,fireEvent,render,screen,waitFor}from'@testing-library/react';
import{afterEach,beforeEach,describe,expect,it,vi}from'vitest';
import{I18nProvider}from'../lib/i18n';
import{ChatPage}from'./ChatPage';
import{ApiError}from'../lib/api';

const apiMock=vi.hoisted(()=>({
 workflows:vi.fn(),graph:vi.fn(),messages:vi.fn(),messageSnapshot:vi.fn(),regenerate:vi.fn(),agentRuns:vi.fn(),createAgentRun:vi.fn(),agentRun:vi.fn(),agentRunEvents:vi.fn(),aiStatus:vi.fn(),aiSettings:vi.fn(),saveAISettings:vi.fn(),resetAISettings:vi.fn(),validateAISettings:vi.fn(),send:vi.fn(),chat:vi.fn(),
 createWorkflow:vi.fn(),fork:vi.fn(),activate:vi.fn(),prunePlan:vi.fn(),pruneCommit:vi.fn(),routes:vi.fn()
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
 apiMock.workflows.mockResolvedValue([{id:'wf-1',name:'研究项目',activeInstanceId:'root'}]);
 apiMock.graph.mockResolvedValue(graph);apiMock.messages.mockResolvedValue([]);apiMock.send.mockResolvedValue({id:'u1',role:'user',content:'测试消息'});
 apiMock.messageSnapshot.mockImplementation(async(w:string,i:string,scope:string)=>({messages:await apiMock.messages(w,i,scope),contentRevision:1}));apiMock.regenerate.mockResolvedValue({messages:[],contentRevision:2});apiMock.agentRuns.mockResolvedValue([]);apiMock.agentRun.mockResolvedValue({});apiMock.agentRunEvents.mockResolvedValue({runId:'',events:[],nextAfterSequence:null});
 apiMock.aiSettings.mockResolvedValue({configured:false,provider:'openai-compatible',baseUrl:null,model:null,timeoutSeconds:60,systemPrompt:'',hasApiKey:false,source:'none',persistence:'memory'});
 apiMock.chat.mockResolvedValue({userMessage:{id:'u1',role:'user',content:'测试消息'},assistantMessage:{id:'a1',role:'assistant',content:'助手回复'}});
});

afterEach(()=>{cleanup();vi.clearAllMocks()});

describe('chat delivery mode',()=>{
 it('opens model settings from the compact sidebar button without translating conversation titles',async()=>{apiMock.aiStatus.mockResolvedValue({configured:false,provider:'openai-compatible',model:null});renderChat();expect(await screen.findByText('数据集构建')).toBeInTheDocument();expect(screen.queryByRole('button',{name:/继承的路线记忆/})).not.toBeInTheDocument();fireEvent.click(screen.getByRole('button',{name:/设置/}));expect(await screen.findByRole('heading',{name:'模型设置'})).toBeInTheDocument();expect(screen.getByText('数据集构建')).toBeInTheDocument()});
 it('saves through the message endpoint when AI is not configured',async()=>{
  apiMock.aiStatus.mockResolvedValue({configured:false,provider:'openai-compatible',model:null});
  apiMock.messages.mockResolvedValueOnce([]).mockResolvedValueOnce([{id:'u1',role:'user',content:'测试消息'}]);
  renderChat();
  expect(await screen.findByText('仅记录模式 · 尚未连接 AI')).toBeInTheDocument();
  fireEvent.change(screen.getByRole('textbox'),{target:{value:'测试消息'}});fireEvent.click(screen.getByRole('button',{name:'发送'}));
  await waitFor(()=>expect(apiMock.send).toHaveBeenCalledWith('wf-1','root','测试消息'));
  expect(apiMock.chat).not.toHaveBeenCalled();expect(await screen.findByText('测试消息')).toBeInTheDocument();
 });

 it('uses the chat endpoint and renders the assistant reply when AI is configured',async()=>{
  apiMock.aiStatus.mockResolvedValue({configured:true,provider:'openai-compatible',model:'test-model'});
  apiMock.messages.mockResolvedValueOnce([]).mockResolvedValueOnce([
   {id:'u1',role:'user',content:'测试消息'},{id:'a1',role:'assistant',content:'助手回复'}
  ]);
  renderChat();
  expect(await screen.findByText('AI 已配置 · test-model')).toBeInTheDocument();
  fireEvent.change(screen.getByRole('textbox'),{target:{value:'测试消息'}});fireEvent.click(screen.getByRole('button',{name:'发送'}));
  await waitFor(()=>expect(apiMock.chat).toHaveBeenCalledWith('wf-1','root','测试消息'));
  expect(apiMock.send).not.toHaveBeenCalled();expect(await screen.findByText('助手回复')).toBeInTheDocument();
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
 it('shows thinking in the stream, renders assistant Markdown, then removes thinking',async()=>{apiMock.aiStatus.mockResolvedValue({configured:true,provider:'openai-compatible',model:'test-model'});let finish!:(x:unknown)=>void;apiMock.chat.mockReturnValue(new Promise(resolve=>{finish=resolve}));apiMock.messages.mockResolvedValueOnce([]).mockResolvedValueOnce([{id:'a1',role:'assistant',content:'**完成**'}]);renderChat();await screen.findByText(/AI 已配置/);fireEvent.change(screen.getByRole('textbox'),{target:{value:'开始'}});fireEvent.click(screen.getByRole('button',{name:'发送'}));expect(await screen.findByText('正在思考')).toBeInTheDocument();finish({});expect(await screen.findByText('完成')).toHaveProperty('tagName','STRONG');await waitFor(()=>expect(screen.queryByText('正在思考')).not.toBeInTheDocument())});
 it('shows a localized inline AI error by error code',async()=>{apiMock.aiStatus.mockResolvedValue({configured:true,provider:'openai-compatible',model:'test-model'});apiMock.chat.mockRejectedValue(new ApiError('raw timeout',503,'aiTimeout'));renderChat();await screen.findByText(/AI 已配置/);fireEvent.change(screen.getByRole('textbox'),{target:{value:'开始'}});fireEvent.click(screen.getByRole('button',{name:'发送'}));const message=await screen.findByText('模型响应超时，请重试。');expect(message.closest('.messages')).not.toBeNull();expect(message.closest('[role="alert"]')).not.toBeNull()});
 it('refreshes the route before retrying a failed answer',async()=>{apiMock.aiStatus.mockResolvedValue({configured:true,provider:'openai-compatible',model:'test-model'});apiMock.chat.mockRejectedValueOnce(new ApiError('raw timeout',503,'aiTimeout'));apiMock.messages.mockResolvedValue([{id:'u1',role:'user',content:'开始'}]);apiMock.messageSnapshot.mockResolvedValue({messages:[{id:'u1',role:'user',content:'开始'}],contentRevision:4});apiMock.regenerate.mockResolvedValue({messages:[{id:'u1',role:'user',content:'开始'},{id:'a1',role:'assistant',content:'恢复回答'}],contentRevision:5});renderChat();await screen.findByText(/AI 已配置/);fireEvent.change(screen.getByRole('textbox'),{target:{value:'开始'}});fireEvent.click(screen.getByRole('button',{name:'发送'}));await screen.findByText('模型响应超时，请重试。');fireEvent.click(screen.getByRole('button',{name:'重试回答'}));await waitFor(()=>expect(apiMock.regenerate).toHaveBeenCalledWith('wf-1','root','u1','开始',4));expect(await screen.findByText('恢复回答')).toBeInTheDocument()});
 it('loads local messages normally and reveals inherited effective memory only on demand',async()=>{apiMock.aiStatus.mockResolvedValue({configured:false,provider:'openai-compatible',model:null});const child={...graph,activeInstanceId:'child',nodes:[...graph.nodes,{id:'child',parentId:'parent',topicId:'child-topic',title:'子节点',status:'active' as const}]};apiMock.graph.mockResolvedValue(child);apiMock.messages.mockImplementation((_w:string,_i:string,scope:string)=>Promise.resolve(scope==='effective'?[{id:'p1',role:'user',content:'父节点记忆',inherited:true},{id:'l1',role:'user',content:'当前节点消息',inherited:false}]:[{id:'l1',role:'user',content:'当前节点消息',inherited:false}]));renderChat();expect(await screen.findByText('当前节点消息')).toBeInTheDocument();expect(screen.queryByText('父节点记忆')).not.toBeInTheDocument();expect(apiMock.messages).toHaveBeenCalledWith('wf-1','child','local');fireEvent.click(screen.getByRole('button',{name:/继承的路线记忆/}));expect(await screen.findByText('父节点记忆')).toBeInTheDocument();expect(apiMock.messages).toHaveBeenCalledWith('wf-1','child','effective');expect(screen.getAllByText('当前节点消息')).toHaveLength(1)});
 it('ignores a late message response from the previously active node',async()=>{apiMock.aiStatus.mockResolvedValue({configured:false,provider:'openai-compatible',model:null});const branch={...graph,activeInstanceId:'branch',nodes:[...graph.nodes,{id:'branch',parentId:'root',topicId:'t2',title:'模块B',status:'active' as const}]};let rootDone!:(x:unknown)=>void,branchDone!:(x:unknown)=>void;const rootPromise=new Promise(resolve=>{rootDone=resolve}),branchPromise=new Promise(resolve=>{branchDone=resolve});apiMock.graph.mockResolvedValueOnce(graph).mockResolvedValue(branch);apiMock.messages.mockImplementation((_w:string,i:string)=>i==='root'?rootPromise:branchPromise);renderChat();await screen.findByText('数据集构建');window.dispatchEvent(new MessageEvent('message',{data:{type:'conversation-workflow-changed'}}));expect(await screen.findByText('模块B')).toBeInTheDocument();branchDone([{id:'b',role:'user',content:'B消息'}]);expect(await screen.findByText('B消息')).toBeInTheDocument();rootDone([{id:'a',role:'user',content:'A旧消息'}]);await waitFor(()=>expect(screen.queryByText('A旧消息')).not.toBeInTheDocument())});
 it('hides the previous node snapshot before the next node finishes loading',async()=>{apiMock.aiStatus.mockResolvedValue({configured:false,provider:'openai-compatible',model:null});const branch={...graph,activeInstanceId:'branch',nodes:[...graph.nodes,{id:'branch',parentId:'root',topicId:'t2',title:'模块B',status:'active' as const}]};let branchDone!:(x:unknown)=>void;apiMock.graph.mockResolvedValueOnce(graph).mockResolvedValue(branch);apiMock.messageSnapshot.mockResolvedValueOnce({messages:[{id:'a',role:'user',content:'A 已加载消息'}],contentRevision:3}).mockReturnValueOnce(new Promise(resolve=>{branchDone=resolve}));renderChat();expect(await screen.findByText('A 已加载消息')).toBeInTheDocument();window.dispatchEvent(new MessageEvent('message',{data:{type:'conversation-workflow-changed'}}));expect(await screen.findByText('模块B')).toBeInTheDocument();expect(screen.queryByText('A 已加载消息')).not.toBeInTheDocument();branchDone({messages:[{id:'b',role:'user',content:'B 延迟消息'}],contentRevision:1});expect(await screen.findByText('B 延迟消息')).toBeInTheDocument()});
 it('uses a synchronous send lock to prevent duplicate submissions',async()=>{apiMock.aiStatus.mockResolvedValue({configured:true,provider:'openai-compatible',model:'test-model'});apiMock.chat.mockReturnValue(new Promise(()=>{}));renderChat();await screen.findByText(/AI 已配置/);const box=screen.getByRole('textbox');fireEvent.change(box,{target:{value:'只发送一次'}});const form=box.closest('form')!;fireEvent.submit(form);fireEvent.submit(form);expect(apiMock.chat).toHaveBeenCalledTimes(1);expect(apiMock.chat).toHaveBeenCalledWith('wf-1','root','只发送一次')});
 it('keeps independent in-flight locks across an A to B to A switch sequence',async()=>{apiMock.aiStatus.mockResolvedValue({configured:true,provider:'openai-compatible',model:'test-model'});const branch={...graph,activeInstanceId:'branch',nodes:[...graph.nodes,{id:'branch',parentId:'root',topicId:'t2',title:'模块B',status:'active' as const}]};apiMock.graph.mockResolvedValueOnce(graph).mockResolvedValueOnce(branch).mockResolvedValue(graph);apiMock.chat.mockReturnValue(new Promise(()=>{}));renderChat();await screen.findByText('数据集构建');let box=screen.getByRole('textbox');fireEvent.change(box,{target:{value:'A请求'}});fireEvent.submit(box.closest('form')!);window.dispatchEvent(new MessageEvent('message',{data:{type:'conversation-workflow-changed'}}));await screen.findByText('模块B');box=screen.getByRole('textbox');fireEvent.change(box,{target:{value:'B请求'}});fireEvent.submit(box.closest('form')!);window.dispatchEvent(new MessageEvent('message',{data:{type:'conversation-workflow-changed'}}));await screen.findByText('数据集构建');box=screen.getByRole('textbox');fireEvent.change(box,{target:{value:'A重复请求'}});fireEvent.submit(box.closest('form')!);expect(apiMock.chat).toHaveBeenCalledTimes(2);expect(apiMock.chat).toHaveBeenNthCalledWith(1,'wf-1','root','A请求');expect(apiMock.chat).toHaveBeenNthCalledWith(2,'wf-1','branch','B请求')});
 it('offers edit and copy only on the last local user message and regenerates once',async()=>{apiMock.aiStatus.mockResolvedValue({configured:true,provider:'openai-compatible',model:'test-model'});apiMock.messages.mockResolvedValue([{id:'u0',role:'user',content:'旧问题'},{id:'a0',role:'assistant',content:'旧回答'},{id:'u1',role:'user',content:'最近问题'}]);apiMock.messageSnapshot.mockImplementation(async()=>({messages:await apiMock.messages(),contentRevision:7}));let finish!:(x:unknown)=>void;apiMock.regenerate.mockReturnValue(new Promise(resolve=>{finish=resolve}));const writeText=vi.fn().mockResolvedValue(undefined);Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText}});renderChat();expect(await screen.findByText('最近问题')).toBeInTheDocument();expect(screen.getAllByRole('button',{name:'编辑'})).toHaveLength(1);fireEvent.click(screen.getByRole('button',{name:'复制'}));await waitFor(()=>expect(writeText).toHaveBeenCalledWith('最近问题'));fireEvent.click(screen.getByRole('button',{name:'编辑'}));const editor=screen.getByRole('textbox',{name:'编辑你的提问'});fireEvent.change(editor,{target:{value:'修改后的问题'}});const save=screen.getByRole('button',{name:'保存并重新生成'});fireEvent.click(save);fireEvent.click(save);expect(apiMock.regenerate).toHaveBeenCalledTimes(1);expect(apiMock.regenerate).toHaveBeenCalledWith('wf-1','root','u1','修改后的问题',7);expect(await screen.findByText('正在思考')).toBeInTheDocument();finish({messages:[{id:'u1',role:'user',content:'修改后的问题'},{id:'a1',role:'assistant',content:'**新回答**'}],contentRevision:9});expect(await screen.findByText('新回答')).toHaveProperty('tagName','STRONG');expect(screen.queryByText('正在思考')).not.toBeInTheDocument()});
 it('ignores a regenerate result after switching to another node',async()=>{apiMock.aiStatus.mockResolvedValue({configured:true,provider:'openai-compatible',model:'test-model'});const branch={...graph,activeInstanceId:'branch',nodes:[...graph.nodes,{id:'branch',parentId:'root',topicId:'t2',title:'模块B',status:'active' as const}]};apiMock.graph.mockResolvedValueOnce(graph).mockResolvedValue(branch);apiMock.messageSnapshot.mockImplementation(async(_w:string,i:string)=>({messages:i==='root'?[{id:'u1',role:'user',content:'A问题'}]:[{id:'b1',role:'user',content:'B消息'}],contentRevision:1}));let finish!:(x:unknown)=>void;apiMock.regenerate.mockReturnValue(new Promise(resolve=>{finish=resolve}));renderChat();expect(await screen.findByText('A问题')).toBeInTheDocument();fireEvent.click(screen.getByRole('button',{name:'编辑'}));fireEvent.change(screen.getByRole('textbox',{name:'编辑你的提问'}),{target:{value:'A修改'}});fireEvent.click(screen.getByRole('button',{name:'保存并重新生成'}));window.dispatchEvent(new MessageEvent('message',{data:{type:'conversation-workflow-changed'}}));expect(await screen.findByText('B消息')).toBeInTheDocument();finish({messages:[{id:'u1',role:'user',content:'A修改'}],contentRevision:2});await waitFor(()=>expect(screen.queryByText('A修改')).not.toBeInTheDocument());expect(screen.getByText('B消息')).toBeInTheDocument()});
 it('supports a numeric SQLite message id and keeps original content when regenerate fails',async()=>{apiMock.aiStatus.mockResolvedValue({configured:true,provider:'openai-compatible',model:'test-model'});apiMock.messageSnapshot.mockResolvedValue({messages:[{id:42,role:'user',content:'数字 ID 原问题'}],contentRevision:5});apiMock.regenerate.mockRejectedValue(new ApiError('conflict',409,'conflict'));renderChat();expect(await screen.findByText('数字 ID 原问题')).toBeInTheDocument();fireEvent.click(screen.getByRole('button',{name:'编辑'}));fireEvent.change(screen.getByRole('textbox',{name:'编辑你的提问'}),{target:{value:'不应本地写入'}});fireEvent.click(screen.getByRole('button',{name:'保存并重新生成'}));await waitFor(()=>expect(apiMock.regenerate).toHaveBeenCalledWith('wf-1','root',42,'不应本地写入',5));expect(screen.getByText('数字 ID 原问题')).toBeInTheDocument();expect(screen.queryByText('不应本地写入')).not.toBeInTheDocument();expect(screen.getByRole('alert')).toHaveTextContent('编辑期间对话已发生变化，请重新打开编辑后再试。')});
 it('does not let an older Agent completion refresh overwrite a newer chat snapshot',async()=>{apiMock.aiStatus.mockResolvedValue({configured:false,provider:'openai-compatible',model:null});let staleDone!:(value:unknown)=>void;apiMock.messageSnapshot.mockResolvedValueOnce({messages:[{id:'base',role:'user',content:'基础消息'}],contentRevision:5}).mockReturnValueOnce(new Promise(resolve=>{staleDone=resolve})).mockResolvedValue({messages:[{id:'base',role:'user',content:'基础消息'},{id:'new',role:'user',content:'较新的普通消息'}],contentRevision:7});apiMock.createAgentRun.mockResolvedValue({runId:88,workflowId:'wf-1',instanceId:'root',status:'completed',inputContentRevision:5,objective:'数据集构建',constraints:[],deliverables:[],acceptanceChecks:[],finalAnswer:'Agent 完成'});apiMock.agentRun.mockResolvedValue({runId:88,workflowId:'wf-1',instanceId:'root',status:'completed',inputContentRevision:5,objective:'数据集构建',constraints:[],deliverables:[],acceptanceChecks:[],finalAnswer:'Agent 完成'});renderChat();expect(await screen.findByText('基础消息')).toBeInTheDocument();fireEvent.click(screen.getByRole('button',{name:/交给 Agent/}));fireEvent.click(screen.getByRole('checkbox'));fireEvent.click(screen.getByRole('button',{name:'启动运行'}));await waitFor(()=>expect(apiMock.messageSnapshot).toHaveBeenCalledTimes(2));const composer=screen.getByPlaceholderText('在这条记忆路线中发送消息…');fireEvent.change(composer,{target:{value:'较新的普通消息'}});const send=screen.getByRole('button',{name:'发送'});expect(send).toBeEnabled();fireEvent.click(send);await waitFor(()=>expect(apiMock.send).toHaveBeenCalledWith('wf-1','root','较新的普通消息'));expect(await screen.findByText('较新的普通消息')).toBeInTheDocument();staleDone({messages:[{id:'stale',role:'assistant',content:'过期 Agent 快照'}],contentRevision:6});await waitFor(()=>expect(screen.queryByText('过期 Agent 快照')).not.toBeInTheDocument());expect(screen.getByText('较新的普通消息')).toBeInTheDocument()});
});
