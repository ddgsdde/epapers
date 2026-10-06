import {useEffect,useRef,useState} from 'react';
import {AlignHorizontalDistributeCenter,AlignVerticalDistributeCenter} from 'lucide-react';
import type {Layout,Preview,Widget} from './types';
type Geometry=Pick<Widget,'x'|'y'|'width'|'height'>;
export function Canvas({layout,preview,selected,select,edit,beginMove,size}:{layout:Layout;preview:Preview|null;selected:string|null;select:(id:string|null)=>void;edit:(id:string,patch:Partial<Geometry>)=>void;beginMove:()=>void;size:[number,number]}){
 const viewport=useRef<HTMLDivElement>(null);
 const [fitScale,setFitScale]=useState(1);
 useEffect(()=>{const el=viewport.current;if(!el)return;const observer=new ResizeObserver(([entry])=>setFitScale(Math.max(1,Math.floor((Math.min(entry.contentRect.width,650)-10)/size[0]))));observer.observe(el);return()=>observer.disconnect();},[size[0]]);
 const [grid,setGrid]=useState(false),[snap,setSnap]=useState(false),[zoom,setZoom]=useState('fit');
 const drag=useRef<{id:string;initial:Geometry;cx:number;cy:number;scale:number;corner?:string}|null>(null);
 const names:Record<string,string>={text:'文本',entity:'实体数值',clock:'时钟',date:'日期',countdown:'倒计时',line:'分隔线',rect:'边框',qrcode:'二维码',image:'图片',chart:'趋势图',temperature:'温度',humidity:'湿度',trend:'趋势图',title:'标题',tag_temperature:'屏幕温度',entity_carousel:'组件轮播'};
 const selectedWidget=layout.widgets.find(w=>w.id===selected);
 function start(e:React.PointerEvent,w:Widget,corner?:string){e.preventDefault();e.stopPropagation();beginMove();select(w.id);(e.currentTarget as HTMLElement).focus();e.currentTarget.setPointerCapture(e.pointerId);drag.current={id:w.id,initial:{x:w.x,y:w.y,width:w.width,height:w.height},cx:e.clientX,cy:e.clientY,scale:e.currentTarget.closest('.screen')!.getBoundingClientRect().width/size[0],corner};}
 function change(e:React.PointerEvent){const d=drag.current;if(!d)return;const step=snap?4:1,dx=Math.round((e.clientX-d.cx)/d.scale/step)*step,dy=Math.round((e.clientY-d.cy)/d.scale/step)*step,i=d.initial;
  if(!d.corner){edit(d.id,{x:Math.max(0,Math.min(size[0]-i.width,i.x+dx)),y:Math.max(0,Math.min(size[1]-i.height,i.y+dy))});return;}
  const left=d.corner.includes('w')?Math.max(0,Math.min(i.x+i.width-4,i.x+dx)):i.x,top=d.corner.includes('n')?Math.max(0,Math.min(i.y+i.height-4,i.y+dy)):i.y;
  const right=d.corner.includes('e')?Math.max(i.x+4,Math.min(size[0],i.x+i.width+dx)):i.x+i.width,bottom=d.corner.includes('s')?Math.max(i.y+4,Math.min(size[1],i.y+i.height+dy)):i.y+i.height;
  edit(d.id,{x:left,y:top,width:right-left,height:bottom-top});
 }
 function align(axis:'x'|'y'){if(!selectedWidget)return;beginMove();edit(selectedWidget.id,{[axis]:Math.floor((size[axis==='x'?0:1]-selectedWidget[axis==='x'?'width':'height'])/2)});}
 return <div className="canvas-area"><div className="canvas-note"><div className="canvas-controls"><select aria-label="画布缩放" value={zoom} onChange={e=>setZoom(e.target.value)}><option value="fit">适应画布 ({fitScale*100}%)</option><option value="1">100%</option><option value="2">200%</option><option value="3">300%</option></select><label><input type="checkbox" checked={grid} onChange={e=>setGrid(e.target.checked)}/>网格</label><label><input type="checkbox" checked={snap} onChange={e=>setSnap(e.target.checked)}/>4px 吸附</label></div><span>{preview?`${preview.bytes.toLocaleString()} 字节`:''}</span></div>
 <div ref={viewport} className="canvas-viewport"><div className="screen-wrap" style={{width:`${size[0]*(zoom==='fit'?fitScale:Number(zoom))+10}px`,maxWidth:'none'}}><div className="screen" style={{aspectRatio:`${size[0]}/${size[1]}`}} onClick={e=>{if(e.target===e.currentTarget||(e.target as HTMLElement).classList.contains('pixel-preview'))select(null);}}>
 {preview?<img className="pixel-preview" src={preview.image} alt="墨水屏内容预览" draggable={false}/>:<div className="rendering">正在渲染…</div>}
 {grid?<div className="pixel-grid" style={{backgroundSize:`${4/size[0]*100}% ${4/size[1]*100}%`}}/>:null}
 {layout.widgets.map(w=><button key={w.id} type="button" className={`widget-hit ${selected===w.id?'selected':''}`} aria-label={`选择组件 ${w.id}`} title={w.id} style={{left:`${w.x/size[0]*100}%`,top:`${w.y/size[1]*100}%`,width:`${w.width/size[0]*100}%`,height:`${w.height/size[1]*100}%`}} onPointerDown={e=>start(e,w)} onPointerMove={change} onPointerUp={()=>{drag.current=null;}} onPointerCancel={()=>{drag.current=null;}} onKeyDown={e=>{if(!['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(e.key))return;e.preventDefault();beginMove();const n=e.shiftKey?5:1;edit(w.id,{x:Math.max(0,Math.min(size[0]-w.width,w.x+(e.key==='ArrowLeft'?-n:e.key==='ArrowRight'?n:0))),y:Math.max(0,Math.min(size[1]-w.height,w.y+(e.key==='ArrowUp'?-n:e.key==='ArrowDown'?n:0)))});}}>
 {selected===w.id?(['nw','ne','sw','se'] as const).map(c=><span key={c} role="slider" aria-label={`缩放 ${w.id} ${c}`} className={`resize-handle ${c}`} onPointerDown={e=>start(e,w,c)} onPointerMove={change} onPointerUp={e=>{e.stopPropagation();drag.current=null;}}/>):null}</button>)}
 </div></div></div><div className="canvas-caption"><span>{size[0]} × {size[1]} · 2.13 寸</span><div><button disabled={!selected} title="水平居中" aria-label="水平居中" onClick={()=>align('x')}><AlignHorizontalDistributeCenter size={16}/></button><button disabled={!selected} title="垂直居中" aria-label="垂直居中" onClick={()=>align('y')}><AlignVerticalDistributeCenter size={16}/></button></div></div>
 <div className="layer-strip" aria-label="页面图层">{layout.widgets.map(w=><button key={w.id} className={w.id===selected?'active':''} onClick={()=>select(w.id)}>{w.kind==='text'?w.text.slice(0,8)||'文本':names[w.id]??names[w.kind]}</button>)}</div>{preview?.warnings.map(w=><p key={w} className="warning">{w}</p>)}
 </div>;
}
