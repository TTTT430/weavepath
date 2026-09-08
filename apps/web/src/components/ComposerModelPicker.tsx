import{useEffect,useMemo,useRef,useState}from'react';
import type{AIStatus}from'../domain/types';
import{api}from'../lib/api';
import{useI18n}from'../lib/i18n';
import{AppIcon}from'./AppIcon';

interface Props{
 status:AIStatus|null
 disabled?:boolean
 onChanged:(status:AIStatus)=>void
 onOpenSettings:()=>void
}

export function ComposerModelPicker({status,disabled=false,onChanged,onOpenSettings}:Props){
 const{t}=useI18n();
 const root=useRef<HTMLDivElement>(null);
 const[open,setOpen]=useState(false),[models,setModels]=useState<string[]>([]),[query,setQuery]=useState('');
 const[loading,setLoading]=useState(false),[switching,setSwitching]=useState(''),[error,setError]=useState('');
 const current=status?.model||'';
 const configured=Boolean(status?.configured&&current);
 const filtered=useMemo(()=>{
  const needle=query.trim().toLocaleLowerCase();
  return models.filter(model=>!needle||model.toLocaleLowerCase().includes(needle));
 },[models,query]);

 useEffect(()=>{
  if(!open)return;
  const close=(event:PointerEvent)=>{if(root.current&&!root.current.contains(event.target as Node))setOpen(false)};
  const escape=(event:KeyboardEvent)=>{if(event.key==='Escape')setOpen(false)};
  document.addEventListener('pointerdown',close);document.addEventListener('keydown',escape);
  return()=>{document.removeEventListener('pointerdown',close);document.removeEventListener('keydown',escape)};
 },[open]);

 async function load(force=false){
  if(!configured||loading||(!force&&models.length))return;
  setLoading(true);setError('');
  try{
   const result=await api.aiModels();
   setModels(result.models.includes(current)?result.models:[current,...result.models]);
  }catch{setError(t('modelListFailed'))}
  finally{setLoading(false)}
 }

 function toggle(){
  if(!configured){onOpenSettings();return}
  const next=!open;setOpen(next);setQuery('');setError('');
  if(next)void load();
 }

 async function choose(model:string){
  if(model===current){setOpen(false);return}
  setSwitching(model);setError('');
  try{onChanged(await api.switchAIModel(model));setOpen(false)}
  catch{setError(t('modelSwitchFailed'))}
  finally{setSwitching('')}
 }

 return <div className="composer-model-picker" ref={root}>
  <button type="button" className="composer-model-trigger" disabled={disabled} aria-haspopup="dialog" aria-expanded={open} aria-label={configured?`${t('switchModel')}: ${current}`:t('configureModel')} title={configured?t('switchModel'):t('configureModel')} onClick={toggle}>
   <AppIcon name="model" size={15}/><span>{configured?current:t('configureModel')}</span><AppIcon name="chevronDown" size={13}/>
  </button>
  {open&&<section className="composer-model-menu" role="dialog" aria-label={t('switchModel')}>
   <header><strong>{t('switchModel')}</strong><button type="button" className="icon-button" aria-label={t('refreshModels')} title={t('refreshModels')} disabled={loading||!!switching} onClick={()=>void load(true)}><AppIcon name="retry" size={14}/></button><button type="button" className="icon-button" aria-label={t('modelSettings')} title={t('modelSettings')} onClick={()=>{setOpen(false);onOpenSettings()}}><AppIcon name="settings" size={14}/></button></header>
   {models.length>6&&<label className="composer-model-search"><span>{t('searchModels')}</span><input autoFocus value={query} onChange={event=>setQuery(event.target.value)} placeholder={t('searchModels')}/></label>}
   {loading?<div className="composer-model-state" role="status"><span className="composer-model-spinner"/><span>{t('loadingModels')}</span></div>:error?<div className="composer-model-state is-error" role="alert"><AppIcon name="warning" size={15}/><span>{error}</span></div>:<div className="composer-model-options" role="listbox" aria-label={t('availableModels')}>{filtered.map(model=><button type="button" role="option" aria-selected={model===current} className={model===current?'current':''} disabled={!!switching} key={model} onClick={()=>void choose(model)}><span>{model}</span>{switching===model?<span className="composer-model-spinner"/>:model===current?<AppIcon name="check" size={14}/>:null}</button>)}{!filtered.length&&<p>{t('noModels')}</p>}</div>}
  </section>}
 </div>;
}
