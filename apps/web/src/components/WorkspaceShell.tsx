import{useCallback,useEffect,useState}from'react';
import type{Graph}from'../domain/types';
import{useI18n}from'../lib/i18n';
import{ChatPage}from'../pages/ChatPage';
import{WorkspaceCanvas}from'../pages/WorkspaceCanvas';

type WorkspaceView='chat'|'workflow';

function initialView():WorkspaceView{return localStorage.getItem('weavepath.workspace.view')==='workflow'?'workflow':'chat'}

export function WorkspaceShell(){
 const{t}=useI18n(),[view,setViewState]=useState<WorkspaceView>(initialView),[workflowId,setWorkflowId]=useState(localStorage.getItem('cw.workflow')||''),[graph,setGraph]=useState<Graph|null>(null);
 const setView=useCallback((next:WorkspaceView)=>{setViewState(next);localStorage.setItem('weavepath.workspace.view',next)},[]);
 const workspaceChanged=useCallback((context:{workflowId:string;graph:Graph|null})=>{setWorkflowId(context.workflowId);setGraph(context.graph)},[]);
 const openWorkflow=useCallback((id:string)=>{setWorkflowId(id);setView('workflow')},[setView]);
 useEffect(()=>{if(!workflowId&&view==='workflow')setView('chat')},[workflowId,view,setView]);
 return <main className="workspace-shell">
  <header className="workspace-topbar">
   <strong>{t('app')}</strong>
   <nav className="workspace-view-tabs" aria-label={t('workflowCanvas')}>
    <button type="button" className={view==='chat'?'current':''} aria-pressed={view==='chat'} onClick={()=>setView('chat')}>{t('chatView')}</button>
    <button type="button" className={view==='workflow'?'current':''} aria-pressed={view==='workflow'} disabled={!workflowId} onClick={()=>setView('workflow')}>{t('workflowView')}</button>
   </nav>
   <span title={graph?.name||''}>{graph?.name||''}</span>
  </header>
  <section className="workspace-stage">
   <div className={`workspace-surface chat-surface ${view==='chat'?'is-active':''}`} aria-hidden={view!=='chat'}>
    <ChatPage onOpenWorkflow={openWorkflow} onWorkspaceChange={workspaceChanged}/>
   </div>
   <div className={`workspace-surface workflow-surface ${view==='workflow'?'is-active':''}`} aria-hidden={view!=='workflow'}>
    <WorkspaceCanvas workflowId={workflowId} visible={view==='workflow'} onContinue={()=>setView('chat')}/>
   </div>
  </section>
 </main>;
}
