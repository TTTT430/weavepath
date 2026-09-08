import type{ReactNode}from'react';
import{AppIcon,type AppIconName}from'./AppIcon';

export type ActivityPhase='connecting'|'waiting'|'receiving'|'reconnecting';

interface ActivityStatusProps{
 label:string
 detail?:string
 phase?:ActivityPhase
 tone?:'active'|'error'|'muted'
 compact?:boolean
 children?:ReactNode
}

export function ActivityStatus({label,detail,phase='connecting',tone='active',compact=false,children}:ActivityStatusProps){
 const icon:AppIconName=tone==='error'?'warning':tone==='muted'?'check':phase==='reconnecting'?'retry':'activity';
 return <div className={`activity-status is-${tone} is-${phase}${compact?' is-compact':''}`} role={tone==='error'?'alert':'status'} aria-live="polite">
  <span className="activity-status__icon" aria-hidden="true"><AppIcon name={icon} size={15}/></span>
  <span className="activity-status__copy"><strong>{label}</strong>{detail&&<small><AppIcon name="clock" size={12}/>{detail}</small>}</span>
  {tone==='active'&&<span className="activity-status__pulse" aria-hidden="true"><i/><i/><i/></span>}
  {children&&<span className="activity-status__actions">{children}</span>}
 </div>;
}
