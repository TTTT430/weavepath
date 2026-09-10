#!/usr/bin/env node
import {createServer} from 'node:http';
import {randomBytes,randomUUID,timingSafeEqual} from 'node:crypto';
import {mkdir,readFile,rename,unlink,writeFile} from 'node:fs/promises';
import net from 'node:net';
import os from 'node:os';
import path from 'node:path';
import process from 'node:process';

const CONTRACT_VERSION=1,HOST_KIND='codex',MAX_BODY=1024*1024,MAX_FRAME=8*1024*1024;
const interactionClientId=argument('--interaction-client-id');
const token=randomBytes(32).toString('hex');
const discoveryPath=process.env.WEAVEPATH_HOST_BRIDGE_DISCOVERY||path.join(process.env.LOCALAPPDATA||path.join(os.homedir(),'.local','share'),'WeavePath','host-bridge.json');
const nativePipeAvailable=Boolean(process.env.CODEX_APP_TOOLS_PIPE_PATH?.trim());
const capabilities={canFork:nativePipeAvailable,canForkFromCheckpoint:false,canNavigate:nativePipeAvailable,canReadTranscript:nativePipeAvailable,canReadLocalTurns:nativePipeAvailable,canArchive:nativePipeAvailable,canRename:nativePipeAvailable,canOpenExternalWindow:nativePipeAvailable,supportedCheckpointCursorKinds:nativePipeAvailable?['instanceHead']:[]};
const tool={name:'weavepath_companion_status',description:'Return the local WeavePath Codex companion status and negotiated host capabilities.',inputSchema:{type:'object',properties:{},additionalProperties:false},annotations:{readOnlyHint:true,openWorldHint:false}};
let nativeClient=null,baseUrl=null;

function argument(name){const index=process.argv.indexOf(name);return index>=0&&process.argv[index+1]?process.argv[index+1]:null}
function isObject(value){return value!==null&&typeof value==='object'&&!Array.isArray(value)}
function secureEqual(left,right){const a=Buffer.from(left),b=Buffer.from(right);return a.length===b.length&&timingSafeEqual(a,b)}
function envelope(result){return{contractVersion:CONTRACT_VERSION,hostKind:HOST_KIND,result}}
function json(res,status,value){const body=JSON.stringify(value);res.writeHead(status,{'Content-Type':'application/json; charset=utf-8','Content-Length':Buffer.byteLength(body),'Cache-Control':'no-store'});res.end(body)}
function binding(value){if(!isObject(value)||typeof value.workflowId!=='string'||typeof value.instanceId!=='string'||typeof value.threadId!=='string')throw new Error('Host binding is incomplete.');return value}
function findStringDeep(value,keys,depth=0){if(depth>6||value==null)return null;if(typeof value==='string')return null;if(Array.isArray(value)){for(const item of value){const found=findStringDeep(item,keys,depth+1);if(found)return found}return null}if(isObject(value)){for(const key of keys){if(typeof value[key]==='string'&&value[key].trim())return value[key].trim()}for(const child of Object.values(value)){const found=findStringDeep(child,keys,depth+1);if(found)return found}}return null}
function nextCursorOf(value,depth=0){if(depth>6||value==null)return null;if(isObject(value)){for(const key of ['nextCursor','next_cursor'])if(typeof value[key]==='string'&&value[key].trim())return value[key];for(const child of Object.values(value)){const found=nextCursorOf(child,depth+1);if(found)return found}}return null}
function conversationItem(value){if(!isObject(value))return null;const threadId=findStringDeep(value,['threadId','thread_id','id']);if(!threadId)return null;const title=typeof value.title==='string'&&value.title.trim()?value.title.trim():threadId;const item={threadId,providerConversationId:threadId,title};for(const key of ['updatedAt','status'])if(typeof value[key]==='string')item[key]=value[key];item.metadata={};for(const key of ['hostId','projectId','backingKind'])if(typeof value[key]==='string')item.metadata[key]=value[key];return item}

