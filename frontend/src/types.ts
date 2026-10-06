export type CarouselEntry={entity_id:string;label:string};
export type Widget={id:string;kind:string;x:number;y:number;width:number;height:number;text:string;entity_id:string|null;attribute:string|null;font:string;font_size:number;font_weight:number;letter_spacing:number;history_hours:number;history_minutes:number|null;chart_style:string;chart_labels:boolean;chart_value:boolean;chart_grid:boolean;state_labels:boolean;align:string;decimals:number;unit:string|null;asset:string|null;inverted:boolean;deadline:number|null;carousel_entries:CarouselEntry[];carousel_minutes:number};
export type Layout={name:string;profile:string;rotation:number;inverted:boolean;timezone:string;widgets:Widget[]};
export type Device={id:string;name:string;profile:string;address:string|null;gateway_id:string|null;online:boolean;simulated:boolean;battery_mv:number|null;supply_mv:number|null;battery_percent:number|null;rssi:number|null;firmware:string|null;last_seen:number|null;last_refresh:number|null;last_content_check:number|null;last_sync:number|null;error:string|null;applied_revision:string|null;temperature_centi:number|null;temperature_sampled_at:number|null;battery_sampled_at:number|null;battery_source:string;refresh_interval:number;ha_name:string;ha_entity_prefix:string;area_name:string;ha_sync_status:string|null;capabilities:{suota?:boolean;full_refreshes?:number;partial_refreshes?:number;refresh_reason?:number}};
export type Entity={entity_id:string;state:string;attributes:Record<string,unknown>;domain?:string;area?:string|null;device?:string|null;category?:string|null};
export type Job={id:string;device_id:string;kind:string;revision:string;state:string;created:number;updated:number;error:string|null};
export type Event={ts:number;level:string;message:string;device_id:string|null};
export type Gateway={id:string;name?:string;area_name?:string;online:boolean;firmware:string;ota?:boolean;free_heap?:number;queue_depth?:number;wifi_rssi?:number;last_seen:number};
export type RemovedDevice=Pick<Device,'id'|'name'|'address'|'profile'>;
export type State={devices:Device[];removed_devices:RemovedDevice[];gateways:Gateway[];jobs:Job[];profiles:Record<string,{width:number;height:number;controller:string;qualified:boolean}>;ha:{connected:boolean;error:string|null;entities:number;confirmed_at:number};events:Event[];demo:boolean;ha_url:string;timezone:string};
export type Preview={image:string;bytes:number;warnings:string[];size:[number,number]};

export type FontOption={id:string;name:string;sample:string;weights:number[];preview:string};
export type TemplateOption={id:string;name:string;description:string};

export type Area={id:string;name:string};
export type DisplayProgram={pages:Layout[];enabled:boolean;interval_minutes:number;started_at:number};
