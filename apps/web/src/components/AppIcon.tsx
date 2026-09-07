export type AppIconName=
 |'edit'
 |'copy'
 |'check'
 |'pin'
 |'close'
 |'plus'
 |'minus'
 |'chevronDown'
 |'chevronRight'
 |'locate'
 |'fit'
 |'details'
 |'canvas'
 |'play'
 |'settings'
 |'workflow';

export interface AppIconProps{
 name:AppIconName
 size?:number
 className?:string
}

/** Decorative inline icon. The owning control must provide its accessible name. */
export function AppIcon({name,size=16,className='app-icon'}:AppIconProps){
 return <svg className={className} viewBox="0 0 20 20" width={size} height={size} fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
  {name==='edit'?<><path d="M4 13.8 3.5 17l3.2-.5L16 7.2a1.7 1.7 0 0 0 0-2.4l-.8-.8a1.7 1.7 0 0 0-2.4 0L3.5 13.3"/><path d="m11.8 5 3.2 3.2"/></>
  :name==='copy'?<><rect x="6.5" y="6.5" width="10" height="10" rx="2"/><path d="M13.5 6.5v-1A2 2 0 0 0 11.5 3h-8A2 2 0 0 0 1.5 5v8a2 2 0 0 0 2 2h1"/></>
  :name==='pin'?<><path d="m7 3 6 0-.7 4 2.2 2.3v1.2h-4v5.8L10 17l-.5-.7v-5.8h-4V9.3L7.7 7z"/><path d="M7.7 7h4.6"/></>
  :name==='close'?<><path d="m5 5 10 10"/><path d="m15 5-10 10"/></>
  :name==='plus'?<><path d="M10 4v12"/><path d="M4 10h12"/></>
  :name==='minus'?<path d="M4 10h12"/>
  :name==='chevronDown'?<path d="m5 7.5 5 5 5-5"/>
  :name==='chevronRight'?<path d="m7.5 5 5 5-5 5"/>
  :name==='locate'?<><circle cx="10" cy="10" r="4"/><circle cx="10" cy="10" r="1" fill="currentColor" stroke="none"/><path d="M10 2v2M10 16v2M2 10h2M16 10h2"/></>
  :name==='fit'?<><path d="M8 3H3v5M12 3h5v5M8 17H3v-5M12 17h5v-5"/></>
  :name==='details'?<><circle cx="10" cy="10" r="7"/><path d="M10 9v5"/><path d="M10 6.2h.01"/></>
  :name==='canvas'?<><rect x="3" y="3" width="14" height="14" rx="2"/><path d="M8 3v14M8 9h9"/></>
  :name==='play'?<path d="m7 4 9 6-9 6z" fill="currentColor" stroke="none"/>
  :name==='settings'?<><circle cx="10" cy="10" r="2.7"/><path d="M10 2.5v2M10 15.5v2M2.5 10h2M15.5 10h2M4.7 4.7l1.4 1.4M13.9 13.9l1.4 1.4M15.3 4.7l-1.4 1.4M6.1 13.9l-1.4 1.4"/></>
  :name==='workflow'?<><rect x="2.5" y="3" width="5" height="4" rx="1"/><rect x="12.5" y="13" width="5" height="4" rx="1"/><rect x="12.5" y="3" width="5" height="4" rx="1"/><path d="M7.5 5h5M5 7v8h7.5"/></>
  :<path d="m3.5 10.2 4 4L16.5 5"/>}
 </svg>;
}
