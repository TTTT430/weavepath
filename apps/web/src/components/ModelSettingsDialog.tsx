import{useEffect,useState}from'react';
import type{AISettingsInput}from'../domain/types';
import{api}from'../lib/api';
import{useI18n,type Locale}from'../lib/i18n';
import'../settings.css';

const presets={
 openai:{url:'https://api.openai.com/v1',model:'gpt-4.1-mini'},
 deepseek:{url:'https://api.deepseek.com/v1',model:'deepseek-chat'},
 lmstudio:{url:'http://localhost:1234/v1',model:''},
 ollama:{url:'http://localhost:11434/v1',model:''},
 custom:{url:'',model:''}
}as const;
type Preset=keyof typeof presets;

export function ModelSettingsDialog({onClose,onSaved}:{onClose:()=>void;onSaved:()=>void}){
 const{t,locale,setLocale}=useI18n();const[provider,setProvider]=useState<Preset>('custom'),[baseUrl,setBaseUrl]=useState(''),[model,setModel]=useState(''),[apiKey,setApiKey]=useState(''),[timeout,setTimeoutValue]=useState(60),[persist,setPersist]=useState(false),[hasKey,setHasKey]=useState(false),[models,setModels]=useState<string[]>([]),[busy,setBusy]=useState(false),[notice,setNotice]=useState(''),[confirmReset,setConfirmReset]=useState(false);
 useEffect(()=>{api.aiSettings().then(x=>{setBaseUrl(x.baseUrl||'');setModel(x.model||'');setTimeoutValue(x.timeoutSeconds);setPersist(x.persistence==='local');setHasKey(x.hasApiKey)}).catch(e=>setNotice(e.message))},[]);
 function choose(value:Preset){setProvider(value);const p=presets[value];if(value!=='custom'){setBaseUrl(p.url);setModel(p.model)}}
 function body():AISettingsInput{return{baseUrl:baseUrl.trim(),model:model.trim(),...(apiKey?{apiKey}:{}),timeoutSeconds:Number(timeout),persistence:persist?'local':'memory'}}
 async function validate(){setBusy(true);setNotice('');try{const hasSelectedModel=!!model.trim();const x=await api.validateAISettings(body());setModels(x.models);setNotice(t(!hasSelectedModel||x.selectedModelAvailable?'connectionOk':'connectionOkModelMissing'))}catch(e){setNotice(`${t('connectionFailed')}: ${e instanceof Error?e.message:String(e)}`)}finally{setBusy(false)}}
 async function save(){setBusy(true);setNotice('');try{await api.saveAISettings(body());await onSaved();onClose()}catch(e){setNotice(e instanceof Error?e.message:String(e))}finally{setBusy(false)}}
 async function reset(){setBusy(true);try{await api.resetAISettings();await onSaved();onClose()}catch(e){setNotice(e instanceof Error?e.message:String(e));setBusy(false)}}
 return <div className="modal-backdrop" role="presentation" onMouseDown={e=>{if(e.target===e.currentTarget)onClose()}}><section className="modal settings-modal" role="dialog" aria-modal="true" aria-labelledby="model-settings-title"><header><h2 id="model-settings-title">{t('modelSettings')}</h2><button aria-label={t('close')} onClick={onClose}>×</button></header>
  <label>{t('language')}<select value={locale} onChange={e=>setLocale(e.target.value as Locale)}><option value="zh-CN">中文</option><option value="en">English</option></select></label>
  <label>{t('provider')}<select value={provider} onChange={e=>choose(e.target.value as Preset)}><option value="openai">OpenAI</option><option value="deepseek">DeepSeek</option><option value="lmstudio">LM Studio</option><option value="ollama">Ollama</option><option value="custom">{t('customProvider')}</option></select></label>
  <label>{t('baseUrl')}<input value={baseUrl} onChange={e=>setBaseUrl(e.target.value)} placeholder="https://…/v1"/></label>
  <label>{t('model')}<input list="available-models" value={model} onChange={e=>setModel(e.target.value)}/><datalist id="available-models">{models.map(x=><option key={x} value={x}/>)}</datalist></label>
  <label>{t('apiKey')}<input type="password" autoComplete="new-password" value={apiKey} onChange={e=>setApiKey(e.target.value)} placeholder={hasKey?t('keyRetained'):t('keyOptional')}/><small>{t('keyMemoryHint')}</small></label>
  <label>{t('timeout')}<input type="number" min="1" max="300" value={timeout} onChange={e=>setTimeoutValue(Number(e.target.value))}/></label>
  <label className="check"><input type="checkbox" checked={persist} onChange={e=>setPersist(e.target.checked)}/><span>{t('persistNonSecret')}</span></label>
  {notice&&<p className="settings-notice" role="status">{notice}</p>}
  {confirmReset?<div className="reset-confirm"><span>{t('resetConfirm')}</span><button onClick={()=>setConfirmReset(false)}>{t('cancel')}</button><button className="danger" disabled={busy} onClick={()=>void reset()}>{t('confirmReset')}</button></div>:<div className="modal-actions"><button className="danger-outline" onClick={()=>setConfirmReset(true)}>{t('reset')}</button><span/><button onClick={()=>void validate()} disabled={busy||!baseUrl.trim()}>{t('testFetch')}</button><button className="primary" onClick={()=>void save()} disabled={busy||!baseUrl.trim()||!model.trim()}>{t('save')}</button></div>}
 </section></div>
}
