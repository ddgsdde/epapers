#include "gateway.h"
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/semphr.h"
#include "nvs_flash.h"
#include "nvs.h"
#include "esp_event.h"
#include "esp_wifi.h"
#include "esp_netif.h"
#include "esp_timer.h"
#include "esp_mac.h"
#include "esp_websocket_client.h"
#include "esp_crt_bundle.h"
#include "esp_log.h"
#include "esp_ota_ops.h"
#include "esp_app_desc.h"

static char gateway_id[40];
static esp_websocket_client_handle_t websocket;
static SemaphoreHandle_t output_lock;
static uint64_t time_epoch;
static int64_t time_received;
static int32_t time_offset;
static char ws_rx[32768];
static size_t ws_used;
static void message(cJSON *data);

void gateway_time(uint64_t *epoch,int32_t *offset){
 *epoch=time_epoch?time_epoch+(esp_timer_get_time()-time_received)/1000000:0;*offset=time_offset;
}
void gateway_emit(cJSON *data){
 char *text=cJSON_PrintUnformatted(data);if(!text){cJSON_Delete(data);return;}
 xSemaphoreTake(output_lock,portMAX_DELAY);
 if(websocket&&esp_websocket_client_is_connected(websocket))esp_websocket_client_send_text(websocket,text,strlen(text),pdMS_TO_TICKS(3000));
 printf("EP:%s\n",text);fflush(stdout);
 xSemaphoreGive(output_lock);cJSON_free(text);cJSON_Delete(data);
}
void gateway_ack(const char *job,const char *state,const char *error){cJSON *data=cJSON_CreateObject();cJSON_AddStringToObject(data,"type","ack");cJSON_AddStringToObject(data,"job_id",job);cJSON_AddStringToObject(data,"state",state);if(error)cJSON_AddStringToObject(data,"error",error);gateway_emit(data);}
static void hello(void){const esp_app_desc_t *app=esp_app_get_description();char identity[65];for(unsigned i=0;i<32;i++)snprintf(identity+i*2,3,"%02x",app->app_elf_sha256[i]);cJSON *data=cJSON_CreateObject();cJSON_AddStringToObject(data,"type","hello");cJSON_AddStringToObject(data,"gateway_id",gateway_id);cJSON_AddStringToObject(data,"firmware",app->version);cJSON_AddBoolToObject(data,"ota",true);cJSON_AddStringToObject(data,"build_id",identity);gateway_emit(data);}
static void socket_event(void *arg,esp_event_base_t base,int32_t event,void *raw){
 if(event==WEBSOCKET_EVENT_CONNECTED){ws_used=0;hello();}
 if(event==WEBSOCKET_EVENT_DATA){esp_websocket_event_data_t *data=raw;if(data->op_code!=1&&data->op_code!=0)return;
  if(data->payload_len>=sizeof(ws_rx)||data->payload_offset+data->data_len>=sizeof(ws_rx)){ws_used=0;return;}
  if(data->payload_offset==0)ws_used=0;
  memcpy(ws_rx+data->payload_offset,data->data_ptr,data->data_len);ws_used=data->payload_offset+data->data_len;
  if(ws_used==data->payload_len){ws_rx[ws_used]=0;cJSON *item=cJSON_Parse(ws_rx);if(item){message(item);cJSON_Delete(item);}ws_used=0;}
 }
}
static void wifi_event(void *arg,esp_event_base_t base,int32_t event,void *data){
 if(base==WIFI_EVENT&&event==WIFI_EVENT_STA_START)esp_wifi_connect();
 if(base==WIFI_EVENT&&event==WIFI_EVENT_STA_DISCONNECTED){esp_wifi_connect();}
 if(base==IP_EVENT&&event==IP_EVENT_STA_GOT_IP&&websocket)esp_websocket_client_start(websocket);
}
static void start_wifi(void){
 nvs_handle_t handle;if(nvs_open("epapers",NVS_READONLY,&handle)!=ESP_OK)return;
 char ssid[33]={0},password[65]={0},url[256]={0},token[128]={0};size_t size=sizeof(ssid);
 if(nvs_get_str(handle,"ssid",ssid,&size)!=ESP_OK){nvs_close(handle);return;}
 size=sizeof(password);nvs_get_str(handle,"password",password,&size);size=sizeof(url);nvs_get_str(handle,"url",url,&size);size=sizeof(token);nvs_get_str(handle,"token",token,&size);nvs_close(handle);
 if(!ssid[0]||!url[0]||!token[0])return;
 esp_netif_init();esp_event_loop_create_default();esp_netif_create_default_wifi_sta();wifi_init_config_t config=WIFI_INIT_CONFIG_DEFAULT();ESP_ERROR_CHECK(esp_wifi_init(&config));
 esp_event_handler_register(WIFI_EVENT,ESP_EVENT_ANY_ID,wifi_event,0);esp_event_handler_register(IP_EVENT,IP_EVENT_STA_GOT_IP,wifi_event,0);
 char header[180];snprintf(header,sizeof(header),"Authorization: Bearer %s\r\n",token);
 esp_websocket_client_config_t ws={.uri=url,.headers=header,.reconnect_timeout_ms=5000,.network_timeout_ms=10000,.buffer_size=2048,.crt_bundle_attach=esp_crt_bundle_attach};
 websocket=esp_websocket_client_init(&ws);esp_websocket_register_events(websocket,WEBSOCKET_EVENT_ANY,socket_event,0);
 wifi_config_t station={0};strlcpy((char*)station.sta.ssid,ssid,sizeof(station.sta.ssid));strlcpy((char*)station.sta.password,password,sizeof(station.sta.password));station.sta.threshold.authmode=password[0]?WIFI_AUTH_WPA2_PSK:WIFI_AUTH_OPEN;
 ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA,&station));ESP_ERROR_CHECK(esp_wifi_start());
 memset(password,0,sizeof(password));memset(token,0,sizeof(token));
}
static void message(cJSON *data){
 const cJSON *type=cJSON_GetObjectItemCaseSensitive(data,"type");if(!cJSON_IsString(type))return;
 if(!strcmp(type->valuestring,"time")){const cJSON *epoch=cJSON_GetObjectItem(data,"epoch"),*offset=cJSON_GetObjectItem(data,"offset"),*valid=cJSON_GetObjectItem(data,"valid");if(cJSON_IsNumber(epoch)&&epoch->valuedouble>=1704067200&&cJSON_IsNumber(offset)&&cJSON_IsTrue(valid)){time_epoch=(uint64_t)epoch->valuedouble;time_offset=offset->valueint;time_received=esp_timer_get_time();}}
 else if(!strncmp(type->valuestring,"ota_",4))ota_message(data);
 else if(!strcmp(type->valuestring,"command")){ble_command(data);}
 else if(!strcmp(type->valuestring,"discover"))ble_discover();
 else if(!strcmp(type->valuestring,"hello_request"))hello();
 else if(!strcmp(type->valuestring,"configure")){
  nvs_handle_t handle;if(nvs_open("epapers",NVS_READWRITE,&handle)!=ESP_OK)return;
  const char *keys[]={"ssid","password","url","token"};for(unsigned i=0;i<4;i++){const cJSON *value=cJSON_GetObjectItem(data,keys[i]);if(cJSON_IsString(value))nvs_set_str(handle,keys[i],value->valuestring);}
  nvs_commit(handle);nvs_close(handle);cJSON *result=cJSON_CreateObject();cJSON_AddStringToObject(result,"type","configured");gateway_emit(result);
 }
}
static void usb_task(void *arg){char *line=malloc(32768);size_t used=0;while(true){int ch=getchar();if(ch==EOF){vTaskDelay(pdMS_TO_TICKS(5));continue;}if(ch=='\n'){line[used]=0;cJSON *item=cJSON_Parse(line);if(item){message(item);cJSON_Delete(item);}used=0;}else if(ch!='\r'){if(used<32767)line[used++]=(char)ch;else used=0;}}}
static void heartbeat(void *arg){while(true){cJSON *data=cJSON_CreateObject();cJSON_AddStringToObject(data,"type","heartbeat");cJSON_AddNumberToObject(data,"free_heap",esp_get_free_heap_size());cJSON_AddNumberToObject(data,"uptime",esp_timer_get_time()/1000000);wifi_ap_record_t ap;if(esp_wifi_sta_get_ap_info(&ap)==ESP_OK)cJSON_AddNumberToObject(data,"wifi_rssi",ap.rssi);gateway_emit(data);vTaskDelay(pdMS_TO_TICKS(25000));}}
void app_main(void){
 ESP_ERROR_CHECK(nvs_flash_init());output_lock=xSemaphoreCreateMutex();uint8_t mac[6];esp_read_mac(mac,ESP_MAC_WIFI_STA);snprintf(gateway_id,sizeof(gateway_id),"esp32s3-%02x%02x%02x%02x%02x%02x",mac[0],mac[1],mac[2],mac[3],mac[4],mac[5]);
 esp_log_level_set("*",ESP_LOG_WARN);ble_start();ota_start();start_wifi();esp_ota_mark_app_valid_cancel_rollback();hello();xTaskCreate(usb_task,"usb-json",8192,0,5,0);xTaskCreate(heartbeat,"heartbeat",4096,0,3,0);
}
