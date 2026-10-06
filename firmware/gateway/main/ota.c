#include "gateway.h"
#include <string.h>
#include <stdlib.h>
#include <stdio.h>
#include "esp_system.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/semphr.h"
#include "esp_ota_ops.h"
#include "esp_timer.h"
#include "mbedtls/base64.h"
#include "psa/crypto.h"

static SemaphoreHandle_t lock;
static struct {bool active,tag;char id[40],sha[65];size_t size,offset;esp_ota_handle_t handle;uint8_t *image;int64_t touched;cJSON *meta;psa_hash_operation_t hash;} transfer;
static const char *str(cJSON *data,const char *key){cJSON *value=cJSON_GetObjectItem(data,key);return cJSON_IsString(value)?value->valuestring:NULL;}
bool ota_busy(void){return transfer.active;}
void gateway_ota_status(const char *id,const char *state,size_t offset,float progress,const char *error,const char *identity){cJSON *r=cJSON_CreateObject();cJSON_AddStringToObject(r,"type","ota_status");cJSON_AddStringToObject(r,"id",id);cJSON_AddStringToObject(r,"state",state);cJSON_AddNumberToObject(r,"offset",offset);cJSON_AddNumberToObject(r,"progress",progress);if(error)cJSON_AddStringToObject(r,"error",error);if(identity)cJSON_AddStringToObject(r,"identity",identity);gateway_emit(r);}
static void clear(void){if(transfer.active&&!transfer.tag&&transfer.handle)esp_ota_abort(transfer.handle);psa_hash_abort(&transfer.hash);free(transfer.image);cJSON_Delete(transfer.meta);memset(&transfer,0,sizeof(transfer));}
static void restart(void *arg){vTaskDelay(pdMS_TO_TICKS(750));esp_restart();}
static void watchdog(void *arg){while(true){vTaskDelay(pdMS_TO_TICKS(1000));xSemaphoreTake(lock,portMAX_DELAY);if(transfer.active&&esp_timer_get_time()-transfer.touched>60000000){gateway_ota_status(transfer.id,"failed",transfer.offset,0,"固件传输超时",NULL);clear();}xSemaphoreGive(lock);}}
void ota_start(void){lock=xSemaphoreCreateMutex();psa_crypto_init();xTaskCreate(watchdog,"ota-timeout",3072,0,3,0);}
void ota_message(cJSON *data){
 const char *type=str(data,"type"),*id=str(data,"id");if(!type||!id||strlen(id)>32)return;
 xSemaphoreTake(lock,portMAX_DELAY);
 const char *error=NULL;esp_err_t result=ESP_OK;
 if(!strcmp(type,"ota_begin")){
  if(transfer.active||ble_busy()){gateway_ota_status(id,"busy",0,0,NULL,NULL);goto done;}
  const char *kind=str(data,"kind"),*sha=str(data,"sha256"),*identity=str(data,"identity");cJSON *size=cJSON_GetObjectItem(data,"size");
  if(!kind||(!strcmp(kind,"tag")?false:strcmp(kind,"gateway")!=0)||!sha||strlen(sha)!=64||!identity||!cJSON_IsNumber(size)||size->valuedouble<256||size->valuedouble>4194304){error="升级参数无效";goto fail;}
  transfer.tag=!strcmp(kind,"tag");transfer.size=(size_t)size->valuedouble;strlcpy(transfer.id,id,sizeof(transfer.id));strlcpy(transfer.sha,sha,sizeof(transfer.sha));transfer.meta=cJSON_Duplicate(data,true);
  transfer.hash=psa_hash_operation_init();if(psa_hash_setup(&transfer.hash,PSA_ALG_SHA_256)!=PSA_SUCCESS){error="SHA 初始化失败";goto fail;}
  if(transfer.tag){if(transfer.size>65536){error="墨水屏固件太大";goto fail;}transfer.image=malloc(transfer.size);if(!transfer.image){error="网关内存不足";goto fail;}}
  else{const esp_partition_t *partition=esp_ota_get_next_update_partition(NULL);if(!partition||transfer.size>partition->size){error="升级分区容量不足";goto fail;}result=esp_ota_begin(partition,transfer.size,&transfer.handle);if(result!=ESP_OK){error="无法开始 Flash 升级";goto fail;}}
  transfer.active=true;transfer.touched=esp_timer_get_time();gateway_ota_status(id,"ready",0,0,NULL,NULL);
 }else{
  if(!transfer.active||strcmp(id,transfer.id)){error="升级会话不匹配";goto fail;}
  transfer.touched=esp_timer_get_time();
  if(!strcmp(type,"ota_chunk")){
   const char *encoded=str(data,"data");cJSON *offset=cJSON_GetObjectItem(data,"offset");uint8_t chunk[2048];size_t length=0;
   if(!encoded||!cJSON_IsNumber(offset)||offset->valuedouble!=transfer.offset||mbedtls_base64_decode(chunk,sizeof(chunk),&length,(const unsigned char*)encoded,strlen(encoded))||!length||transfer.offset+length>transfer.size){error="固件数据顺序或长度错误";goto fail;}
   if(transfer.tag)memcpy(transfer.image+transfer.offset,chunk,length);else if(esp_ota_write(transfer.handle,chunk,length)!=ESP_OK){error="Flash 写入失败";goto fail;}
   if(psa_hash_update(&transfer.hash,chunk,length)!=PSA_SUCCESS){error="SHA 更新失败";goto fail;}
   transfer.offset+=length;gateway_ota_status(id,"chunk",transfer.offset,0,NULL,NULL);
  }else if(!strcmp(type,"ota_end")){
   uint8_t digest[32];size_t n=0;char hex[65];if(transfer.offset!=transfer.size||psa_hash_finish(&transfer.hash,digest,sizeof(digest),&n)!=PSA_SUCCESS||n!=32){error="固件未完整接收";goto fail;}
   for(unsigned i=0;i<32;i++)snprintf(hex+i*2,3,"%02x",digest[i]);if(strcmp(hex,transfer.sha)){error="固件 SHA256 不匹配";goto fail;}
   if(transfer.tag){cJSON *meta=transfer.meta;uint8_t *image=transfer.image;size_t length=transfer.size;transfer.meta=NULL;transfer.image=NULL;transfer.active=false;memset(&transfer,0,sizeof(transfer));if(!ble_update(meta,image,length)){cJSON_Delete(meta);free(image);error="墨水屏升级队列已满";goto fail;}gateway_ota_status(id,"flashing",length,75,NULL,NULL);}
   else{const esp_partition_t *partition=esp_ota_get_next_update_partition(NULL);result=esp_ota_end(transfer.handle);transfer.handle=0;if(result!=ESP_OK||esp_ota_set_boot_partition(partition)!=ESP_OK){error="固件镜像验证失败";goto fail;}gateway_ota_status(id,"rebooting",transfer.size,95,NULL,NULL);transfer.active=false;cJSON_Delete(transfer.meta);transfer.meta=NULL;xTaskCreate(restart,"ota-reboot",2048,0,5,0);}
  }else if(!strcmp(type,"ota_abort")){clear();gateway_ota_status(id,"failed",0,0,"升级已取消",NULL);}
 }
 goto done;
 fail:gateway_ota_status(id,"failed",transfer.offset,0,error,NULL);clear();
 done:xSemaphoreGive(lock);
}
