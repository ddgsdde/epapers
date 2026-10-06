import {useEffect,useState} from 'react';
import {api} from './api';
import type {Layout,Preview,TemplateOption} from './types';
export function TemplateGallery({profile,modes,choose}:{profile:string;modes:TemplateOption[];choose:(id:string)=>void}){
 const [images,setImages]=useState<Record<string,string>>({});
 useEffect(()=>{const controller=new AbortController();setImages({});let index=0;async function worker(){while(index<modes.length){const mode=modes[index++];try{const layout=await api<Layout>(`templates/${profile}/${mode.id}`,'GET',undefined,controller.signal);const preview=await api<Preview>('preview','POST',layout,controller.signal);if(!controller.signal.aborted)setImages(old=>({...old,[mode.id]:preview.image}));}catch(e){if(!controller.signal.aborted)console.warn('Template preview unavailable',mode.id);}}}void Promise.all([worker(),worker(),worker()]);return()=>controller.abort();},[profile,modes]);
 return <div className="template-grid">{modes.map(mode=><button className="template-card" key={mode.id} onClick={()=>choose(mode.id)}><div className="template-art">{images[mode.id]?<img src={images[mode.id]} alt={`${mode.name}墨水屏预览`}/>:<span className="empty-small">加载预览…</span>}</div><h3>{mode.name}</h3><p>{mode.description}</p></button>)}</div>;
}
