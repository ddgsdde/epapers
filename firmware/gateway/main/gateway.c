#include "gateway.h"
#include "network.h"
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/semphr.h"
#include "nvs_flash.h"
#include "esp_timer.h"
#include "esp_mac.h"
#include "esp_log.h"
#include "esp_ota_ops.h"
#include "esp_app_desc.h"

static char gateway_id[40];
static SemaphoreHandle_t output_lock;
static uint64_t time_epoch;
static int64_t time_received;
static int32_t time_offset;
static void message(cJSON *data);

void gateway_time(uint64_t *epoch,int32_t *offset){
 *epoch=time_epoch?time_epoch+(esp_timer_get_time()-time_received)/1000000:0;*offset=time_offset;
}
void gateway_emit(cJSON *data){
 char *text=cJSON_PrintUnformatted(data);if(!text){cJSON_Delete(data);return;}
 xSemaphoreTake(output_lock,portMAX_DELAY);
 network_send(text);
 printf("EP:%s\n",text);fflush(stdout);
 xSemaphoreGive(output_lock);cJSON_free(text);cJSON_Delete(data);
}
void gateway_ack(const char *job,const char *state,const char *error){cJSON *data=cJSON_CreateObject();cJSON_AddStringToObject(data,"type","ack");cJSON_AddStringToObject(data,"job_id",job);cJSON_AddStringToObject(data,"state",state);if(error)cJSON_AddStringToObject(data,"error",error);gateway_emit(data);}
static void hello(void){const esp_app_desc_t *app=esp_app_get_description();char identity[65];for(unsigned i=0;i<32;i++)snprintf(identity+i*2,3,"%02x",app->app_elf_sha256[i]);cJSON *data=cJSON_CreateObject();cJSON_AddStringToObject(data,"type","hello");cJSON_AddStringToObject(data,"gateway_id",gateway_id);cJSON_AddStringToObject(data,"firmware",app->version);cJSON_AddBoolToObject(data,"ota",true);cJSON_AddStringToObject(data,"build_id",identity);network_add_status(data);gateway_emit(data);}
static void message(cJSON *data){
 const cJSON *type=cJSON_GetObjectItemCaseSensitive(data,"type");if(!cJSON_IsString(type))return;
 if(!strcmp(type->valuestring,"time")){const cJSON *epoch=cJSON_GetObjectItem(data,"epoch"),*offset=cJSON_GetObjectItem(data,"offset"),*valid=cJSON_GetObjectItem(data,"valid");if(cJSON_IsNumber(epoch)&&epoch->valuedouble>=1704067200&&cJSON_IsNumber(offset)&&cJSON_IsTrue(valid)){time_epoch=(uint64_t)epoch->valuedouble;time_offset=offset->valueint;time_received=esp_timer_get_time();}}
 else if(!strncmp(type->valuestring,"ota_",4))ota_message(data);
 else if(!strcmp(type->valuestring,"command")){ble_command(data);}
 else if(!strcmp(type->valuestring,"discover"))ble_discover();
 else if(!strcmp(type->valuestring,"hello_request"))hello();
 else if(!strcmp(type->valuestring,"configure")){
  char error[192];bool ok=network_configure(data,error,sizeof(error));cJSON *result=cJSON_CreateObject();cJSON_AddStringToObject(result,"type",ok?"configured":"configuration_error");if(!ok)cJSON_AddStringToObject(result,"error",error);gateway_emit(result);
 }else if(!strcmp(type->valuestring,"network_status_request")){
  cJSON *result=cJSON_CreateObject();cJSON_AddStringToObject(result,"type","network_status");network_add_status(result);gateway_emit(result);
 }else if(!strcmp(type->valuestring,"setup_request")){network_open_setup();}

}
static void usb_task(void *arg){char *line=malloc(32768);size_t used=0;while(true){int ch=getchar();if(ch==EOF){vTaskDelay(pdMS_TO_TICKS(5));continue;}if(ch=='\n'){line[used]=0;cJSON *item=cJSON_Parse(line);if(item){message(item);cJSON_Delete(item);}used=0;}else if(ch!='\r'){if(used<32767)line[used++]=(char)ch;else used=0;}}}
static void heartbeat(void *arg){while(true){cJSON *data=cJSON_CreateObject();cJSON_AddStringToObject(data,"type","heartbeat");cJSON_AddNumberToObject(data,"free_heap",esp_get_free_heap_size());cJSON_AddNumberToObject(data,"uptime",esp_timer_get_time()/1000000);network_add_status(data);gateway_emit(data);vTaskDelay(pdMS_TO_TICKS(25000));}}
void app_main(void){
 ESP_ERROR_CHECK(nvs_flash_init());output_lock=xSemaphoreCreateMutex();uint8_t mac[6];esp_read_mac(mac,ESP_MAC_WIFI_STA);snprintf(gateway_id,sizeof(gateway_id),"esp32s3-%02x%02x%02x%02x%02x%02x",mac[0],mac[1],mac[2],mac[3],mac[4],mac[5]);
 esp_log_level_set("*",ESP_LOG_WARN);ble_start();ota_start();network_start(gateway_id,message,hello);esp_ota_mark_app_valid_cancel_rollback();hello();xTaskCreate(usb_task,"usb-json",8192,0,5,0);xTaskCreate(heartbeat,"heartbeat",4096,0,3,0);
}
