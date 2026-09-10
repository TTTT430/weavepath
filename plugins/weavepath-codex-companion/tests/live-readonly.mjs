import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {mkdtemp,readFile,rm} from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const pipe=process.env.CODEX_APP_TOOLS_PIPE_PATH?.trim();
const threadId=process.env.CODEX_THREAD_ID?.trim();
assert.ok(pipe,'CODEX_APP_TOOLS_PIPE_PATH is required for a live Codex check.');
assert.ok(threadId,'CODEX_THREAD_ID is required for a live Codex check.');

const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const temporary=await mkdtemp(path.join(os.tmpdir(),'weavepath-codex-live-'));
const discovery=path.join(temporary,'host-bridge.json');
const child=spawn(
 process.execPath,
 [path.join(root,'server.mjs'),'--interaction-client-id',threadId],
 {env:{...process.env,WEAVEPATH_HOST_BRIDGE_DISCOVERY:discovery},stdio:['pipe','pipe','pipe']},
);

async function operation(configuration,name,payload,operationId){
 const response=await fetch(`${configuration.baseUrl}/v1/operations/${name}`,{
  method:'POST',
  headers:{Authorization:`Bearer ${configuration.token}`,'Content-Type':'application/json'},
  body:JSON.stringify({contractVersion:1,operationId,payload}),
 });
 const value=await response.json();
 assert.equal(response.status,200,JSON.stringify(value));
 assert.equal(value.contractVersion,1);
 assert.equal(value.hostKind,'codex');
 return value.result;
}

try{
 let configuration=null;
 for(let attempt=0;attempt<100;attempt+=1){
  try{configuration=JSON.parse(await readFile(discovery,'utf8'));break}
  catch{await new Promise(resolve=>setTimeout(resolve,25))}
 }
 assert.ok(configuration,'Codex companion did not publish discovery information.');
 const handshake=await fetch(`${configuration.baseUrl}/v1/handshake`,{
  headers:{Authorization:`Bearer ${configuration.token}`},
 });
 const descriptor=await handshake.json();
 assert.equal(handshake.status,200);
 assert.equal(descriptor.connected,true);
 assert.equal(descriptor.capabilities.canReadTranscript,true);

 const listed=await operation(configuration,'listConversations',{},'live-list');
 assert.ok(Array.isArray(listed.items));
 const inspected=await operation(configuration,'inspect',{
  binding:{
   workflowId:'weavepath-live-check',instanceId:'current',threadId,
   provider:'codex',providerConversationId:threadId,
  },
  limit:1,
 },'live-inspect');
 assert.ok(Array.isArray(inspected.items));
 console.log(`Codex live read-only bridge check passed (${listed.items.length} visible tasks).`);
}finally{
 child.stdin.end();
 await new Promise(resolve=>{
  const timer=setTimeout(()=>{child.kill();resolve()},1500);
  child.once('exit',()=>{clearTimeout(timer);resolve()});
 });
 await rm(temporary,{recursive:true,force:true});
}
