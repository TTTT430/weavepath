import{afterEach,describe,expect,it,vi}from'vitest';
import{api,ApiError,ATTACHMENT_CHUNK_SIZE,RESUMABLE_UPLOAD_THRESHOLD}from'./api';

const response=(status:number,body:unknown)=>({
 ok:status>=200&&status<300,
 status,
 json:vi.fn().mockResolvedValue(body),
})as unknown as Response;

afterEach(()=>vi.unstubAllGlobals());

describe('agent runtime API contract',()=>{
 it('uses dedicated non-secret endpoints for model discovery and quick switching',async()=>{
  const fetchMock=vi.fn()
   .mockResolvedValueOnce(response(200,{models:['model-a','model-b'],count:2}))
   .mockResolvedValueOnce(response(200,{configured:true,provider:'openai-compatible',model:'model-b'}));
  vi.stubGlobal('fetch',fetchMock);
  expect(await api.aiModels()).toEqual({models:['model-a','model-b'],count:2});
  expect((await api.switchAIModel('model-b')).model).toBe('model-b');
  expect(fetchMock.mock.calls[0][0]).toBe('/api/v1/ai/models');
  expect(fetchMock.mock.calls[1][0]).toBe('/api/v1/ai/settings/model');
  expect(fetchMock.mock.calls[1][1]).toMatchObject({method:'PATCH'});
 expect(JSON.parse(String((fetchMock.mock.calls[1][1] as RequestInit).body))).toEqual({model:'model-b',reasoningEffort:null});
 });

 it('uploads file bytes separately from chat JSON and can discard an unbound upload',async()=>{
  const metadata={attachmentId:'att-1',name:'large notes.md',mimeType:'text/markdown',size:8_000_000,sha256:'abc',status:'uploaded',parseStatus:'ready',parser:'utf8-text',parseErrorCode:null,parseError:null,extractedCharacters:7,chunkCount:1,contextCharacters:0,contextTruncated:false,contextSources:[]};
  const fetchMock=vi.fn()
   .mockResolvedValueOnce(response(201,metadata))
   .mockResolvedValueOnce(response(200,{ok:true,attachmentId:'att-1'}));
  vi.stubGlobal('fetch',fetchMock);
  const file=new File(['content'],'large notes.md',{type:'text/markdown'});
  expect(await api.uploadAttachment('wf','route/a',file)).toEqual(metadata);
  expect(fetchMock.mock.calls[0][0]).toBe('/api/v1/workflows/wf/instances/route%2Fa/attachments?name=large%20notes.md&mimeType=text%2Fmarkdown');
  expect(fetchMock.mock.calls[0][1]).toMatchObject({method:'POST',body:file,headers:{Accept:'application/json','Content-Type':'text/markdown'}});
  await api.deleteAttachment('wf','route/a','att/1');
 expect(fetchMock.mock.calls[1][0]).toBe('/api/v1/workflows/wf/instances/route%2Fa/attachments/att%2F1');
 });

 it('resumes a large upload and sends only chunks reported missing by the server',async()=>{
  const metadata={attachmentId:'att-large',name:'dataset.txt',mimeType:'text/plain',size:RESUMABLE_UPLOAD_THRESHOLD+10,sha256:'abc',status:'uploaded',parseStatus:'processing',parser:null,parseErrorCode:null,parseError:null,extractedCharacters:0,chunkCount:0,contextCharacters:0,contextTruncated:false,contextSources:[]};
  const session={uploadId:'upl-1',workflowId:'wf',instanceId:'route/a',name:'dataset.txt',mimeType:'text/plain',size:metadata.size,chunkSize:ATTACHMENT_CHUNK_SIZE,totalChunks:2,receivedChunks:[0],missingChunks:[1],status:'uploading',attachmentId:null};
  const fetchMock=vi.fn()
   .mockResolvedValueOnce(response(201,session))
   .mockResolvedValueOnce(response(200,{...session,receivedChunks:[0,1],missingChunks:[]}))
   .mockResolvedValueOnce(response(200,metadata));
  vi.stubGlobal('fetch',fetchMock);
  const chunk=new Blob(['tail']),slice=vi.fn(()=>chunk),file={name:'dataset.txt',type:'text/plain',size:metadata.size,lastModified:7,slice}as unknown as File,progress=vi.fn();
  expect(await api.uploadAttachment('wf','route/a',file,progress)).toEqual(metadata);
  expect(fetchMock.mock.calls.map(call=>call[0])).toEqual([
   '/api/v1/workflows/wf/instances/route%2Fa/attachment-uploads',
   '/api/v1/workflows/wf/instances/route%2Fa/attachment-uploads/upl-1/chunks/1',
   '/api/v1/workflows/wf/instances/route%2Fa/attachment-uploads/upl-1/complete',
  ]);
  expect(JSON.parse(String((fetchMock.mock.calls[0][1]as RequestInit).body))).toMatchObject({name:'dataset.txt',size:metadata.size,chunkSize:ATTACHMENT_CHUNK_SIZE,clientKey:expect.any(String)});
  expect(slice).toHaveBeenCalledWith(ATTACHMENT_CHUNK_SIZE,metadata.size);
  expect((fetchMock.mock.calls[1][1]as RequestInit).body).toBe(chunk);
  expect(progress.mock.calls.map(call=>call[0])).toEqual([
   {uploadedBytes:ATTACHMENT_CHUNK_SIZE,totalBytes:metadata.size,uploadedChunks:1,totalChunks:2,resumedChunks:1},
   {uploadedBytes:metadata.size,totalBytes:metadata.size,uploadedChunks:2,totalChunks:2,resumedChunks:1},
  ]);
 });

 it('lists route assets and uses explicit inspect and reparse endpoints',async()=>{
  const metadata={attachmentId:'att-1',name:'paper.pdf',mimeType:'application/pdf',size:100,sha256:'abc',status:'uploaded',parseStatus:'ready',parser:'pypdf',parseErrorCode:null,parseError:null,extractedCharacters:20,chunkCount:1,contextCharacters:0,contextTruncated:false,contextSources:[]};
  const fetchMock=vi.fn()
   .mockResolvedValueOnce(response(200,{attachments:[metadata]}))
   .mockResolvedValueOnce(response(200,{...metadata,chunks:[{ordinal:1,locator:'page 1',characters:20,preview:'content'}]}))
   .mockResolvedValueOnce(response(200,metadata));
  vi.stubGlobal('fetch',fetchMock);
  expect(await api.attachments('wf','route/a')).toEqual([metadata]);
  expect((await api.attachment('wf','route/a','att/1')).chunks?.[0].locator).toBe('page 1');
  expect((await api.reparseAttachment('wf','route/a','att/1')).parser).toBe('pypdf');
  expect(fetchMock.mock.calls.map(call=>call[0])).toEqual([
   '/api/v1/workflows/wf/instances/route%2Fa/attachments?scope=route',
   '/api/v1/workflows/wf/instances/route%2Fa/attachments/att%2F1',
   '/api/v1/workflows/wf/instances/route%2Fa/attachments/att%2F1/reparse',
  ]);
 });

 it('keeps the persisted run id on an error response',async()=>{
  vi.stubGlobal('fetch',vi.fn().mockResolvedValue(response(502,{code:'toolExecutionFailed',error:'failed',runId:'run-7'})));
  let caught:unknown;
  try{await api.createAgentRun('wf','node',{objective:'test',constraints:[],deliverables:[],acceptanceChecks:[],expectedContentRevision:2,idempotencyKey:'idem'})}
  catch(error){caught=error}
 expect(caught).toBeInstanceOf(ApiError);
 expect(caught).toMatchObject({status:502,code:'toolExecutionFailed',runId:'run-7'});
 });

 it('preserves safe model connection diagnostics on an error response',async()=>{
  const diagnostics={requestedMode:'auto',routeUsed:null,attempts:[
   {route:'direct',outcome:'connection-error',category:'dns',durationMs:21},
   {route:'system',outcome:'connection-error',category:'proxy',durationMs:34},
  ]};
  vi.stubGlobal('fetch',vi.fn().mockResolvedValue(response(503,{
   code:'modelDiscoveryConnectionFailed',error:'Unable to reach the model provider',diagnostics,
  })));
  let caught:unknown;
  try{await api.validateAISettings({baseUrl:'https://example.test/v1',model:'model',persistence:'memory',systemPrompt:'',networkMode:'auto'})}
  catch(error){caught=error}
  expect(caught).toBeInstanceOf(ApiError);
  expect(caught).toMatchObject({status:503,code:'modelDiscoveryConnectionFailed',diagnostics});
 });

 it('normalizes structured memory route provenance without stringifying objects',async()=>{
  vi.stubGlobal('fetch',vi.fn().mockResolvedValue(response(200,{
   runId:'run-7',workflowId:'wf',instanceId:'b',status:'completed',inputContentRevision:2,
   objective:'test',constraints:[],deliverables:[],acceptanceChecks:[],
   memoryRoute:[{instanceId:'a',topicId:'ta',title:'数据集'},{instanceId:'b',topicId:'tb',title:'实验'}],
   retrievalPlan:{planVersion:1,mode:'automatic',query:'sentiment',engine:'fts5-trigram',budgetCharacters:24000,candidateCount:2,selectedCharacters:80,selectedChunks:1,truncated:false,routeInstanceIds:['a','b'],contextSha256:'abc',sources:[{attachmentId:'att-1',name:'notes.txt',chunkOrdinal:1,locator:'lines 1-2'}]},
   availableTools:[{name:'safe_calculator',version:'1.0.0',description:'Arithmetic'}],
  })));
  const run=await api.agentRun('run-7');
  expect(run.memoryRoute).toEqual([
   {instanceId:'a',topicId:'ta',title:'数据集'},
   {instanceId:'b',topicId:'tb',title:'实验'},
 ]);
 expect(run.retrievalPlan).toMatchObject({mode:'automatic',selectedChunks:1,contextSha256:'abc'});
 expect(run.availableTools).toEqual([{name:'safe_calculator',version:'1.0.0',description:'Arithmetic'}]);
 });

 it('uses the durable cancel, retry and approval decision endpoints',async()=>{
  const body={runId:'run-7',workflowId:'wf',instanceId:'b',status:'running',inputContentRevision:2,objective:'test',constraints:[],deliverables:[],acceptanceChecks:[]};
  const fetchMock=vi.fn().mockResolvedValue(response(200,body));
  vi.stubGlobal('fetch',fetchMock);
  await api.cancelAgentRun('run-7');
  await api.retryAgentRun('run-7',{idempotencyKey:'retry-1',expectedContentRevision:3});
  await api.decideAgentApproval('run-7','approval/1','approved');
  expect(fetchMock.mock.calls[0][0]).toBe('/api/v1/runs/run-7/cancel');
  expect(fetchMock.mock.calls[0][1]).toMatchObject({method:'POST'});
  expect(fetchMock.mock.calls[1][0]).toBe('/api/v1/runs/run-7/retry');
  expect(JSON.parse(String((fetchMock.mock.calls[1][1] as RequestInit).body))).toEqual({idempotencyKey:'retry-1',expectedContentRevision:3});
  expect(fetchMock.mock.calls[2][0]).toBe('/api/v1/runs/run-7/approvals/approval%2F1/decision');
  expect(JSON.parse(String((fetchMock.mock.calls[2][1] as RequestInit).body))).toEqual({decision:'approved'});
 });

 it('normalizes legacy approval status and cache fields while preserving unavailable and unknown states',async()=>{
  const base={runId:'run-7',workflowId:'wf',instanceId:'b',inputContentRevision:2,objective:'test',constraints:[],deliverables:[],acceptanceChecks:[]};
  const fetchMock=vi.fn()
   .mockResolvedValueOnce(response(200,{...base,status:'waiting_approval',approvals:[{id:'approval-1',tool:{name:'apply_patch',version:'1'},toolArguments:{path:'a'},status:'pending'}],metrics:{durationMs:null,modelStepCount:1,toolCallCount:1,toolDurationMs:0,inputTokens:100,outputTokens:2,estimatedCost:null,cachedInputTokens:60,cacheMissTokens:40,cacheHitRate:.6,cacheStatus:'unavailable'}}))
   .mockResolvedValueOnce(response(200,{...base,status:'future_provider_state'}));
  vi.stubGlobal('fetch',fetchMock);
  const legacy=await api.agentRun('run-7');
  expect(legacy.status).toBe('awaiting_approval');
  expect(legacy.approvalRequests).toEqual([expect.objectContaining({approvalId:'approval-1',toolName:'apply_patch',toolVersion:'1',arguments:{path:'a'},status:'pending'})]);
  expect(legacy.metrics).toMatchObject({cachedInputTokens:60,uncachedInputTokens:40,cacheReuseRatio:.6,cacheStatus:'not_reported'});
  expect((await api.agentRun('run-8')).status).toBe('unknown');
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
