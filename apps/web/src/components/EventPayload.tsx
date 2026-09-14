import {memo,useState} from 'react';

export const EventPayload=memo(function EventPayload({label,value}:{label:string;value:unknown}){
 const[open,setOpen]=useState(false);
 return <details onToggle={event=>setOpen(event.currentTarget.open)}><summary>{label}</summary>{open&&<pre>{typeof value==='string'?value:JSON.stringify(value,null,2)}</pre>}</details>;
});
