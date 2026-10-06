import {useEffect,useRef,useState} from 'react';
import {api} from './api';
type Connection={base_url:string;key_configured:boolean;managed:boolean};
export function HAConnection({connected,updated,report}:{connected:boolean;updated:()=>void;report:(err:unknown)=>void}){
 const [config,setConfig]=useState<Connection|null>(null),[url,setUrl]=useState(''),[busy,setBusy]=useState(false),[saved,setSaved]=useState(false);
 const keyInput=useRef<HTMLInputElement>(null);
 useEffect(()=>{let active=true;void api<Connection>('ha/config').then(value=>{if(active){setConfig(value);setUrl(value.base_url);}}).catch(report);return()=>{active=false;};},[report]);
 async function save(){
  const key=keyInput.current?.value??'';
  if(keyInput.current)keyInput.current.value='';
  setBusy(true);setSaved(false);
  try{const value=await api<Connection>('ha/config','PUT',{base_url:url,...(key.trim()?{api_key:key}:{})});setConfig(value);setUrl(value.base_url);setSaved(true);updated();}catch(err){report(err);}finally{setBusy(false);}
 }
 return <section className="ha-connection"><h2>Home Assistant 连接</h2><form onSubmit={e=>{e.preventDefault();void save();}} autoComplete="off"><label>Base URL<input type="url" required placeholder="http://10.0.0.2:8123" value={url} disabled={busy||config?.managed} onChange={e=>{setUrl(e.target.value);setSaved(false);}}/></label><label>API Key<input ref={keyInput} type="password" autoComplete="off" name="ha-new-key" disabled={busy||config?.managed} placeholder={config?.key_configured?'已配置；留空保留现有 Key':'填写 HA 长期访问令牌'}/><small>{config?.key_configured?'Key 已配置，保存后不可查看。':'保存后不可查看。'}</small></label><div className="button-row"><button className="primary" disabled={busy||!config||config.managed}>{busy?'保存中…':'保存并连接'}</button><span className="hint" role="status">{saved?'已保存 · ':''}{connected?'已连接':config?.key_configured?'等待连接':'尚未配置'}</span></div></form></section>;
}
