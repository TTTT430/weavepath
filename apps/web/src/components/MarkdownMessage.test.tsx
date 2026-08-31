import{cleanup,render,screen}from'@testing-library/react';
import{afterEach,describe,expect,it}from'vitest';
import{MarkdownMessage}from'./MarkdownMessage';

afterEach(cleanup);
describe('MarkdownMessage',()=>{
 it('renders core Markdown and GFM structures',()=>{const{container}=render(<MarkdownMessage content={'# Heading\n\n**bold**\n\n- one\n- two\n\n```ts\nconst x = 1\n```\n\n[OpenAI](https://openai.com)\n\n| A | B |\n|---|---|\n| 1 | 2 |'}/>);expect(screen.getByRole('heading',{name:'Heading'})).toBeInTheDocument();expect(screen.getByText('bold').tagName).toBe('STRONG');expect(screen.getAllByRole('listitem')).toHaveLength(2);expect(container.querySelector('pre code')).toHaveTextContent('const x = 1');expect(screen.getByRole('link',{name:'OpenAI'})).toHaveAttribute('rel','noreferrer noopener');expect(container.querySelectorAll('table td')).toHaveLength(2)});
 it('does not execute or mount raw HTML',()=>{const{container}=render(<MarkdownMessage content={'<script>window.__unsafe = true</script>\n\n<img src=x onerror="window.__unsafe=true">'}/>);expect(container.querySelector('script')).toBeNull();expect(container.querySelector('img')).toBeNull();expect(container).toHaveTextContent('<script>')});
 it('blocks unsafe javascript link protocols',()=>{render(<MarkdownMessage content={'[unsafe](javascript:alert(1))'}/>);expect(screen.getByText('unsafe').closest('a')).toHaveAttribute('href','')});
});
