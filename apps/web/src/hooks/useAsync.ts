import {useCallback,useState} from 'react';
export function useAsync(){const [busy,setBusy]=useState(false),[error,setError]=useState(''); const run=useCallback(async<T,>(fn:()=>Promise<T>)=>{setBusy(true);setError('');try{return await fn()}catch(e){setError(e instanceof Error?e.message:String(e));throw e}finally{setBusy(false)}},[]);return{busy,error,setError,run}}
