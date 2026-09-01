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
