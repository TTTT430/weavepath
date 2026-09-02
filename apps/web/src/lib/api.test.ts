import{afterEach,describe,expect,it,vi}from'vitest';
import{api,ApiError}from'./api';

const response=(status:number,body:unknown)=>({
 ok:status>=200&&status<300,
 status,
 json:vi.fn().mockResolvedValue(body),
})as unknown as Response;

afterEach(()=>vi.unstubAllGlobals());

describe('agent runtime API contract',()=>{
 it('keeps the persisted run id on an error response',async()=>{
  vi.stubGlobal('fetch',vi.fn().mockResolvedValue(response(502,{code:'toolExecutionFailed',error:'failed',runId:'run-7'})));
  let caught:unknown;
  try{await api.createAgentRun('wf','node',{objective:'test',constraints:[],deliverables:[],acceptanceChecks:[],expectedContentRevision:2,idempotencyKey:'idem'})}
  catch(error){caught=error}
  expect(caught).toBeInstanceOf(ApiError);
  expect(caught).toMatchObject({status:502,code:'toolExecutionFailed',runId:'run-7'});
 });

 it('normalizes structured memory route provenance without stringifying objects',async()=>{
  vi.stubGlobal('fetch',vi.fn().mockResolvedValue(response(200,{
   runId:'run-7',workflowId:'wf',instanceId:'b',status:'completed',inputContentRevision:2,
   objective:'test',constraints:[],deliverables:[],acceptanceChecks:[],
   memoryRoute:[{instanceId:'a',topicId:'ta',title:'数据集'},{instanceId:'b',topicId:'tb',title:'实验'}],
   availableTools:[{name:'safe_calculator',version:'1.0.0',description:'Arithmetic'}],
  })));
  const run=await api.agentRun('run-7');
  expect(run.memoryRoute).toEqual([
   {instanceId:'a',topicId:'ta',title:'数据集'},
   {instanceId:'b',topicId:'tb',title:'实验'},
  ]);
  expect(run.availableTools).toEqual([{name:'safe_calculator',version:'1.0.0',description:'Arithmetic'}]);
 });
});

describe('turn canvas API contract',()=>{
 it('loads route-scoped turns without requesting inherited messages separately',async()=>{
  const fetchMock=vi.fn().mockResolvedValue(response(200,{workflowId:'wf',instanceId:'b',contentRevision:4,eventRevision:8,memoryRoute:[{instanceId:'a',title:'数据集'},{instanceId:'b',title:'实验'}],inheritedMessageCount:3,turns:[{id:'turn-1',sequence:1,userMessage:{id:10,role:'user',content:'本地问题'},responses:[],status:'pending'}]}));
  vi.stubGlobal('fetch',fetchMock);
  const snapshot=await api.turns('wf','b');
  expect(snapshot.turns[0].userMessage.content).toBe('本地问题');
  expect(fetchMock).toHaveBeenCalledWith('/api/v1/workflows/wf/instances/b/turn-tree',expect.any(Object));
 });

 it('sends an anchored fork with revision and idempotency identity',async()=>{
  const fetchMock=vi.fn().mockResolvedValue(response(201,{node:{id:'child'},graphRevision:6}));
  vi.stubGlobal('fetch',fetchMock);
  await api.fork('wf','b',{title:'分支',anchorMessageId:10,expectedContentRevision:4,idempotencyKey:'idem-1'});
  const init=fetchMock.mock.calls[0][1] as RequestInit;
  expect(JSON.parse(String(init.body))).toEqual({title:'分支',anchorMessageId:10,expectedContentRevision:4,idempotencyKey:'idem-1'});
 });

 it('supports a one-click empty turn branch and a later rename',async()=>{
  const fetchMock=vi.fn().mockResolvedValueOnce(response(201,{node:{id:'child',title:'新分支 1'},graphRevision:6})).mockResolvedValueOnce(response(200,{node:{id:'child',title:'模型对比'},graphRevision:7,eventRevision:9}));
  vi.stubGlobal('fetch',fetchMock);
  await api.forkChat('wf','b',{anchorMessageId:10,expectedContentRevision:4,idempotencyKey:'quick-1'});
  await api.renameInstance('wf','child','模型对比',6);
  expect(fetchMock.mock.calls[0][0]).toBe('/api/v1/workflows/wf/instances/b/fork-chat');
  expect(JSON.parse(String((fetchMock.mock.calls[0][1] as RequestInit).body))).toEqual({anchorMessageId:10,expectedContentRevision:4,idempotencyKey:'quick-1'});
  expect(fetchMock.mock.calls[1][0]).toBe('/api/v1/workflows/wf/instances/child');
  expect(fetchMock.mock.calls[1][1]).toMatchObject({method:'PATCH'});
  expect(JSON.parse(String((fetchMock.mock.calls[1][1] as RequestInit).body))).toEqual({title:'模型对比',expectedRevision:6});
 });
});
