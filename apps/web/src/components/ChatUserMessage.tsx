import{formatFileSize,parseChatMessage}from'../lib/chatAttachments';
import{AppIcon}from'./AppIcon';

export function ChatUserMessage({content}:{content:string}){
 const parsed=parseChatMessage(content);
 return <div className="chat-user-content">
  {!!parsed.attachments.length&&<div className="message-attachment-list">{parsed.attachments.map(file=><span className="message-attachment" key={`${file.id}-${file.name}`} title={file.name}><AppIcon name="attachment" size={14}/><span>{file.name}</span><small>{formatFileSize(file.size)}</small></span>)}</div>}
  {parsed.prompt&&<span className="chat-user-prompt">{parsed.prompt}</span>}
 </div>;
}
