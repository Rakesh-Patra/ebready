import {useEffect,useRef,useState} from 'react';
import {Menu} from 'lucide-react';
export default function MobileMenu({items}:{items:Array<{label:string;href?:string;onSelect?:()=>void}>}){
 const ref=useRef<HTMLDetailsElement>(null),[open,setOpen]=useState(false);
 function close(){if(ref.current)ref.current.open=false;}
 useEffect(()=>{const outside=(event:PointerEvent)=>{if(ref.current&&!ref.current.contains(event.target as Node))close();};const escape=(event:KeyboardEvent)=>{if(event.key==='Escape'&&ref.current?.open){close();ref.current.querySelector('summary')?.focus();}};document.addEventListener('pointerdown',outside);document.addEventListener('keydown',escape);return()=>{document.removeEventListener('pointerdown',outside);document.removeEventListener('keydown',escape);};},[]);
 return <details className="mobile-menu" ref={ref} onToggle={event=>setOpen(event.currentTarget.open)}><summary aria-label="Open navigation menu"><Menu size={22}/></summary>{open&&<nav className="mobile-menu-panel" aria-label="Mobile navigation">{items.map(item=>item.href?<a key={item.label} href={item.href} onClick={close}>{item.label}</a>:<button key={item.label} onClick={()=>{close();item.onSelect?.();}}>{item.label}</button>)}</nav>}</details>;
}
