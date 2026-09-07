import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import './markdown-message.css';

export function MarkdownMessage({content,className=''}:{content:string;className?:string}){
 return <div className={`markdown-message ${className}`.trim()}><ReactMarkdown remarkPlugins={[remarkGfm]} components={{
  a:({children,node:_,...props})=><a {...props} target="_blank" rel="noreferrer noopener">{children}</a>,
  table:({children,node:_,...props})=><div className="markdown-table-scroll"><table {...props}>{children}</table></div>,
 }}>{content}</ReactMarkdown></div>
}
