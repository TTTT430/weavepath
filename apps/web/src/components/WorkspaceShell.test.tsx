import{cleanup,fireEvent,render,screen}from'@testing-library/react';
import{afterEach,beforeEach,describe,expect,it,vi}from'vitest';
import{I18nProvider}from'../lib/i18n';
import{WorkspaceShell}from'./WorkspaceShell';

vi.mock('../pages/ChatPage',async()=>{
 const{useEffect,useState}=await import('react');
 const graph={workflowId:'wf',name:'研究工作流',rootInstanceId:'a',activeInstanceId:'a',graphRevision:1,eventRevision:1,nodes:[{id:'a',parentId:null,topicId:'ta',title:'数据集',status:'active' as const}]};
 return{ChatPage:({onWorkspaceChange}:{onWorkspaceChange?:(value:{workflowId:string;graph:typeof graph})=>void})=>{const[value,setValue]=useState('');useEffect(()=>onWorkspaceChange?.({workflowId:'wf',graph}),[onWorkspaceChange]);return <label>chat-state<input aria-label="chat-state" value={value} onChange={event=>setValue(event.target.value)}/></label>}};
});

vi.mock('../pages/WorkspaceCanvas',async()=>{
 const{useState}=await import('react');
 return{WorkspaceCanvas:({workflowId,onContinue}:{workflowId:string;onContinue?:()=>void})=>{const[value,setValue]=useState('');return <div><span>canvas-{workflowId}</span><input aria-label="canvas-state" value={value} onChange={event=>setValue(event.target.value)}/><button onClick={onContinue}>continue-test</button></div>}};
});

beforeEach(()=>{localStorage.clear();localStorage.setItem('cw.locale','zh-CN');localStorage.setItem('cw.workflow','wf')});
afterEach(()=>cleanup());

describe('workspace shell',()=>{
 it('keeps chat and canvas mounted while switching the native workspace surface',()=>{
  render(<I18nProvider><WorkspaceShell/></I18nProvider>);
  const chat=screen.getByLabelText('chat-state'),canvas=screen.getByLabelText('canvas-state');
  fireEvent.change(chat,{target:{value:'保留的草稿'}});
  fireEvent.click(screen.getByRole('button',{name:'工作流'}));
  fireEvent.change(canvas,{target:{value:'保留的画布状态'}});
  expect(chat).toHaveValue('保留的草稿');
  fireEvent.click(screen.getByRole('button',{name:'对话'}));
  expect(canvas).toHaveValue('保留的画布状态');
  expect(chat).toHaveValue('保留的草稿');
 });

 it('returns to chat only through the explicit continue action',()=>{
  render(<I18nProvider><WorkspaceShell/></I18nProvider>);
  fireEvent.click(screen.getByRole('button',{name:'工作流'}));
  expect(screen.getByRole('button',{name:'工作流'})).toHaveAttribute('aria-pressed','true');
  fireEvent.click(screen.getByRole('button',{name:'continue-test'}));
  expect(screen.getByRole('button',{name:'对话'})).toHaveAttribute('aria-pressed','true');
 });
});
