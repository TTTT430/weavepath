import{useEffect,useState}from'react';
import type{AISettingsInput,ConnectionDiagnostics,NetworkMode}from'../domain/types';
import{api,ApiError}from'../lib/api';
import{useI18n,type Locale}from'../lib/i18n';
import{AppearanceSelect}from'./AppearanceSelect';
import{AppIcon}from'./AppIcon';
import'../settings.css';

const presets={
 openai:{url:'https://api.openai.com/v1',model:'gpt-4.1-mini'},
 deepseek:{url:'https://api.deepseek.com/v1',model:'deepseek-chat'},
 lmstudio:{url:'http://localhost:1234/v1',model:''},
 ollama:{url:'http://localhost:11434/v1',model:''},
 custom:{url:'',model:''}
}as const;
type Preset=keyof typeof presets;
const DRAFT_KEY='weavepath.model-settings.draft.v2';
type SettingsDraft={provider?:Preset;baseUrl?:string;model?:string;persist?:boolean;persistApiKey?:boolean;networkMode?:NetworkMode};
type Notice={message:string;tone:'active'|'success'|'error';diagnostics?:ConnectionDiagnostics};
function readDraft():SettingsDraft{
 try{const value=JSON.parse(localStorage.getItem(DRAFT_KEY)||'null');return value&&typeof value==='object'?value:{}}
 catch{return {}}
}

