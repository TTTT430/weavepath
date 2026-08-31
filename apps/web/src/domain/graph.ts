import type { Graph,Instance } from './types';
export function instanceMap(graph:Graph){return new Map(graph.nodes.map(n=>[n.id,n]));}
export function memoryPath(graph:Graph,id:string){const map=instanceMap(graph), path:Instance[]=[]; const seen=new Set<string>(); let node=map.get(id); while(node&&!seen.has(node.id)){seen.add(node.id);path.unshift(node);node=node.parentId?map.get(node.parentId):undefined} return path;}
export function routeLabel(graph:Graph,id:string){return memoryPath(graph,id).map(n=>n.title).join(' → ')}
export function graphEdges(graph:Graph){return graph.nodes.flatMap(n=>n.parentId?[{id:`${n.parentId}-${n.id}`,source:n.parentId,target:n.id}]:[])}
