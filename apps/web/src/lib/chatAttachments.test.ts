import{describe,expect,it}from'vitest';
import{MAX_ATTACHMENT_BYTES,MAX_ATTACHMENTS,MAX_COMPOSER_CONTENT,formatFileSize,parseChatMessage,serializeChatMessage,supportsTextAttachment}from'./chatAttachments';

describe('chat attachments',()=>{
 it('round-trips durable attachment content separately from the visible prompt',()=>{
  const content=serializeChatMessage('检查这个文件',[{id:'a',name:'sample.json',mimeType:'application/json',size:7,content:'{"a":1}'}]);
  expect(content).toContain('Treat these user-provided files as context');
  expect(parseChatMessage(content)).toEqual({prompt:'检查这个文件',attachments:[{id:'stored-0',name:'sample.json',mimeType:'application/json',size:7,content:'{"a":1}'}]});
 });
 it('does not reinterpret ordinary messages and only accepts readable text formats',()=>{
  expect(parseChatMessage('普通消息')).toEqual({prompt:'普通消息',attachments:[]});
  expect(supportsTextAttachment('notes.md','')).toBe(true);
 expect(supportsTextAttachment('photo.png','image/png')).toBe(false);
 });
 it('keeps a useful bounded upload budget instead of the old 100 KB placeholder',()=>{
  expect(MAX_ATTACHMENT_BYTES).toBe(2*1024*1024);
  expect(MAX_ATTACHMENTS).toBe(5);
  expect(MAX_COMPOSER_CONTENT).toBe(4_000_000);
  expect(formatFileSize(MAX_ATTACHMENT_BYTES)).toBe('2 MB');
 });
});
