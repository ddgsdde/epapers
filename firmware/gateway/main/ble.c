#include "gateway.h"
#include "ep_core.h"
#include <string.h>
#include <stdlib.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"
#include "freertos/semphr.h"
#include "nimble/nimble_port.h"
#include "nimble/nimble_port_freertos.h"
#include "host/ble_hs.h"
#include "host/ble_gap.h"
#include "host/ble_gatt.h"
#include "mbedtls/base64.h"
#include "esp_random.h"
#include "esp_timer.h"

static QueueHandle_t commands;
static SemaphoreHandle_t completion;
static uint8_t own_type;
static volatile uint16_t connection=BLE_HS_CONN_HANDLE_NONE;
static uint16_t service_start,service_end,rx_handle,tx_handle,discovered_handle;
static volatile int operation_error;
static uint8_t response[80];static size_t response_len;
static ble_uuid_any_t service_uuid,rx_uuid,tx_uuid;
static bool ready;
static const char *ota_phase;static uint8_t ota_result;
typedef struct {ble_addr_t addr;char text[18];int rssi;int64_t last_seen,last_poll;} seen_device;
static seen_device seen[20];static unsigned seen_count;
static int gap_event(struct ble_gap_event *event,void *arg);
static void scan(void){if(!ready||connection!=BLE_HS_CONN_HANDLE_NONE)return;struct ble_gap_disc_params params={.passive=0,.itvl=160,.window=48,.filter_duplicates=1};ble_gap_disc(own_type,5000,&params,gap_event,0);}
static void signal(int error){operation_error=error;xSemaphoreGive(completion);}
static bool wait_for(int rc,int timeout){if(rc){operation_error=rc;return false;}if(xSemaphoreTake(completion,pdMS_TO_TICKS(timeout))!=pdTRUE){operation_error=BLE_HS_ETIMEOUT;return false;}return operation_error==0;}
static void clear_wait(void){while(xSemaphoreTake(completion,0)==pdTRUE){}operation_error=0;}
static int write_cb(uint16_t conn,const struct ble_gatt_error *error,struct ble_gatt_attr *attr,void *arg){signal(error->status);return 0;}
static int read_cb(uint16_t conn,const struct ble_gatt_error *error,struct ble_gatt_attr *attr,void *arg){
 response_len=0;if(!error->status&&attr&&attr->om){response_len=OS_MBUF_PKTLEN(attr->om);if(response_len<=sizeof(response))os_mbuf_copydata(attr->om,0,response_len,response);else response_len=0;}signal(error->status);return 0;
}
static int mtu_cb(uint16_t conn,const struct ble_gatt_error *error,uint16_t mtu,void *arg){signal(error->status);return 0;}
static int service_cb(uint16_t conn,const struct ble_gatt_error *error,const struct ble_gatt_svc *svc,void *arg){if(!error->status){service_start=svc->start_handle;service_end=svc->end_handle;}else signal(error->status==BLE_HS_EDONE?0:error->status);return 0;}
static int char_cb(uint16_t conn,const struct ble_gatt_error *error,const struct ble_gatt_chr *chr,void *arg){if(!error->status)discovered_handle=chr->val_handle;else signal(error->status==BLE_HS_EDONE?0:error->status);return 0;}
static int gap_event(struct ble_gap_event *event,void *arg){
 switch(event->type){
 case BLE_GAP_EVENT_CONNECT:connection=event->connect.status?BLE_HS_CONN_HANDLE_NONE:event->connect.conn_handle;signal(event->connect.status);break;
 case BLE_GAP_EVENT_DISCONNECT:connection=BLE_HS_CONN_HANDLE_NONE;signal(BLE_HS_ENOTCONN);break;
 case BLE_GAP_EVENT_DISC:{struct ble_hs_adv_fields fields;if(ble_hs_adv_parse_fields(&fields,event->disc.data,event->disc.length_data))break;
  bool ep=false;for(unsigned i=0;i<fields.num_uuids128;i++)if(!ble_uuid_cmp((ble_uuid_t*)&fields.uuids128[i],&service_uuid.u))ep=true;
  if(fields.name_len>=7&&!memcmp(fields.name,"epapers",7))ep=true;
  if(!ep)break;char address[18];const uint8_t *a=event->disc.addr.val;snprintf(address,sizeof(address),"%02X:%02X:%02X:%02X:%02X:%02X",a[5],a[4],a[3],a[2],a[1],a[0]);
  unsigned i;for(i=0;i<seen_count;i++)if(!strcmp(seen[i].text,address))break;
  if(i==seen_count){if(seen_count>=20)break;seen_count++;strlcpy(seen[i].text,address,sizeof(seen[i].text));}
  seen[i].addr=event->disc.addr;seen[i].rssi=event->disc.rssi;
  int64_t now=esp_timer_get_time();if(now-seen[i].last_seen<20000000)break;seen[i].last_seen=now;
  cJSON *data=cJSON_CreateObject();char key[40];snprintf(key,sizeof(key),"da-%02x%02x%02x%02x%02x%02x",a[5],a[4],a[3],a[2],a[1],a[0]);cJSON_AddStringToObject(data,"type","telemetry");cJSON_AddStringToObject(data,"device_id",key);cJSON_AddStringToObject(data,"address",address);cJSON_AddNumberToObject(data,"rssi",event->disc.rssi);cJSON_AddStringToObject(data,"name","新发现的墨水屏");gateway_emit(data);break;}
 default:break;
 }return 0;
}
static bool connect_address(const char *text){
 ble_addr_t address={.type=BLE_ADDR_PUBLIC};unsigned values[6];
 if(sscanf(text,"%02x:%02x:%02x:%02x:%02x:%02x",&values[5],&values[4],&values[3],&values[2],&values[1],&values[0])!=6)return false;
 for(unsigned i=0;i<6;i++)address.val[i]=(uint8_t)values[i];bool known=false;for(unsigned i=0;i<seen_count;i++)if(!strcasecmp(seen[i].text,text)){address=seen[i].addr;known=true;}
 /* First operation after gateway reboot may precede discovery. Try both address types. */
 if(!known&&(address.val[5]&0xc0)==0xc0)address.type=BLE_ADDR_RANDOM;
 ble_gap_disc_cancel();bool connected=false;ota_phase="连接";for(unsigned attempt=0;attempt<(known?1:2);attempt++){clear_wait();if(wait_for(ble_gap_connect(own_type,&address,10000,NULL,gap_event,NULL),12000)){connected=true;break;}ble_gap_conn_cancel();vTaskDelay(pdMS_TO_TICKS(200));if(connection!=BLE_HS_CONN_HANDLE_NONE)break;address.type=address.type==BLE_ADDR_RANDOM?BLE_ADDR_PUBLIC:BLE_ADDR_RANDOM;}if(!connected)return false;
 ota_phase="MTU 协商";clear_wait();if(!wait_for(ble_gattc_exchange_mtu(connection,mtu_cb,NULL),5000)||ble_att_mtu(connection)<27)return false;
 ota_phase="显示服务发现";service_start=service_end=0;clear_wait();if(!wait_for(ble_gattc_disc_svc_by_uuid(connection,&service_uuid.u,service_cb,NULL),5000)||!service_start)return false;
 discovered_handle=0;clear_wait();if(!wait_for(ble_gattc_disc_chrs_by_uuid(connection,service_start,service_end,&rx_uuid.u,char_cb,NULL),5000)||!discovered_handle)return false;rx_handle=discovered_handle;
 discovered_handle=0;clear_wait();if(!wait_for(ble_gattc_disc_chrs_by_uuid(connection,service_start,service_end,&tx_uuid.u,char_cb,NULL),5000)||!discovered_handle)return false;tx_handle=discovered_handle;return true;
}
static uint32_t read32(const uint8_t *p){return (uint32_t)p[0]|(uint32_t)p[1]<<8|(uint32_t)p[2]<<16|(uint32_t)p[3]<<24;}
static bool send_packet(uint8_t command,uint32_t transaction,uint16_t sequence,const uint8_t *payload,uint16_t length,bool *applied){
 uint8_t packet[208];size_t size=ep_packet(packet,sizeof(packet),command,transaction,sequence,payload,length);if(!size)return false;
 for(int attempt=0;attempt<3;attempt++){
  clear_wait();if(!wait_for(ble_gattc_write_flat(connection,rx_handle,packet,size,write_cb,0),20000))continue;
  clear_wait();if(!wait_for(ble_gattc_read(connection,tx_handle,read_cb,0),20000))continue;
  if(response_len<20||response[0]!='E'||response[1]!='P'||response[2]!=1||response[3]!=(command|0x80)||read32(response+4)!=transaction||response[8]!=(sequence&255)||response[9]!=(sequence>>8))continue;
  unsigned n=response[10]|response[11]<<8;if(n<2||response_len!=16+n||ep_crc32(response,12+n)!=read32(response+12+n))continue;
  if(response[12]==EP_BUSY){
   int polls=0;
   do{vTaskDelay(pdMS_TO_TICKS(100));clear_wait();if(!wait_for(ble_gattc_read(connection,tx_handle,read_cb,0),2000))break;
    if(response_len<20||read32(response+4)!=transaction||response[3]!=(command|0x80))break;
    unsigned size=response[10]|response[11]<<8;if(response_len!=16+size||ep_crc32(response,12+size)!=read32(response+12+size))break;
   }while(response[12]==EP_BUSY&&++polls<200);
  }
  if(response[12]!=EP_OK)return false;*applied=response[13]!=0;return true;
 }return false;
}
static bool sync_time(cJSON *command){uint64_t epoch;int32_t offset;gateway_time(&epoch,&offset);if(!epoch)return false;cJSON *zone=cJSON_GetObjectItem(cJSON_GetObjectItem(command,"time"),"offset");if(cJSON_IsNumber(zone))offset=zone->valueint;uint8_t p[12];for(int i=0;i<8;i++)p[i]=(uint8_t)(epoch>>(8*i));for(int i=0;i<4;i++)p[i+8]=(uint8_t)((uint32_t)offset>>(8*i));bool applied=false;return send_packet(EP_TIME,esp_random(),0,p,12,&applied)&&applied;}
static const char *string(cJSON *data,const char *key){cJSON *value=cJSON_GetObjectItem(data,key);return cJSON_IsString(value)?value->valuestring:NULL;}
static bool configure(cJSON *command){const char *profile=string(command,"profile");uint8_t p[2]={0,0};if(profile){if(!strcmp(profile,"HINK-E0213A41"))p[0]=1;else if(!strcmp(profile,"HINK-E0213A55"))p[0]=2;else if(!strcmp(profile,"OPM021B1"))p[0]=3;}bool applied=false;return p[0]&&send_packet(EP_CONFIG,esp_random(),0,p,2,&applied)&&applied;}
static void report(cJSON *command,bool synced){
 if(response_len<36)return;const char *key=string(command,"device_id"),*address=string(command,"address");if(!key)return;
 cJSON *data=cJSON_CreateObject();cJSON_AddStringToObject(data,"type","telemetry");cJSON_AddStringToObject(data,"device_id",key);cJSON_AddStringToObject(data,"address",address);if(response_len>=58){char version[17]={0};memcpy(version,response+34,16);cJSON_AddStringToObject(data,"firmware",version);}else cJSON_AddStringToObject(data,"firmware","0.1.0-ram");
 unsigned mv=response[16]|response[17]<<8;if(mv!=65535)cJSON_AddNumberToObject(data,"battery_mv",mv);else cJSON_AddNullToObject(data,"battery_mv");if(response[18]!=255)cJSON_AddNumberToObject(data,"battery_percent",response[18]);else cJSON_AddNullToObject(data,"battery_percent");
 if(response_len>=38)cJSON_AddNumberToObject(data,"supply_mv",response[32]|response[33]<<8);
 if(response_len>=64){
  int16_t temperature=(uint16_t)response[54]|(uint16_t)response[55]<<8;
  unsigned temp_age=response[56]|response[57]<<8,supply_age=response[58]|response[59]<<8;
  uint64_t epoch;int32_t offset;gateway_time(&epoch,&offset);
  if(temperature!=INT16_MIN&&temperature>=-2500&&temperature<=8500)cJSON_AddNumberToObject(data,"temperature_centi",temperature);else cJSON_AddNullToObject(data,"temperature_centi");
  if(epoch&&temp_age!=65535&&temperature!=INT16_MIN)cJSON_AddNumberToObject(data,"temperature_sampled_at",epoch-temp_age);else cJSON_AddNullToObject(data,"temperature_sampled_at");
  if(epoch&&supply_age!=65535)cJSON_AddNumberToObject(data,"battery_sampled_at",epoch-supply_age);else cJSON_AddNullToObject(data,"battery_sampled_at");
 }
 for(unsigned i=0;i<seen_count;i++)if(address&&!strcasecmp(seen[i].text,address)){seen[i].last_poll=esp_timer_get_time();break;}
 if(synced){uint64_t epoch;int32_t offset;gateway_time(&epoch,&offset);cJSON_AddNumberToObject(data,"last_sync",epoch);}
 if(response[20]){char error[64];snprintf(error,sizeof(error),"panel diagnostic %u (LUT %u, pins %u)",response[20],response[22]|response[23]<<8,response[21]);cJSON_AddStringToObject(data,"error",error);}else cJSON_AddNullToObject(data,"error");
 cJSON *caps=cJSON_AddObjectToObject(data,"capabilities");cJSON_AddBoolToObject(caps,"suota",response_len>=58&&read32(response+50)!=0);cJSON_AddBoolToObject(caps,"clock_synced",response[14]);cJSON_AddBoolToObject(caps,"needs_sync",response[15]);cJSON_AddNumberToObject(caps,"lut_size",response[22]|response[23]<<8);cJSON_AddNumberToObject(caps,"pin_group",response[21]);cJSON_AddNumberToObject(caps,"model",response[19]);
 if(response_len>=80){cJSON_AddNumberToObject(caps,"full_refreshes",read32(response+60));cJSON_AddNumberToObject(caps,"partial_refreshes",read32(response+64));cJSON_AddNumberToObject(caps,"refresh_reason",response[68]);}
 gateway_emit(data);
}
static void poll_telemetry(void){
 if(!ready||ota_busy())return;int64_t now=esp_timer_get_time();
 for(unsigned i=0;i<seen_count;i++){
  if(now-seen[i].last_seen>90000000||(seen[i].last_poll&&now-seen[i].last_poll<900000000))continue;
  seen[i].last_poll=now;bool applied=false;
  if(connect_address(seen[i].text)&&send_packet(EP_STATUS,esp_random(),0,NULL,0,&applied)){
   cJSON *data=cJSON_CreateObject();char key[40];const uint8_t *a=seen[i].addr.val;
   snprintf(key,sizeof(key),"da-%02x%02x%02x%02x%02x%02x",a[5],a[4],a[3],a[2],a[1],a[0]);
   cJSON_AddStringToObject(data,"device_id",key);cJSON_AddStringToObject(data,"address",seen[i].text);report(data,false);cJSON_Delete(data);
  }
  if(connection!=BLE_HS_CONN_HANDLE_NONE){ble_gap_terminate(connection,BLE_ERR_REM_USER_CONN_TERM);vTaskDelay(pdMS_TO_TICKS(150));}
  break; /* One short connection per idle pass; display/OTA jobs take priority. */
 }
}
static uint16_t ota_char(const char *uuid){ble_uuid_any_t value;ble_uuid_from_str(&value,uuid);discovered_handle=0;clear_wait();return wait_for(ble_gattc_disc_chrs_by_uuid(connection,service_start,service_end,&value.u,char_cb,NULL),5000)?discovered_handle:0;}
static bool ota_write(uint16_t handle,const void *data,size_t size){clear_wait();int rc=ble_gattc_write_flat(connection,handle,data,size,write_cb,NULL);if(rc){operation_error=rc;return false;}return wait_for(rc,15000);}
static bool ota_status_read(uint16_t handle,uint8_t expected){for(unsigned i=0;i<50;i++){vTaskDelay(pdMS_TO_TICKS(20));clear_wait();if(!wait_for(ble_gattc_read(connection,handle,read_cb,NULL),3000)||response_len!=1)return false;ota_result=response[0];if(response[0]==expected)return true;if(response[0]!=0&&response[0]!=1&&response[0]!=2&&response[0]!=0x10)return false;}return false;}
static bool update_tag(cJSON *data,const uint8_t *image,size_t length){
 const char *id=string(data,"id"),*address=string(data,"address"),*identity=string(data,"identity");
 ota_phase="连接";ota_result=255;if(!connect_address(address))return false;
 ota_phase="服务发现";ble_uuid16_t suota=BLE_UUID16_INIT(0xfef5);service_start=service_end=0;clear_wait();if(!wait_for(ble_gattc_disc_svc_by_uuid(connection,&suota.u,service_cb,NULL),5000)||!service_start)return false;
 ota_phase="特征发现";uint16_t mem=ota_char("8082caa8-41a6-4021-91c6-56f9b954cc34"),gpio=ota_char("724249f0-5ec3-4b5f-8804-42345af08651"),patch_len=ota_char("9d84b9a3-000c-49d8-9183-855b673fda31"),patch=ota_char("457871e8-d516-4ca1-9116-57d0b17b9cb2"),status=ota_char("5f78df94-798c-46f5-990a-b3eb6a065c88");
 if(!mem||!gpio||!patch_len||!patch||!status)return false;
 ota_phase="启动升级";uint32_t dev=0x13000000,map=0x05060300;if(!ota_write(mem,&dev,4)||!ota_status_read(status,0x10))return false;ota_phase="Flash 引脚";if(!ota_write(gpio,&map,4))return false;
 uint8_t xor=0;for(size_t i=0;i<length;i++)xor^=image[i];size_t total=length+1;uint8_t block[512];unsigned chunk=ble_att_mtu(connection)-3;if(chunk>244)chunk=244;uint16_t limit=ota_char("42c3dfdd-77be-4d9c-8454-8f875267fb3b");unsigned supported=20;if(limit){clear_wait();if(wait_for(ble_gattc_read(connection,limit,read_cb,NULL),3000)&&response_len==2)supported=response[0]|response[1]<<8;}if(!supported||supported>244)return false;if(chunk>supported)chunk=supported;
 for(size_t offset=0;offset<total;offset+=512){uint16_t n=total-offset<512?total-offset:512;
  for(unsigned i=0;i<n;i++)block[i]=offset+i<length?image[offset+i]:xor;
  ota_phase="块长度";if(!ota_write(patch_len,&n,2))return false;
  ota_phase="写入数据";for(unsigned pos=0;pos<n;pos+=chunk){unsigned count=n-pos<chunk?n-pos:chunk;if(!ota_write(patch,block+pos,count))return false;}
  ota_phase="块校验";if(!ota_status_read(status,2))return false;
  gateway_ota_status(id,"flashing",offset+n,75+(offset+n)*20.0f/total,NULL,NULL);
 }
 ota_phase="镜像校验";dev=0xfe000000;if(!ota_write(mem,&dev,4)||!ota_status_read(status,2))return false;
 ota_phase="重启";gateway_ota_status(id,"rebooting",total,97,NULL,NULL);dev=0xfd000000;/* SDK may reboot before the ATT write response. The new CRC readback is mandatory below. */ota_write(mem,&dev,4);
 if(connection!=BLE_HS_CONN_HANDLE_NONE)ble_gap_terminate(connection,BLE_ERR_REM_USER_CONN_TERM);
 vTaskDelay(pdMS_TO_TICKS(1800));
 ota_phase="重启身份核对";for(unsigned attempt=0;attempt<8;attempt++){
  if(connect_address(address)){bool applied=false;if(send_packet(EP_INFO,esp_random(),0,NULL,0,&applied)&&response_len>=58){char actual[9];snprintf(actual,sizeof(actual),"%08lx",(unsigned long)read32(response+50));report(data,false);if(!strcmp(actual,identity)){gateway_ota_status(id,"verified",length,100,NULL,actual);return true;}return false;}}
  if(connection!=BLE_HS_CONN_HANDLE_NONE)ble_gap_terminate(connection,BLE_ERR_REM_USER_CONN_TERM);vTaskDelay(pdMS_TO_TICKS(1000));
 }return false;
}
static void worker(void *arg){
 while(true){cJSON *data;if(xQueueReceive(commands,&data,pdMS_TO_TICKS(5000))!=pdTRUE){poll_telemetry();scan();continue;}
  if(string(data,"kind")&&!strcmp(string(data,"kind"),"firmware")){
   uint8_t *image=(uint8_t*)(uintptr_t)cJSON_GetObjectItem(data,"image_pointer")->valuedouble;size_t length=(size_t)cJSON_GetObjectItem(data,"image_length")->valuedouble;
   if(!update_tag(data,image,length)){char detail[128];snprintf(detail,sizeof(detail),"SUOTA %s失败（状态 0x%02x，BLE %d）",ota_phase?ota_phase:"未知阶段",ota_result,operation_error);gateway_ota_status(string(data,"id"),"failed",0,0,detail,NULL);}
   free(image);if(connection!=BLE_HS_CONN_HANDLE_NONE)ble_gap_terminate(connection,BLE_ERR_REM_USER_CONN_TERM);cJSON_Delete(data);vTaskDelay(pdMS_TO_TICKS(200));scan();continue;
  }
  const char *job=string(data,"job_id"),*address=string(data,"address"),*kind=string(data,"kind");bool ok=false,synced=false;const char *error="蓝牙连接或服务发现失败";
  if(job&&kind&&address&&ready&&connect_address(address)){
   gateway_ack(job,"transferring",NULL);bool applied=false;
   if(!strcmp(kind,"display")){
    error="显示包传输或 MCU 刷新失败";cJSON *payload=cJSON_GetObjectItem(data,"payload");const char *encoded=string(payload,"blob");size_t length=0;
    if(encoded){uint8_t *blob=malloc(EP_MAX_BLOB);if(blob&&mbedtls_base64_decode(blob,EP_MAX_BLOB,&length,(const unsigned char*)encoded,strlen(encoded))==0&&ep_blob_valid(blob,length)){
      ok=configure(data)&&sync_time(data);synced=ok;uint32_t transaction=esp_random();uint8_t begin[8];uint32_t crc=ep_crc32(blob,length);for(int i=0;i<4;i++){begin[i]=(uint8_t)(length>>(8*i));begin[i+4]=(uint8_t)(crc>>(8*i));}
      if(ok)ok=send_packet(EP_BEGIN,transaction,0,begin,8,&applied);unsigned chunk=ble_att_mtu(connection)-19;if(chunk>192)chunk=192;uint16_t sequence=1;
      for(size_t pos=0;ok&&pos<length;pos+=chunk){unsigned n=length-pos<chunk?length-pos:chunk;ok=send_packet(EP_DATA,transaction,sequence++,blob+pos,n,&applied);}
      if(ok){gateway_ack(job,"received",NULL);ok=send_packet(EP_COMMIT,transaction,sequence,NULL,0,&applied)&&applied;}
     }free(blob);}
   }else if(!strcmp(kind,"sync_time")){ok=sync_time(data);synced=ok;if(ok)gateway_ack(job,"received",NULL);}
   else if(!strcmp(kind,"status")){ok=send_packet(EP_STATUS,esp_random(),0,NULL,0,&applied);if(ok)gateway_ack(job,"received",NULL);}
   else if(!strcmp(kind,"full_refresh")){ok=sync_time(data);synced=ok;if(ok){gateway_ack(job,"received",NULL);ok=send_packet(EP_REFRESH,esp_random(),0,NULL,0,&applied)&&applied;}}
   else {error="固件暂不支持此操作";}
   report(data,synced);
  }
  if(job)gateway_ack(job,ok?"applied":"failed",ok?NULL:error);
  if(connection!=BLE_HS_CONN_HANDLE_NONE){ble_gap_terminate(connection,BLE_ERR_REM_USER_CONN_TERM);vTaskDelay(pdMS_TO_TICKS(150));}
  cJSON_Delete(data);scan();
 }
}
void ble_discover(void){scan();}
bool ble_busy(void){return connection!=BLE_HS_CONN_HANDLE_NONE||uxQueueMessagesWaiting(commands)>0;}
bool ble_update(cJSON *meta,uint8_t *image,size_t length){cJSON_DeleteItemFromObject(meta,"kind");cJSON_AddStringToObject(meta,"kind","firmware");cJSON_AddNumberToObject(meta,"image_pointer",(uintptr_t)image);cJSON_AddNumberToObject(meta,"image_length",length);return xQueueSend(commands,&meta,0)==pdTRUE;}
void ble_command(cJSON *data){if(ota_busy()){const char *job=string(data,"job_id");if(job)gateway_ack(job,"failed","网关正在接收固件");return;}cJSON *copy=cJSON_Duplicate(data,true);if(!copy||xQueueSend(commands,&copy,0)!=pdTRUE){const char *job=string(data,"job_id");if(job)gateway_ack(job,"failed","网关队列已满");cJSON_Delete(copy);}}
static void host_task(void *arg){nimble_port_run();nimble_port_freertos_deinit();}
static void on_sync(void){ble_hs_id_infer_auto(0,&own_type);ready=true;scan();}
void ble_start(void){
 commands=xQueueCreate(20,sizeof(cJSON*));completion=xSemaphoreCreateBinary();
 ble_uuid_from_str(&service_uuid,EP_SERVICE_UUID);ble_uuid_from_str(&rx_uuid,EP_RX_UUID);ble_uuid_from_str(&tx_uuid,EP_TX_UUID);
 ESP_ERROR_CHECK(nimble_port_init());ble_hs_cfg.sync_cb=on_sync;ble_att_set_preferred_mtu(247);nimble_port_freertos_init(host_task);xTaskCreate(worker,"ble-jobs",8192,0,5,0);
}
