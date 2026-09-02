import {useEffect, useState} from 'react';
import {useI18n} from '../lib/i18n';
import {applyAppearance, readAppearance, saveAppearance, type Appearance} from '../lib/appearance';

export function AppearanceSelect(){
 const {t}=useI18n();
 const [appearance,setAppearance]=useState<Appearance>(readAppearance);
 useEffect(()=>{applyAppearance(appearance)},[appearance]);
 return <label className="language appearance-select"><span>{t('appearance')}</span><select value={appearance} onChange={event=>{const next=event.target.value as Appearance;setAppearance(next);saveAppearance(next)}}><option value="dark">{t('appearanceDark')}</option><option value="light">{t('appearanceLight')}</option></select></label>;
}
