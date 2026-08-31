import{ChatPage}from'./pages/ChatPage';import{GraphPage}from'./pages/GraphPage';export default function App(){return location.pathname==='/graph'?<GraphPage/>:<ChatPage/>}
