import{useCallback,useEffect,useState,type FormEvent}from'react';
import type{AttachmentSearchResult,AttachmentSearchStatus,UploadedAttachment}from'../domain/types';
import{api}from'../lib/api';
import{formatFileSize}from'../lib/chatAttachments';
import{useI18n}from'../lib/i18n';
import{AppIcon}from'./AppIcon';

interface AttachmentManagerProps{
 workflowId:string
 instanceId:string
 onClose:()=>void
 initialAttachmentId?:string
 initialChunkOrdinal?:number
}

export function AttachmentManager({workflowId,instanceId,onClose,initialAttachmentId,initialChunkOrdinal}:AttachmentManagerProps){
 const{t,locale}=useI18n();
 const[items,setItems]=useState<UploadedAttachment[]>([]);
 const[selected,setSelected]=useState<UploadedAttachment|null>(null);
 const[loading,setLoading]=useState(true);
 const[error,setError]=useState('');
 const[retrying,setRetrying]=useState('');
 const[query,setQuery]=useState('');
 const[searchResults,setSearchResults]=useState<AttachmentSearchResult[]|null>(null);
 const[searching,setSearching]=useState(false);
 const[indexStatus,setIndexStatus]=useState<AttachmentSearchStatus|null>(null);
 const load=useCallback(async()=>{
  setLoading(true);setError('');
  try{
   const[value,status]=await Promise.all([api.attachments(workflowId,instanceId,'route'),api.attachmentSearchStatus(workflowId,instanceId)]);setItems(value);setIndexStatus(status);
  }catch{setError(t('attachmentLoadFailed'))}
  finally{setLoading(false)}
 },[instanceId,t,workflowId]);
 useEffect(()=>{void load()},[load]);
 useEffect(()=>{setQuery('');setSearchResults(null);setSelected(null)},[instanceId,workflowId]);
 useEffect(()=>{if(initialAttachmentId)void inspect({attachmentId:initialAttachmentId})},[initialAttachmentId,instanceId,workflowId]);
 useEffect(()=>{const close=(event:KeyboardEvent)=>{if(event.key==='Escape')onClose()};window.addEventListener('keydown',close);return()=>window.removeEventListener('keydown',close)},[onClose]);

 async function inspect(item:Pick<UploadedAttachment,'attachmentId'>){
  setError('');
  try{setSelected(await api.attachment(workflowId,instanceId,item.attachmentId))}
  catch{setError(t('attachmentLoadFailed'))}
 }

 async function search(event:FormEvent){
  event.preventDefault();
  const value=query.trim();
  if(!value){setSearchResults(null);return}
  setSearching(true);setError('');
  try{setSearchResults(await api.searchAttachments(workflowId,instanceId,value))}
  catch{setError(t('attachmentLoadFailed'))}
  finally{setSearching(false)}
 }

 function clearSearch(){setQuery('');setSearchResults(null)}

 async function reparse(item:UploadedAttachment){
  setRetrying(item.attachmentId);setError('');
  try{
   const value=await api.reparseAttachment(workflowId,instanceId,item.attachmentId);
   setItems(current=>current.map(existing=>existing.attachmentId===value.attachmentId?value:existing));setSelected(value);
  }catch{setError(t('attachmentParseFailed'))}
  finally{setRetrying('')}
 }

 function statusLabel(item:UploadedAttachment){
  return item.parseStatus==='ready'?t('parseReady'):item.parseStatus==='processing'?t('parseProcessing'):t('parseFailed');
 }

 return <div className="attachment-manager-backdrop" role="presentation" onMouseDown={event=>{if(event.target===event.currentTarget)onClose()}}>
  <section className="attachment-manager" role="dialog" aria-modal="true" aria-labelledby="attachment-manager-title">
   <header>
    <div><h2 id="attachment-manager-title">{t('attachmentLibrary')}</h2><p>{t('attachmentLibraryHint')}</p>{indexStatus&&<span className={`attachment-index-status${indexStatus.ready?' ready':''}`}><AppIcon name={indexStatus.ready?'check':'clock'} size={12}/>{indexStatus.ready?t('searchIndexReady'):t('searchIndexBuilding')} · {indexStatus.indexedChunks.toLocaleString(locale)}/{indexStatus.readyChunks.toLocaleString(locale)}</span>}</div>
    <div className="attachment-manager-header-actions"><button type="button" className="icon-button" aria-label={t('retry')} title={t('retry')} onClick={()=>void load()}><AppIcon name="retry"/></button><button type="button" className="icon-button" aria-label={t('close')} title={t('close')} onClick={onClose}><AppIcon name="close"/></button></div>
   </header>
   {error&&<div className="attachment-manager-error" role="alert"><AppIcon name="warning"/><span>{error}</span></div>}
   <div className="attachment-manager-body">
    <div className="attachment-manager-list" aria-busy={loading||searching}>
     <form className="attachment-search-form" role="search" onSubmit={event=>void search(event)}>
      <AppIcon name="search" size={15}/><input aria-label={t('searchRouteFiles')} placeholder={t('searchFilesPlaceholder')} value={query} onChange={event=>setQuery(event.target.value)}/>
      {query&&<button type="button" className="icon-button" aria-label={t('clearSearch')} title={t('clearSearch')} onClick={clearSearch}><AppIcon name="close" size={13}/></button>}
      <button type="submit" className="icon-button" disabled={!query.trim()||searching} aria-label={t('searchRouteFiles')} title={t('searchRouteFiles')}>{searching?<span className="composer-model-spinner"/>:<AppIcon name="search" size={14}/>}</button>
     </form>
     {searchResults&&<div className="attachment-search-count">{searchResults.length.toLocaleString(locale)} {t('searchResultCount')}</div>}
     {searching?<div className="attachment-manager-empty"><span className="composer-model-spinner"/>{t('searchingFiles')}</div>:searchResults?(!searchResults.length?<div className="attachment-manager-empty"><AppIcon name="search" size={22}/><span>{t('noSearchResults')}</span></div>:searchResults.map(result=><button type="button" key={`${result.attachmentId}-${result.chunkOrdinal}`} className={`attachment-search-card${selected?.attachmentId===result.attachmentId?' selected':''}`} onClick={()=>void inspect(result)}>
      <span className="attachment-search-card-head"><strong title={result.name}>{result.name}</strong><small>{result.locator}</small></span>
      <span className="attachment-search-preview">{result.preview}</span>
      <span className="attachment-search-route">{result.routeTitle} · {result.inherited?t('inheritedFile'):t('localFile')}</span>
     </button>)):loading?<div className="attachment-manager-empty"><span className="composer-model-spinner"/>{t('loadingAttachments')}</div>:!items.length?<div className="attachment-manager-empty"><AppIcon name="attachment" size={22}/><span>{t('noAttachments')}</span></div>:items.map(item=><button type="button" key={item.attachmentId} className={`attachment-file-card${selected?.attachmentId===item.attachmentId?' selected':''}`} aria-pressed={selected?.attachmentId===item.attachmentId} onClick={()=>void inspect(item)}>
      <span className="attachment-file-icon"><AppIcon name="attachment"/></span>
      <span className="attachment-file-main"><strong title={item.name}>{item.name}</strong><small>{formatFileSize(item.size)} · {item.inherited?t('inheritedFile'):t('localFile')}</small></span>
      <span className={`attachment-status ${item.parseStatus}`}>{statusLabel(item)}</span>
     </button>)}
    </div>
    <aside className="attachment-manager-detail">
     {!selected?<div className="attachment-manager-empty"><AppIcon name="details" size={22}/><span>{t('inspectAttachment')}</span></div>:<>
      <div className="attachment-detail-title"><div><h3>{selected.name}</h3><p>{selected.routeTitle||selected.routeInstanceId||'—'}</p></div><span className={`attachment-status ${selected.parseStatus}`}>{statusLabel(selected)}</span></div>
      <dl className="attachment-detail-metrics">
       <div><dt>{t('parser')}</dt><dd>{selected.parser||'—'}</dd></div>
       <div><dt>{t('chunks')}</dt><dd>{selected.chunkCount.toLocaleString(locale)}</dd></div>
       <div><dt>{t('extractedCharacters')}</dt><dd>{selected.extractedCharacters.toLocaleString(locale)}</dd></div>
      </dl>
      {selected.parseStatus==='failed'&&<div className="attachment-parse-failure"><AppIcon name="warning"/><div><strong>{t('attachmentParseFailed')}</strong><p>{selected.parseErrorCode==='attachmentOcrUnavailable'?t('imageOcrUnavailable'):selected.parseError||selected.parseErrorCode||t('attachmentReadingFailed')}</p>{selected.status==='uploaded'&&<button type="button" disabled={retrying===selected.attachmentId} onClick={()=>void reparse(selected)}>{retrying===selected.attachmentId?t('retryingParsing'):t('retryParsing')}</button>}</div></div>}
      {!!selected.contextSources.length&&<section className="attachment-source-section"><h4>{t('sourceUsed')}</h4><ul>{selected.contextSources.map(source=><li key={`${source.chunkOrdinal}-${source.locator}`}><span>{source.locator}</span><small>#{source.chunkOrdinal}</small></li>)}</ul></section>}
      {!!selected.chunks?.length&&<section className="attachment-chunk-section"><h4>{t('chunkPreview')}</h4>{selected.chunks.map(chunk=><details key={chunk.ordinal} open={selected.attachmentId===initialAttachmentId&&chunk.ordinal===initialChunkOrdinal}><summary><span>{chunk.locator}</span><small>{chunk.characters.toLocaleString(locale)}</small></summary><pre>{chunk.preview}</pre></details>)}</section>}
     </>}
    </aside>
   </div>
  </section>
 </div>;
}
