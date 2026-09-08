import{cleanup,render}from'@testing-library/react';
import{afterEach,describe,expect,it}from'vitest';
import{AppIcon,type AppIconName}from'./AppIcon';

afterEach(cleanup);

describe('AppIcon',()=>{
 it('renders every shared control icon as a decorative, non-focusable SVG',()=>{
  const names:AppIconName[]=['edit','copy','check','pin','close','plus','minus','chevronDown','chevronRight','locate','fit','details','canvas','play','activity','retry','clock','warning','settings','model','workflow'];
  for(const name of names){
   const{container,unmount}=render(<AppIcon name={name}/>),svg=container.querySelector('svg');
   expect(svg).not.toBeNull();
   expect(svg).toHaveAttribute('aria-hidden','true');
   expect(svg).toHaveAttribute('focusable','false');
   expect(svg?.querySelector('path, rect, circle')).not.toBeNull();
   unmount();
  }
 });
});