function encodeFrame(message){const payload=Buffer.from(message,'utf8');if(payload.length>MAX_FRAME)throw new Error('Native request is too large.');const frame=Buffer.alloc(4+payload.length);frame.writeUInt32LE(payload.length,0);payload.copy(frame,4);return frame}
function parseNative(response){if(!isObject(response)||typeof response.success!=='boolean'||!Array.isArray(response.contentItems))throw new Error('Codex app tools returned an invalid response.');const texts=response.contentItems.filter(item=>item?.type==='inputText'&&typeof item.text==='string').map(item=>item.text);if(!response.success)throw new Error(texts.find(Boolean)||'Codex app tool failed.');for(const text of texts){try{return{value:JSON.parse(text),text}}catch{}}return{value:null,text:texts.join('\n')}}
class NativePipeClient{
 constructor(pipePath){this.pipePath=pipePath;this.socket=null;this.connecting=null;this.pending=new Map();this.buffer=Buffer.alloc(0);this.nextId=1;this.tools=null}
 async connect(){if(this.socket&&!this.socket.destroyed)return;if(this.connecting)return this.connecting;this.connecting=new Promise((resolve,reject)=>{const socket=net.createConnection(this.pipePath);const fail=error=>{socket.destroy();reject(error)};socket.once('error',fail);socket.once('connect',()=>{socket.off('error',fail);this.socket=socket;this.connecting=null;socket.on('data',chunk=>this.onData(socket,chunk));socket.on('close',()=>this.disconnect(socket,new Error('Codex app tools pipe closed.')));socket.on('error',error=>this.disconnect(socket,error));resolve()})}).catch(error=>{this.connecting=null;throw error});return this.connecting}
 async request(method,params){await this.connect();const id=this.nextId++;const pending=new Promise((resolve,reject)=>{const timer=setTimeout(()=>{this.pending.delete(id);reject(new Error(`Codex app tool request timed out: ${method}`))},30000);this.pending.set(id,{resolve:value=>{clearTimeout(timer);resolve(value)},reject:error=>{clearTimeout(timer);reject(error)}})});this.socket.write(encodeFrame(JSON.stringify({jsonrpc:'2.0',id,method,params})));return pending}
 onData(socket,chunk){if(this.socket!==socket)return;this.buffer=Buffer.concat([this.buffer,chunk]);while(this.buffer.length>=4){const length=this.buffer.readUInt32LE(0);if(length>MAX_FRAME){socket.destroy();return}if(this.buffer.length<length+4)return;const payload=this.buffer.subarray(4,length+4);this.buffer=this.buffer.subarray(length+4);let message;try{message=JSON.parse(payload.toString('utf8'))}catch{socket.destroy();return}const pending=this.pending.get(Number(message.id));if(!pending)continue;this.pending.delete(Number(message.id));message.error?pending.reject(new Error(message.error.message||'Codex app tool failed.')):pending.resolve(message.result)}}
 disconnect(socket,error){if(this.socket!==socket)return;this.socket=null;this.buffer=Buffer.alloc(0);this.tools=null;for(const pending of this.pending.values())pending.reject(error);this.pending.clear()}
 async resolveTool(name){if(!this.tools){const result=await this.request('tools/list',{threadStartKind:interactionClientId?'default':'all'});if(!isObject(result)||!Array.isArray(result.tools))throw new Error('Invalid Codex app tool catalog.');this.tools=result.tools}const matches=this.tools.filter(item=>item?.name===name||item?.name===`codex_app__${name}`||(item?.namespace==='codex_app'&&String(item.name).endsWith(`__${name}`)));if(matches.length!==1)throw new Error(`Codex app tool is unavailable: ${name}.`);return matches[0]}
 async call(name,args){const tool=await this.resolveTool(name);const response=await this.request('tools/call',{arguments:args,callId:`weavepath-${randomUUID()}`,namespace:tool.namespace,threadId:interactionClientId||args.threadId,tool:tool.name,turnId:`weavepath-${randomUUID()}`});return parseNative(response)}
}
function native(){const pipe=process.env.CODEX_APP_TOOLS_PIPE_PATH?.trim();if(!pipe)throw new Error('CODEX_APP_TOOLS_PIPE_PATH is unavailable.');nativeClient??=new NativePipeClient(pipe);return nativeClient}

