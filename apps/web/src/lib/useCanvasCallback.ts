import {useCallback, useLayoutEffect, useRef} from 'react';

// Keep node data stable while letting event handlers see current page state.
export function useCanvasCallback<T extends unknown[]>(callback: ((...args:T)=>void)|undefined) {
 const ref=useRef(callback);
 useLayoutEffect(()=>{ref.current=callback},[callback]);
 return useCallback((...args:T)=>ref.current?.(...args),[]);
}
