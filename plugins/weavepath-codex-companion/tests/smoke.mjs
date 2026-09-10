import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {mkdtemp,readFile,rm} from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const temporary=await mkdtemp(path.join(os.tmpdir(),'weavepath-codex-companion-'));
const discovery=path.join(temporary,'host-bridge.json');
const child=spawn(process.execPath,[path.join(root,'server.mjs'),'--interaction-client-id','test-thread'],{env:{...process.env,WEAVEPATH_HOST_BRIDGE_DISCOVERY:discovery},stdio:['pipe','pipe','pipe']});
try{
 let configuration=null;
 for(let attempt=0;attempt<80;attempt+=1){try{configuration=JSON.parse(await readFile(discovery,'utf8'));break}catch{await new Promise(resolve=>setTimeout(resolve,25))}}
 assert.ok(configuration,'companion did not publish discovery information');
 assert.equal(configuration.hostKind,'codex');
 const unauthorized=await fetch(`${configuration.baseUrl}/v1/handshake`);
 assert.equal(unauthorized.status,401);
 const handshake=await fetch(`${configuration.baseUrl}/v1/handshake`,{headers:{Authorization:`Bearer ${configuration.token}`}});
 assert.equal(handshake.status,200);
 const body=await handshake.json();
 assert.equal(body.contractVersion,1);
 assert.equal(body.connected,Boolean(process.env.CODEX_APP_TOOLS_PIPE_PATH?.trim()));
 assert.equal(body.capabilities.canFork,body.connected);
 assert.equal(body.capabilities.canForkFromCheckpoint,false);
 child.stdin.write(`${JSON.stringify({jsonrpc:'2.0',id:1,method:'initialize',params:{protocolVersion:'2025-06-18'}})}\n`);
 child.stdin.write(`${JSON.stringify({jsonrpc:'2.0',id:2,method:'tools/call',params:{name:'weavepath_companion_status',arguments:{}}})}\n`);
 let output='';
 for await(const chunk of child.stdout){output+=chunk.toString();if(output.includes('"id":2'))break}
 const replies=output.trim().split(/\r?\n/).map(JSON.parse);
 assert.equal(replies.find(item=>item.id===2).result.structuredContent.connected,body.connected);
 console.log('Codex companion loopback/auth/MCP smoke test passed.');
}finally{
 child.stdin.end();
 await new Promise(resolve=>{const timer=setTimeout(()=>{child.kill();resolve()},1000);child.once('exit',()=>{clearTimeout(timer);resolve()})});
 await rm(temporary,{recursive:true,force:true});
}
