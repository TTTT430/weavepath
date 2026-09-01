import{cleanup,fireEvent,render,screen,waitFor}from'@testing-library/react';
import{afterEach,beforeEach,describe,expect,it,vi}from'vitest';
import{I18nProvider}from'../lib/i18n';
import{ApiError}from'../lib/api';
import{ModelSettingsDialog}from'./ModelSettingsDialog';

const apiMock=vi.hoisted(()=>({aiSettings:vi.fn(),validateAISettings:vi.fn(),saveAISettings:vi.fn(),resetAISettings:vi.fn()}));
vi.mock('../lib/api',()=>({api:apiMock,ApiError:class ApiError extends Error{constructor(message:string,public status:number,public code?:string){super(message)}}}));
function view(onSaved=vi.fn()){return render(<I18nProvider><ModelSettingsDialog onClose={vi.fn()} onSaved={onSaved}/></I18nProvider>)}

beforeEach(()=>{localStorage.clear();localStorage.setItem('cw.locale','zh-CN');apiMock.aiSettings.mockResolvedValue({configured:true,provider:'openai-compatible',baseUrl:'http://localhost:1234/v1',model:'私有模型',timeoutSeconds:45,systemPrompt:'',hasApiKey:true,source:'runtime',persistence:'memory'});apiMock.validateAISettings.mockResolvedValue({ok:true,modelCount:2,selectedModelAvailable:true,models:['私有模型','另一个模型']});apiMock.saveAISettings.mockResolvedValue({configured:true})});
afterEach(()=>{cleanup();vi.clearAllMocks()});

describe('model settings',()=>{
 it('renders bilingual chrome without ever rendering the stored secret',async()=>{view();expect(await screen.findByDisplayValue('私有模型')).toBeInTheDocument();expect(screen.queryByDisplayValue(/secret|sk-/i)).not.toBeInTheDocument();expect(screen.getByPlaceholderText('留空将保留已有密钥')).toHaveValue('');fireEvent.change(screen.getByDisplayValue('中文'),{target:{value:'en'}});expect(screen.getByText('Model settings')).toBeInTheDocument();expect(screen.getByDisplayValue('私有模型')).toBeInTheDocument()});
 it('validates without saving and populates the model choices',async()=>{view();fireEvent.click(await screen.findByRole('button',{name:'测试并获取模型'}));await waitFor(()=>expect(apiMock.validateAISettings).toHaveBeenCalledTimes(1));expect(apiMock.saveAISettings).not.toHaveBeenCalled();expect(document.querySelector('option[value="另一个模型"]')).not.toBeNull();expect(screen.getByText('连接成功，已加载模型列表。')).toBeInTheDocument()});
 it('discovers models when the model field is blank but keeps Save disabled',async()=>{apiMock.aiSettings.mockResolvedValue({...await apiMock.aiSettings(),model:''});view();const test=await screen.findByRole('button',{name:'测试并获取模型'}),save=screen.getByRole('button',{name:'保存'});expect(test).toBeEnabled();expect(save).toBeDisabled();fireEvent.click(test);await waitFor(()=>expect(apiMock.validateAISettings).toHaveBeenCalledWith(expect.objectContaining({model:''})));expect(document.querySelector('option[value="私有模型"]')).not.toBeNull()});
 it('explains provider timeouts without exposing low-level transport text',async()=>{apiMock.validateAISettings.mockRejectedValueOnce(new ApiError('Unable to connect to the model provider',504,'modelDiscoveryTimeout'));view();fireEvent.click(await screen.findByRole('button',{name:'测试并获取模型'}));expect(await screen.findByText('连接失败: 模型服务连接超时，当前电脑无法访问该地址。')).toBeInTheDocument();expect(screen.getByText('如果服务商不提供 /models 接口，可以手动填写准确模型 ID 后直接保存。')).toBeInTheDocument()});
 it('saves non-secret settings and refreshes status',async()=>{const refreshed=vi.fn();view(refreshed);fireEvent.click(await screen.findByRole('button',{name:'保存'}));await waitFor(()=>expect(apiMock.saveAISettings).toHaveBeenCalledWith(expect.objectContaining({baseUrl:'http://localhost:1234/v1',model:'私有模型',timeoutSeconds:45,persistence:'memory'})));expect(apiMock.saveAISettings.mock.calls[0][0]).not.toHaveProperty('apiKey');expect(refreshed).toHaveBeenCalledTimes(1)});
});
