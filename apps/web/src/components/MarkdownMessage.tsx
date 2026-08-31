import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import './markdown-message.css';

export function MarkdownMessage({content,className=''}:{content:string;className?:string}){
 return <div className={`markdown-message ${className}`.trim()}><ReactMarkdown remarkPlugins={[remarkGfm]} components={{a:({children,node:_,...props})=><a {...props} target="_blank" rel="noreferrer noopener">{children}</a>}}>{content}</ReactMarkdown></div>
}
