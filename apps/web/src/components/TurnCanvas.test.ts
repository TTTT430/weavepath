import{describe,expect,it}from'vitest';
import type{TurnCanvasSnapshot}from'../domain/types';
import{canvasTurns}from'./TurnCanvas';

describe('Turn Canvas route projection',()=>{
 it('keeps an empty internal route visible beside the anchored parent turn',()=>{
  const snapshot:TurnCanvasSnapshot={workflowId:'wf',instanceId:'owner',scope:'local',contentRevision:1,eventRevision:2,memoryRoute:[{instanceId:'owner',title:'数据集'}],inheritedMessageCount:0,checkpointAnchor:null,preamble:[],eventExtensions:[],turns:[{id:'turn-1',sequence:1,anchorMessageId:11,routeInstanceId:'owner',routeTitle:'数据集',parentTurnId:null,userMessage:{id:11,role:'user',content:'原问题'},responses:[],status:'completed'}],routeNodes:[{routeInstanceId:'owner',title:'数据集',parentRouteInstanceId:null,anchorMessageId:null,checkpointAnchor:null,contentRevision:1,memoryRoute:[{instanceId:'owner',title:'数据集'}],inheritedMessageCount:0},{routeInstanceId:'branch',title:'新分支 1',parentRouteInstanceId:'owner',anchorMessageId:11,checkpointAnchor:{anchorMessageId:11},contentRevision:0,memoryRoute:[{instanceId:'owner',title:'数据集'},{instanceId:'branch',title:'新分支 1'}],inheritedMessageCount:1}]};
  const turns=canvasTurns(snapshot,'选中后开始对话');
  expect(turns).toHaveLength(2);expect(turns[1]).toMatchObject({id:'route:branch',routeInstanceId:'branch',routeTitle:'新分支 1',parentTurnId:'turn-1',isRoutePlaceholder:true});expect(turns[1].userMessage.content).toBe('选中后开始对话');
 });
});
