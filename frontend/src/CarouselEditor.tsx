import {ArrowUp,ArrowDown,Plus,Trash2,Eye} from 'lucide-react';
import {EntityInput} from './EntityPicker';
import type {CarouselEntry,Entity,Widget} from './types';

export function CarouselEditor({widget,entities,update,preview,previewIndex}:{widget:Widget;entities:Entity[];update:(patch:Partial<Widget>)=>void;preview:(index:number|null)=>void;previewIndex:number|null}) {
 const entries=widget.carousel_entries??[];
 function replace(index:number,patch:Partial<CarouselEntry>){update({carousel_entries:entries.map((entry,i)=>i===index?{...entry,...patch}:entry)});}
 function move(index:number,delta:number){const next=[...entries];[next[index],next[index+delta]]=[next[index+delta],next[index]];update({carousel_entries:next});}
 return <section className="component-carousel" aria-label="组件轮播设置">
  <div className="two-col"><label>每项停留（分钟）<input type="number" min="1" max="1440" step="1" value={widget.carousel_minutes??1} onChange={e=>update({carousel_minutes:+e.target.value})}/></label><label>小数位<input type="number" min="0" max="4" value={widget.decimals} onChange={e=>update({decimals:+e.target.value})}/></label></div>
  <label>单位<input value={widget.unit??''} placeholder="跟随各实体" onChange={e=>update({unit:e.target.value||null})}/></label>
  <div className="carousel-entry-list">{entries.map((entry,index)=><div className="carousel-entry" key={index}>
   <div className="carousel-entry-heading"><strong>第 {index+1} 项</strong><button className={previewIndex===index?'active':''} aria-label={`预览第 ${index+1} 项`} aria-pressed={previewIndex===index} onClick={()=>preview(index)}><Eye size={14}/></button><button aria-label={`第 ${index+1} 项上移`} disabled={index===0} onClick={()=>move(index,-1)}><ArrowUp size={14}/></button><button aria-label={`第 ${index+1} 项下移`} disabled={index===entries.length-1} onClick={()=>move(index,1)}><ArrowDown size={14}/></button><button aria-label={`移除第 ${index+1} 项`} onClick={()=>update({carousel_entries:entries.filter((_,i)=>i!==index)})}><Trash2 size={14}/></button></div>
   <EntityInput label={`轮播实体 ${index+1}`} value={entry.entity_id||null} entities={entities} change={entity_id=>replace(index,{entity_id})}/>
   <label>屏幕名称<input aria-label={`第 ${index+1} 项屏幕名称`} maxLength={60} value={entry.label} placeholder={String(entities.find(e=>e.entity_id===entry.entity_id)?.attributes.friendly_name??'例如：客厅温度')} onChange={e=>replace(index,{label:e.target.value})}/></label>
  </div>)}</div>
  <button className="carousel-add" disabled={entries.length>=8} onClick={()=>update({carousel_entries:[...entries,{entity_id:'',label:''}]})}><Plus size={15}/>添加轮播实体</button>
  {previewIndex!==null?<button className="carousel-add" onClick={()=>preview(null)}>恢复自动预览</button>:null}
  <p className="hint">标签与数值一起切换，其他区域保持固定。最多 8 项；断开网关后继续轮播，数值保留最近一次同步。</p>
 </section>;
}
