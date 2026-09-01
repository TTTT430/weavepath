import{WorkspaceCanvas}from'./WorkspaceCanvas';

export function GraphPage(){
 const workflowId=new URLSearchParams(location.search).get('workflow')||localStorage.getItem('cw.workflow')||'';
 return <WorkspaceCanvas workflowId={workflowId} visible onContinue={()=>window.close()} onClose={()=>window.close()}/>;
}