export function ModelSettingsDialog({onClose,onSaved}:{onClose:()=>void;onSaved:()=>void}){
 const draft=useState(readDraft)[0];
 const{t,locale,setLocale}=useI18n();const[provider,setProvider]=useState<Preset>(draft.provider&&draft.provider in presets?draft.provider:'custom'),[baseUrl,setBaseUrl]=useState(draft.baseUrl||''),[model,setModel]=useState(draft.model||''),[apiKey,setApiKey]=useState(''),[persist,setPersist]=useState(draft.persist??false),[persistApiKey,setPersistApiKey]=useState(draft.persistApiKey??false),[networkMode,setNetworkMode]=useState<NetworkMode>(draft.networkMode||'auto'),[hasKey,setHasKey]=useState(false),[secureStorageAvailable,setSecureStorageAvailable]=useState(false),[models,setModels]=useState<string[]>([]),[busy,setBusy]=useState(false),[notice,setNotice]=useState<Notice|null>(null),[confirmReset,setConfirmReset]=useState(false),[loaded,setLoaded]=useState(false);
 useEffect(()=>{api.aiSettings().then(x=>{if(!draft.baseUrl)setBaseUrl(x.baseUrl||'');if(!draft.model)setModel(x.model||'');if(draft.persist===undefined)setPersist(x.persistence==='local');if(draft.persistApiKey===undefined)setPersistApiKey(Boolean(x.apiKeyPersisted));if(!draft.networkMode)setNetworkMode(x.networkMode||'auto');setHasKey(x.hasApiKey);setSecureStorageAvailable(Boolean(x.secureKeyStorageAvailable));if(x.credentialError)setNotice({message:t('secureStorageError'),tone:'error'});setLoaded(true)}).catch(e=>{setNotice({message:e instanceof Error?e.message:String(e),tone:'error'});setLoaded(true)})},[]);
 useEffect(()=>{if(!loaded)return;try{localStorage.setItem(DRAFT_KEY,JSON.stringify({provider,baseUrl,model,persist,persistApiKey,networkMode}))}catch{/* Draft persistence is best-effort. */}},[loaded,provider,baseUrl,model,persist,persistApiKey,networkMode]);
 function choose(value:Preset){setProvider(value);const p=presets[value];if(value!=='custom'){setBaseUrl(p.url);setModel(p.model)}}
 function body():AISettingsInput{return{baseUrl:baseUrl.trim(),model:model.trim(),...(apiKey?{apiKey}:{}),persistence:persist?'local':'memory',persistApiKey,networkMode}}
 function discoveryError(error:unknown){if(error instanceof ApiError){const keys={modelDiscoveryTimeout:'modelDiscoveryTimeout',modelDiscoveryUnauthorized:'modelDiscoveryUnauthorized',modelDiscoveryUnsupported:'modelDiscoveryUnsupported',modelDiscoveryConnectionFailed:'modelDiscoveryConnectionFailed',modelDiscoveryDnsFailed:'modelDiscoveryDnsFailed',modelDiscoveryTlsFailed:'modelDiscoveryTlsFailed',modelDiscoveryProxyFailed:'modelDiscoveryProxyFailed',modelDiscoveryHttpError:'modelDiscoveryHttpError',modelDiscoveryInvalidResponse:'modelDiscoveryInvalidResponse'}as const;const key=error.code?keys[error.code as keyof typeof keys]:undefined;if(key)return t(key)}return error instanceof Error?error.message:String(error)}
 async function validate(){setBusy(true);setNotice({message:t('testingConnection'),tone:'active'});try{const hasSelectedModel=!!model.trim();const x=await api.validateAISettings(body());setModels(x.models);setNotice({message:t(!hasSelectedModel||x.selectedModelAvailable?'connectionOk':'connectionOkModelMissing'),tone:'success',diagnostics:x.diagnostics})}catch(e){setNotice({message:`${t('connectionFailed')}: ${discoveryError(e)}`,tone:'error',diagnostics:e instanceof ApiError?e.diagnostics:undefined})}finally{setBusy(false)}}
 async function save(){setBusy(true);setNotice(null);try{await api.saveAISettings(body());await onSaved();onClose()}catch(e){setNotice({message:e instanceof Error?e.message:String(e),tone:'error',diagnostics:e instanceof ApiError?e.diagnostics:undefined})}finally{setBusy(false)}}
 async function reset(){setBusy(true);try{await api.resetAISettings();try{localStorage.removeItem(DRAFT_KEY)}catch{/* Ignore storage failures. */}await onSaved();onClose()}catch(e){setNotice({message:e instanceof Error?e.message:String(e),tone:'error'});setBusy(false)}}
 function outcomeLabel(value:ConnectionDiagnostics['attempts'][number]['outcome']){return value==='connected'?t('networkConnected'):value==='http-error'?t('networkHttpError'):value==='invalid-response'?t('networkInvalidResponse'):t('networkConnectionError')}
 return <div className="modal-backdrop settings-backdrop" role="presentation" onMouseDown={e=>{if(e.target===e.currentTarget)onClose()}}><section className="modal settings-modal" role="dialog" aria-modal="true" aria-labelledby="model-settings-title">
  <header className="settings-modal-header"><h2 id="model-settings-title">{t('modelSettings')}</h2><button className="settings-close icon-button" type="button" aria-label={t('close')} title={t('close')} onClick={onClose}><AppIcon name="close"/></button></header>
  <div className="settings-modal-body">
   <div className="settings-form-grid">
    <label>{t('language')}<select value={locale} onChange={e=>setLocale(e.target.value as Locale)}><option value="zh-CN">中文</option><option value="en">English</option></select></label><AppearanceSelect/>
    <label className="settings-wide">{t('provider')}<select value={provider} onChange={e=>choose(e.target.value as Preset)}><option value="openai">OpenAI</option><option value="deepseek">DeepSeek</option><option value="lmstudio">LM Studio</option><option value="ollama">Ollama</option><option value="custom">{t('customProvider')}</option></select></label>
    <label className="settings-wide">{t('baseUrl')}<input value={baseUrl} onChange={e=>setBaseUrl(e.target.value)} placeholder="https://…/v1"/></label>
    <label className="settings-wide">{t('model')}<input list="available-models" value={model} onChange={e=>setModel(e.target.value)} placeholder={t('modelPlaceholder')}/><datalist id="available-models">{models.map(x=><option key={x} value={x}/>)}</datalist><small>{t('manualModelHint')}</small></label>
    <label className="settings-wide">{t('apiKey')}<input type="password" autoComplete="new-password" value={apiKey} onChange={e=>setApiKey(e.target.value)} placeholder={hasKey?t('keyRetained'):t('keyOptional')}/><small>{t('keyMemoryHint')}</small></label>
    <label className="settings-wide">{t('networkMode')}<select value={networkMode} onChange={e=>setNetworkMode(e.target.value as NetworkMode)}><option value="auto">{t('networkAuto')}</option><option value="system">{t('networkSystem')}</option><option value="direct">{t('networkDirect')}</option></select><small>{t('networkModeHint')}</small></label>
    <label className="check settings-wide"><input type="checkbox" checked={persist} onChange={e=>{const checked=e.target.checked;setPersist(checked);if(!checked)setPersistApiKey(false)}}/><span>{t('persistNonSecret')}</span></label>
    <label className="check settings-wide"><input type="checkbox" checked={persistApiKey} disabled={!secureStorageAvailable} onChange={e=>{setPersistApiKey(e.target.checked);if(e.target.checked)setPersist(true)}}/><span>{t('persistApiKey')}<small>{secureStorageAvailable?t('persistApiKeyHint'):t('secureStorageUnavailable')}</small></span></label>
   </div>
   {notice&&<div className={`settings-connection is-${notice.tone}`} role={notice.tone==='error'?'alert':'status'} aria-live="polite"><span className="settings-connection__icon"><AppIcon name={notice.tone==='error'?'warning':notice.tone==='success'?'check':'activity'} size={17}/></span><div className="settings-connection__body"><strong>{notice.message}</strong>{notice.diagnostics&&<div className="settings-connection__attempts">{notice.diagnostics.attempts.map((attempt,index)=><span className={`connection-attempt is-${attempt.outcome}`} key={`${attempt.route}-${index}`}><AppIcon name={attempt.outcome==='connected'?'check':attempt.outcome==='connection-error'?'retry':'warning'} size={13}/><b>{attempt.route==='direct'?t('networkDirectRoute'):t('networkSystemRoute')}</b><span>{outcomeLabel(attempt.outcome)}</span><small>{attempt.durationMs} ms{attempt.httpStatus?` · HTTP ${attempt.httpStatus}`:''}</small></span>)}</div>}</div></div>}
  </div>
  <footer className="settings-modal-footer">
   {confirmReset?<div className="reset-confirm"><span>{t('resetConfirm')}</span><button type="button" onClick={()=>setConfirmReset(false)}>{t('cancel')}</button><button type="button" className="danger" disabled={busy} onClick={()=>void reset()}>{t('confirmReset')}</button></div>:<div className="modal-actions"><button type="button" className="danger-outline" onClick={()=>setConfirmReset(true)}>{t('reset')}</button><span/><button type="button" onClick={()=>void validate()} disabled={busy||!baseUrl.trim()}>{t('testFetch')}</button><button type="button" className="primary" onClick={()=>void save()} disabled={busy||!baseUrl.trim()||!model.trim()}>{t('save')}</button></div>}
  </footer>
 </section></div>
}
