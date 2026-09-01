import{WorkspaceShell}from'./components/WorkspaceShell';import{GraphPage}from'./pages/GraphPage';export default function App(){return location.pathname==='/graph'?<GraphPage/>:<WorkspaceShell/>}