async function execute(operation,payload,operationId){
 if(!isObject(payload))throw new Error('Operation payload must be an object.');
 if(operation==='resolveCurrentContext'){const context=isObject(payload.requestContext)?payload.requestContext:{};return{workflowId:context.workflowId||null,instanceId:context.instanceId||null,memoryRoute:Array.isArray(context.memoryRoute)?context.memoryRoute:[],metadata:{codexThreadId:interactionClientId}}}
 if(operation==='listConversations'){const result=await native().call('list_threads',{limit:50});const source=result.value||{};const raw=[...(Array.isArray(source.pinnedThreads)?source.pinnedThreads:[]),...(Array.isArray(source.threads)?source.threads:[])];const seen=new Set(),items=[];for(const value of raw){const item=conversationItem(value);if(item&&!seen.has(item.threadId)){seen.add(item.threadId);items.push(item)}}return{items,nextCursor:nextCursorOf(source)}}
 const source=operation==='fork'?binding(payload.source):binding(payload.binding);
 if(operation==='fork'){
  if(payload.checkpoint?.kind&&payload.checkpoint.kind!=='instanceHead')throw new Error('Codex companion can only fork the current task head.');
  const result=await native().call('fork_thread',{threadId:source.threadId,environment:{type:'same-directory'}});
  const threadId=findStringDeep(result.value,['threadId','thread_id'])||result.text.match(/\b[0-9a-f]{8}-[0-9a-f-]{20,}\b/i)?.[0];
  if(!threadId)throw new Error('Codex did not return a ready child task ID.');
  const target=payload.options?.targetInstanceId;if(typeof target!=='string'||!target)throw new Error('targetInstanceId is required.');
  if(typeof payload.options?.title==='string'&&payload.options.title.trim())await native().call('set_thread_title',{threadId,title:payload.options.title.trim()});
  if(typeof payload.prompt==='string'&&payload.prompt.trim())await native().call('send_message_to_thread',{threadId,prompt:payload.prompt.trim()});
  return{binding:{workflowId:source.workflowId,instanceId:target,threadId,provider:HOST_KIND,providerConversationId:threadId,metadata:{operationId}}};
 }
 if(operation==='navigate'){await native().call('navigate_to_codex_page',{threadId:source.threadId});return{ok:true,data:{activated:true}}}
 if(operation==='inspect'){const args={threadId:source.threadId,turnLimit:Math.min(10,Math.max(1,Number(payload.limit)||10)),includeOutputs:false};if(typeof payload.cursor==='string')args.cursor=payload.cursor;const result=await native().call('read_thread',args);const record=result.value||{text:result.text};return{items:[record],nextCursor:nextCursorOf(record)}}
 if(operation==='archive'){await native().call('set_thread_archived',{threadId:source.threadId,archived:true});return{ok:true,data:{archived:true}}}
 if(operation==='rename'){if(typeof payload.title!=='string'||!payload.title.trim())throw new Error('title is required.');await native().call('set_thread_title',{threadId:source.threadId,title:payload.title.trim()});return{ok:true,data:{title:payload.title.trim()}}}
 throw new Error(`Unknown operation: ${operation}`);
}

