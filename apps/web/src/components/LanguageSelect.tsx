import {useI18n,type Locale} from '../lib/i18n';
export function LanguageSelect(){const {locale,setLocale,t}=useI18n();return <label className="language"><span>{t('language')}</span><select value={locale} onChange={e=>setLocale(e.target.value as Locale)}><option value="zh-CN">中文</option><option value="en">English</option></select></label>}
