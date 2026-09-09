import{cleanup,fireEvent,render,screen,waitFor}from'@testing-library/react';
import{afterEach,beforeEach,describe,expect,it,vi}from'vitest';
import{I18nProvider}from'../lib/i18n';
import{AttachmentManager}from'./AttachmentManager';

const apiMock=vi.hoisted(()=>(
 {attachments:vi.fn(),attachment:vi.fn(),reparseAttachment:vi.fn(),searchAttachments:vi.fn()}
));

vi.mock('../lib/api',()=>({api:apiMock}));

const inherited={
 attachmentId:'att-pdf',name:'research.pdf',mimeType:'application/pdf',size:2_400_000,
 sha256:'abc',status:'bound' as const,parseStatus:'ready' as const,parser:'pypdf',
 parseErrorCode:null,parseError:null,extractedCharacters:12_400,chunkCount:4,
 contextCharacters:6_000,contextTruncated:true,contextSources:[],messageId:10,
 routeInstanceId:'parent',routeTitle:'数据集',inherited:true,createdAt:'2026-09-09T00:00:00Z',boundAt:'2026-09-09T00:01:00Z',
};

beforeEach(()=>{localStorage.setItem('cw.locale','zh-CN');apiMock.attachments.mockResolvedValue([inherited]);apiMock.attachment.mockResolvedValue({...inherited,contextSources:[{attachmentId:'att-pdf',name:'research.pdf',chunkOrdinal:2,locator:'page 3',chunkSha256:'chunk'}],chunks:[{ordinal:2,locator:'page 3',characters:1200,preview:'路线感知检索内容'}]})});
afterEach(()=>{cleanup();vi.clearAllMocks();localStorage.clear()});

describe('AttachmentManager',()=>{
 it('shows only route files and exposes the exact chunks used by a bound request',async()=>{
  render(<I18nProvider><AttachmentManager workflowId="wf-1" instanceId="child" onClose={()=>undefined}/></I18nProvider>);
  const file=await screen.findByRole('button',{name:/research\.pdf/});
  expect(file).toHaveTextContent('继承自父路线');
  fireEvent.click(file);
  expect(await screen.findByText('本次请求使用的来源')).toBeInTheDocument();
  expect(screen.getAllByText('page 3')).toHaveLength(2);
  expect(screen.getByText('#2')).toBeInTheDocument();
  expect(apiMock.attachments).toHaveBeenCalledWith('wf-1','child','route');
  expect(apiMock.attachment).toHaveBeenCalledWith('wf-1','child','att-pdf');
 });

 it('searches only through the route-scoped file endpoint and opens a matching chunk',async()=>{
  apiMock.searchAttachments.mockResolvedValue([{attachmentId:'att-pdf',name:'research.pdf',routeInstanceId:'parent',routeTitle:'数据集',inherited:true,chunkOrdinal:2,locator:'page 3',characters:1200,score:12,preview:'private requirement'}]);
  render(<I18nProvider><AttachmentManager workflowId="wf-1" instanceId="child" onClose={()=>undefined}/></I18nProvider>);
  const input=await screen.findByPlaceholderText('搜索文件内容…');
  fireEvent.change(input,{target:{value:'private requirement'}});
  fireEvent.submit(input.closest('form')!);
  expect(await screen.findByText('private requirement')).toBeInTheDocument();
  expect(apiMock.searchAttachments).toHaveBeenCalledWith('wf-1','child','private requirement');
  fireEvent.click(screen.getByText('private requirement'));
  await waitFor(()=>expect(apiMock.attachment).toHaveBeenCalledWith('wf-1','child','att-pdf'));
 });

 it('keeps unsupported image OCR visible and can retry an unbound asset',async()=>{
  const failed={...inherited,attachmentId:'att-image',name:'scan.png',mimeType:'image/png',status:'uploaded' as const,parseStatus:'failed' as const,parser:null,parseErrorCode:'attachmentOcrUnavailable',parseError:'OCR unavailable',extractedCharacters:0,chunkCount:0,contextCharacters:0,contextTruncated:false,contextSources:[],messageId:null,inherited:false};
  apiMock.attachments.mockResolvedValue([failed]);apiMock.attachment.mockResolvedValue(failed);
  apiMock.reparseAttachment.mockResolvedValue({...failed,parseStatus:'ready',parser:'ocr',parseErrorCode:null,parseError:null,extractedCharacters:80,chunkCount:1});
  render(<I18nProvider><AttachmentManager workflowId="wf-1" instanceId="child" onClose={()=>undefined}/></I18nProvider>);
  fireEvent.click(await screen.findByRole('button',{name:/scan\.png/}));
  expect(await screen.findByText('图片已保存，但当前尚未配置 OCR。')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button',{name:'重新解析'}));
  await waitFor(()=>expect(apiMock.reparseAttachment).toHaveBeenCalledWith('wf-1','child','att-image'));
  expect(await screen.findByText('ocr')).toBeInTheDocument();
 });
});
