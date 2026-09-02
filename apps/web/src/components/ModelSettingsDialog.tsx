import{useEffect,useState}from'react';
import type{AISettingsInput}from'../domain/types';
import{api,ApiError}from'../lib/api';
import{useI18n,type Locale}from'../lib/i18n';
import{AppearanceSelect}from'./AppearanceSelect';
import'../settings.css';

const presets={
 openai:{url:'https://api.openai.com/v1',model:'gpt-4.1-mini'},
 deepseek:{url:'https://api.deepseek.com/v1',model:'deepseek-chat'},
 lmstudio:{url:'http://localhost:1234/v1',model:''},
 ollama:{url:'http://localhost:11434/v1',model:''},
 custom:{url:'',model:''}
}as const;
type Preset=keyof typeof presets;
const DRAFT_KEY='weavepath.model-settings.draft.v1';
const SESSION_SECRET_KEY='weavepath.model-settings.api-key.session.v1';
type SettingsDraft={provider?:Preset;baseUrl?:string;model?:string;timeout?:number;persist?:boolean};
function readDraft():SettingsDraft{
 try{const value=JSON.parse(localStorage.getItem(DRAFT_KEY)||'null');return value&&typeof value==='object'?value:{}}
 catch{return {}}
}
function readSessionSecret(){try{return sessionStorage.getItem(SESSION_SECRET_KEY)||''}catch{return ''}}

