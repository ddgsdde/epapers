export async function api<T>(path:string,method='GET',body?:unknown,signal?:AbortSignal):Promise<T>{
 const token=sessionStorage.getItem('epapers-api-v1');
 const response=await fetch(`api/${path}`,{method,headers:{...(body?{'Content-Type':'application/json'}:{}),...(token?{Authorization:`Bearer ${token}`}:{})},body:body?JSON.stringify(body):undefined,signal});
 const data=await response.json();
 if(!response.ok){if(response.status===401)window.dispatchEvent(new Event('epapers-auth'));throw new Error(typeof data.detail==='string'?data.detail:JSON.stringify(data.detail));}
 return data;
}
export const time=(value:number|null)=>value?new Date(value*1000).toLocaleString('zh-CN',{hour12:false}):'尚无记录';
export const jobLabels:Record<string,string>={queued:'等待网关',transferring:'正在传输',received:'MCU 已接收',applied:'已确认刷新',failed:'失败',superseded:'已被新内容替代'};
export async function download(path:string,name:string){const token=sessionStorage.getItem('epapers-api-v1');const response=await fetch(`api/${path}`,{headers:token?{Authorization:`Bearer ${token}`}:{}});if(!response.ok)throw new Error('下载失败，请检查 API 密钥');const url=URL.createObjectURL(await response.blob());const link=document.createElement('a');link.href=url;link.download=name;document.body.appendChild(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),30000);}
