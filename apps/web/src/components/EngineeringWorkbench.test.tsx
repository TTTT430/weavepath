import{cleanup,fireEvent,render,screen,waitFor}from'@testing-library/react';
import{afterEach,beforeEach,describe,expect,it,vi}from'vitest';
import{I18nProvider}from'../lib/i18n';
import{EngineeringWorkbench}from'./EngineeringWorkbench';

const apiMock=vi.hoisted(()=>({artifacts:vi.fn(),datasets:vi.fn(),experiments:vi.fn(),compareBranches:vi.fn(),mergeKnowledge:vi.fn(),artifact:vi.fn(),createArtifact:vi.fn(),createDataset:vi.fn(),agentRuns:vi.fn(),createExperiment:vi.fn()}));
vi.mock('../lib/api',()=>({api:apiMock}));
const graph={workflowId:'wf',name:'Research',rootInstanceId:'a',activeInstanceId:'c',graphRevision:3,eventRevision:3,nodes:[{id:'a',parentId:null,topicId:'ta',title:'数据集',summary:'讨论数据来源和标签设计。',status:'active' as const},{id:'c',parentId:'a',topicId:'tc',title:'情感分析',summary:'比较大模型情感分类方案。',status:'active' as const},{id:'e',parentId:'a',topicId:'te',title:'规则分析',summary:'设计词典和规则基线。',status:'active' as const}]};
const comparison={workflowId:'wf',instanceIds:['c','e'],sharedRoute:[{instanceId:'a',title:'数据集'}],transcriptsIncluded:false as const,branches:[{instanceId:'c',topicId:'tc',title:'情感分析',status:'active' as const,memoryRoute:[{instanceId:'a',title:'数据集'},{instanceId:'c',title:'情感分析'}],localMessageCounts:{user:1,assistant:1},latestRun:{runId:'r1',status:'completed' as const,objective:'模型情感实验',modelSnapshot:{provider:'openai-compatible',model:'gpt-test'},finalAnswer:'结论 C',errorCode:null,createdAt:'now'},artifacts:[]},{instanceId:'e',topicId:'te',title:'规则分析',status:'active' as const,memoryRoute:[{instanceId:'a',title:'数据集'},{instanceId:'e',title:'规则分析'}],localMessageCounts:{user:1},latestRun:null,artifacts:[]}]};

function view(){localStorage.setItem('cw.locale','zh-CN');return render(<I18nProvider><EngineeringWorkbench workflowId="wf" graph={graph}/></I18nProvider>)}

beforeEach(()=>{apiMock.artifacts.mockResolvedValue([]);apiMock.datasets.mockResolvedValue([]);apiMock.experiments.mockResolvedValue([]);apiMock.compareBranches.mockResolvedValue(comparison);apiMock.mergeKnowledge.mockResolvedValue({mergeId:'m1',transcriptsMerged:false});apiMock.createArtifact.mockResolvedValue({});apiMock.createDataset.mockResolvedValue({datasetId:'d1'});apiMock.agentRuns.mockResolvedValue([]);apiMock.createExperiment.mockResolvedValue({})});
afterEach(()=>{cleanup();vi.clearAllMocks()});

describe('EngineeringWorkbench',()=>{
 it('compares branches and merges only the explicitly checked conclusion',async()=>{
  view();await waitFor(()=>expect(apiMock.artifacts).toHaveBeenCalledWith('wf'));
  fireEvent.click(screen.getByLabelText('情感分析'));fireEvent.click(screen.getByLabelText('规则分析'));
  fireEvent.click(screen.getByRole('button',{name:'开始对比'}));
  expect(await screen.findByText('结论 C')).toBeInTheDocument();
  expect(screen.getByText(/兄弟分支的完整对话仍保持隔离/)).toBeInTheDocument();
  const conclusion=screen.getByLabelText('采用这条结论');
  expect(conclusion.closest('label')).toHaveClass('conclusion-choice');
  expect(conclusion.nextElementSibling).toHaveTextContent('采用这条结论');
  fireEvent.click(conclusion);
  expect(conclusion.closest('label')).toHaveClass('is-selected');
  fireEvent.click(screen.getByRole('button',{name:'加入所选知识'}));
  await waitFor(()=>expect(apiMock.mergeKnowledge).toHaveBeenCalledWith('wf',expect.objectContaining({targetInstanceId:'c',sourceInstanceIds:['c'],artifactIds:[],items:[expect.objectContaining({sourceInstanceId:'c',sourceRunId:'r1',content:'结论 C'})]})));
  expect(await screen.findByText(/所选知识已加入目标路线/)).toBeInTheDocument();
 });

 it('creates versioned artifacts from the native lab form',async()=>{
  view();fireEvent.click(screen.getByRole('button',{name:'成果库'}));
  fireEvent.change(screen.getByLabelText('名称'),{target:{value:'评估报告'}});
  fireEvent.change(screen.getByLabelText('内容'),{target:{value:'# result'}});
  fireEvent.click(screen.getByRole('button',{name:'保存新版本'}));
  await waitFor(()=>expect(apiMock.createArtifact).toHaveBeenCalledWith('wf',expect.objectContaining({name:'评估报告',content:'# result',kind:'report'})));
 });

 it('compares conversation summaries and does not expose the old small-dataset experiment form',async()=>{
  view();
  fireEvent.click(screen.getByLabelText('情感分析'));fireEvent.click(screen.getByLabelText('规则分析'));
  fireEvent.click(screen.getByRole('button',{name:'开始对比'}));
  expect(await screen.findByText('比较大模型情感分类方案。')).toBeInTheDocument();
  expect(screen.getByText('设计词典和规则基线。')).toBeInTheDocument();
  expect(screen.getAllByText('分支独有路线')).toHaveLength(2);
  expect(screen.queryByRole('button',{name:/^实验$/})).not.toBeInTheDocument();
  expect(apiMock.datasets).not.toHaveBeenCalled();
  expect(apiMock.experiments).not.toHaveBeenCalled();
 });
});