export function ModelSettingsDialog({onClose,onSaved}:{onClose:()=>void;onSaved:()=>void}){
 const draft=useState(readDraft)[0];
 const{t,locale,setLocale}=useI18n();const[provider,setProvider]=useState<Preset>(draft.provider&&draft.provider in presets?draft.provider:'custom'),[baseUrl,setBaseUrl]=useState(draft.baseUrl||''),[model,setModel]=useState(draft.model||''),[apiKey,setApiKey]=useState(readSessionSecret),[timeout,setTimeoutValue]=useState(draft.timeout||60),[persist,setPersist]=useState(draft.persist??false),[hasKey,setHasKey]=useState(false),[models,setModels]=useState<string[]>([]),[busy,setBusy]=useState(false),[notice,setNotice]=useState(''),[confirmReset,setConfirmReset]=useState(false),[loaded,setLoaded]=useState(false);
 useEffect(()=>{api.aiSettings().then(x=>{if(!draft.baseUrl)setBaseUrl(x.baseUrl||'');if(!draft.model)setModel(x.model||'');if(!draft.timeout)setTimeoutValue(x.timeoutSeconds);if(draft.persist===undefined)setPersist(x.persistence==='local');setHasKey(x.hasApiKey);setLoaded(true)}).catch(e=>{setNotice(e.message);setLoaded(true)})},[]);
 useEffect(()=>{if(!loaded)return;try{localStorage.setItem(DRAFT_KEY,JSON.stringify({provider,baseUrl,model,timeout,persist}))}catch{/* Draft persistence is best-effort. */}},[loaded,provider,baseUrl,model,timeout,persist]);
 useEffect(()=>{try{if(apiKey)sessionStorage.setItem(SESSION_SECRET_KEY,apiKey);else sessionStorage.removeItem(SESSION_SECRET_KEY)}catch{/* Session secret persistence is best-effort. */}},[apiKey]);
 function choose(value:Preset){setProvider(value);const p=presets[value];if(value!=='custom'){setBaseUrl(p.url);setModel(p.model)}}
 function body():AISettingsInput{return{baseUrl:baseUrl.trim(),model:model.trim(),...(apiKey?{apiKey}:{}),timeoutSeconds:Number(timeout),persistence:persist?'local':'memory'}}
 function discoveryError(error:unknown){if(error instanceof ApiError){const keys={modelDiscoveryTimeout:'modelDiscoveryTimeout',modelDiscoveryUnauthorized:'modelDiscoveryUnauthorized',modelDiscoveryUnsupported:'modelDiscoveryUnsupported',modelDiscoveryConnectionFailed:'modelDiscoveryConnectionFailed',modelDiscoveryInvalidResponse:'modelDiscoveryInvalidResponse'}as const;const key=error.code?keys[error.code as keyof typeof keys]:undefined;if(key)return t(key)}return error instanceof Error?error.message:String(error)}
 async function validate(){setBusy(true);setNotice('');try{const hasSelectedModel=!!model.trim();const x=await api.validateAISettings(body());setModels(x.models);setNotice(t(!hasSelectedModel||x.selectedModelAvailable?'connectionOk':'connectionOkModelMissing'))}catch(e){setNotice(`${t('connectionFailed')}: ${discoveryError(e)}`)}finally{setBusy(false)}}
 async function save(){setBusy(true);setNotice('');try{await api.saveAISettings(body());await onSaved();onClose()}catch(e){setNotice(e instanceof Error?e.message:String(e))}finally{setBusy(false)}}
 async function reset(){setBusy(true);try{await api.resetAISettings();try{localStorage.removeItem(DRAFT_KEY);sessionStorage.removeItem(SESSION_SECRET_KEY)}catch{/* Ignore storage failures. */}await onSaved();onClose()}catch(e){setNotice(e instanceof Error?e.message:String(e));setBusy(false)}}
 return <div className="modal-backdrop settings-backdrop" role="presentation" onMouseDown={e=>{if(e.target===e.currentTarget)onClose()}}><section className="modal settings-modal" role="dialog" aria-modal="true" aria-labelledby="model-settings-title">
  <header className="settings-modal-header"><h2 id="model-settings-title">{t('modelSettings')}</h2><button className="settings-close" type="button" aria-label={t('close')} onClick={onClose}>×</button></header>
  <div className="settings-modal-body">
   <div className="settings-form-grid">
    <label>{t('language')}<select value={locale} onChange={e=>setLocale(e.target.value as Locale)}><option value="zh-CN">中文</option><option value="en">English</option></select></label><AppearanceSelect/>
    <label className="settings-wide">{t('provider')}<select value={provider} onChange={e=>choose(e.target.value as Preset)}><option value="openai">OpenAI</option><option value="deepseek">DeepSeek</option><option value="lmstudio">LM Studio</option><option value="ollama">Ollama</option><option value="custom">{t('customProvider')}</option></select></label>
    <label className="settings-wide">{t('baseUrl')}<input value={baseUrl} onChange={e=>setBaseUrl(e.target.value)} placeholder="https://…/v1"/></label>
    <label className="settings-wide">{t('model')}<input list="available-models" value={model} onChange={e=>setModel(e.target.value)} placeholder={t('modelPlaceholder')}/><datalist id="available-models">{models.map(x=><option key={x} value={x}/>)}</datalist><small>{t('manualModelHint')}</small></label>
    <label className="settings-wide">{t('apiKey')}<input type="password" autoComplete="new-password" value={apiKey} onChange={e=>setApiKey(e.target.value)} placeholder={hasKey?t('keyRetained'):t('keyOptional')}/><small>{t('keyMemoryHint')}</small></label>
    <label>{t('timeout')}<input type="number" min="1" max="300" value={timeout} onChange={e=>setTimeoutValue(Number(e.target.value))}/></label>
    <label className="check settings-wide"><input type="checkbox" checked={persist} onChange={e=>setPersist(e.target.checked)}/><span>{t('persistNonSecret')}</span></label>
   </div>
   {notice&&<p className="settings-notice" role="status">{notice}</p>}
  </div>
  <footer className="settings-modal-footer">
   {confirmReset?<div className="reset-confirm"><span>{t('resetConfirm')}</span><button type="button" onClick={()=>setConfirmReset(false)}>{t('cancel')}</button><button type="button" className="danger" disabled={busy} onClick={()=>void reset()}>{t('confirmReset')}</button></div>:<div className="modal-actions"><button type="button" className="danger-outline" onClick={()=>setConfirmReset(true)}>{t('reset')}</button><span/><button type="button" onClick={()=>void validate()} disabled={busy||!baseUrl.trim()}>{t('testFetch')}</button><button type="button" className="primary" onClick={()=>void save()} disabled={busy||!baseUrl.trim()||!model.trim()}>{t('save')}</button></div>}
  </footer>
 </section></div>
}
