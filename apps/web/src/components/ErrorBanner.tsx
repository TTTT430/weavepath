import {useI18n} from '../lib/i18n';
export function ErrorBanner({message,onRetry}:{message:string;onRetry?:()=>void}){const {t}=useI18n();if(!message)return null;return <div className="error" role="alert"><span>{message||t('failed')}</span>{onRetry&&<button onClick={onRetry}>{t('retry')}</button>}</div>}