async function readBody(req){let size=0,chunks=[];for await(const chunk of req){size+=chunk.length;if(size>MAX_BODY)throw new Error('Request body is too large.');chunks.push(chunk)}return JSON.parse(Buffer.concat(chunks).toString('utf8')||'{}')}
const server=createServer(async(req,res)=>{try{if(req.headers.authorization==null||!secureEqual(req.headers.authorization,`Bearer ${token}`))return json(res,401,{error:{code:'hostUnauthorized',message:'Invalid companion token.'}});if(req.method==='GET'&&req.url==='/v1/handshake')return json(res,200,{contractVersion:CONTRACT_VERSION,hostKind:HOST_KIND,connected:nativePipeAvailable,capabilities,limitations:[...(!nativePipeAvailable?['Codex app tools pipe is unavailable.']:[]),'Historical-message checkpoint forks are not exposed by the current Codex task API.']});const match=req.method==='POST'?req.url?.match(/^\/v1\/operations\/([A-Za-z]+)$/):null;if(!match)return json(res,404,{error:{code:'notFound',message:'Operation route was not found.'}});const body=await readBody(req);if(body.contractVersion!==CONTRACT_VERSION)return json(res,409,{error:{code:'hostContractMismatch',message:'Unsupported contract version.'}});const result=await execute(match[1],body.payload,body.operationId);return json(res,200,envelope(result))}catch(error){return json(res,502,{contractVersion:CONTRACT_VERSION,hostKind:HOST_KIND,error:{code:'hostOperationFailed',message:error instanceof Error?error.message:String(error)}})}});

async function writeDiscovery(){await mkdir(path.dirname(discoveryPath),{recursive:true});const temporary=`${discoveryPath}.${process.pid}.tmp`;await writeFile(temporary,JSON.stringify({contractVersion:CONTRACT_VERSION,hostKind:HOST_KIND,baseUrl,token,pid:process.pid,updatedAt:new Date().toISOString()},null,2),{encoding:'utf8',mode:0o600});await rename(temporary,discoveryPath)}
async function cleanup(){try{const current=JSON.parse(await readFile(discoveryPath,'utf8'));if(current.pid===process.pid)await unlink(discoveryPath)}catch{}}
await new Promise((resolve,reject)=>{server.once('error',reject);server.listen(0,'127.0.0.1',()=>{const address=server.address();baseUrl=`http://127.0.0.1:${address.port}`;resolve()})});
await writeDiscovery();
for(const signal of ['SIGINT','SIGTERM'])process.on(signal,async()=>{await cleanup();server.close(()=>process.exit(0))});

function rpcReply(value){return{content:[{type:'text',text:JSON.stringify(value)}],structuredContent:value}}
async function handle(message){if(message.method==='initialize')return{protocolVersion:message.params?.protocolVersion||'2025-06-18',capabilities:{tools:{}},serverInfo:{name:'weavepath-codex-companion',version:'0.1.0'}};if(message.method==='tools/list')return{tools:[tool]};if(message.method==='tools/call'){if(message.params?.name!==tool.name)throw Object.assign(new Error('Method not found'),{rpcCode:-32601});return rpcReply({ok:true,connected:nativePipeAvailable,hostKind:HOST_KIND,baseUrl,capabilities})}if(message.method==='notifications/initialized')return null;throw Object.assign(new Error(`Method not found: ${message.method}`),{rpcCode:-32601})}
let stdin='';process.stdin.setEncoding('utf8');process.stdin.on('data',chunk=>{stdin+=chunk;let index;while((index=stdin.indexOf('\n'))>=0){const line=stdin.slice(0,index).trim();stdin=stdin.slice(index+1);if(!line)continue;void(async()=>{let message;try{message=JSON.parse(line);const result=await handle(message);if(message.id!==undefined&&result!==null)process.stdout.write(`${JSON.stringify({jsonrpc:'2.0',id:message.id,result})}\n`)}catch(error){if(message?.id!==undefined)process.stdout.write(`${JSON.stringify({jsonrpc:'2.0',id:message.id,error:{code:error.rpcCode||-32603,message:error.message||String(error)}})}\n`)}})()}});
process.stdin.on('end',async()=>{await cleanup();server.close()});
