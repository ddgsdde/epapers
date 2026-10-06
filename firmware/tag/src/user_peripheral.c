#include "user_peripheral.h"
#include "user_custs1_def.h"
#include "custs1_task.h"
#include "app_easy_timer.h"
#include "app_easy_gap.h"
#include "lld_evt.h"
#include "panel.h"
#include "adc.h"
#include <string.h>
#include "ep_suota.h"
#ifdef EP_TAG_SUOTA
#include "ep_build.h"
#endif
static ep_core core __attribute__((section("retention_mem_area0")));
static uint32_t last_clock __attribute__((section("retention_mem_area0")));
static uint16_t fraction_us __attribute__((section("retention_mem_area0")));
static uint64_t last_minute __attribute__((section("retention_mem_area0")));
static uint8_t ack_data[80] __attribute__((section("retention_mem_area0")));
static uint8_t ack_len __attribute__((section("retention_mem_area0")));
static bool clock_started __attribute__((section("retention_mem_area0")));
static uint8_t connected_index __attribute__((section("retention_mem_area0")));
static timer_hnd tick_timer __attribute__((section("retention_mem_area0")));
static uint16_t supply_mv __attribute__((section("retention_mem_area0")));
static uint64_t supply_sample_ms __attribute__((section("retention_mem_area0")));
static bool detected __attribute__((section("retention_mem_area0")));
static void clock_update(void){
 uint32_t now=lld_evt_time_get();uint32_t elapsed=clock_started?((now-last_clock)&BLE_BASETIMECNT_MASK):0;last_clock=now;clock_started=true;
 uint64_t us=(uint64_t)elapsed*625+fraction_us;ep_clock_tick(&core.clock,us/1000);fraction_us=us%1000;
}
static void tick(void);
static void schedule_tick(void){unsigned seconds=core.clock.valid?60-ep_clock_epoch(&core.clock)%60:60;tick_timer=app_easy_timer(seconds*100,tick);}
static void tick(void){
 tick_timer=EASY_TIMER_INVALID_TIMER;
 clock_update();if(!detected){detected=true;panel_detect(0);}uint64_t minute=ep_clock_epoch(&core.clock)/60;
 if(core.active_len&&core.clock.valid&&!core.display_pending&&!core.receiving&&minute!=last_minute
#ifdef EP_TAG_SUOTA
 &&!ep_suota_diag.active
#endif
 ){if(panel_display(core.active,core.active_len,&core))last_minute=minute;}
 schedule_tick();
}
static void ack_encode(ep_ack ack){
 uint8_t payload[64]={ack.result,ack.applied,core.clock.valid,ep_clock_needs_sync(&core.clock),255,255,255,core.panel_model,panel_diag.error,panel_diag.group};
 if(supply_mv==65535||core.clock.monotonic_ms-supply_sample_ms>=600000){
  adc_offset_calibrate(ADC_INPUT_MODE_SINGLE_ENDED);uint32_t samples=0;for(unsigned i=0;i<8;i++)samples+=adc_get_vbat_sample(false);
  supply_mv=(samples*225+512)>>10;supply_sample_ms=core.clock.monotonic_ms;
 }
 payload[20]=supply_mv;payload[21]=supply_mv>>8;
#ifdef EP_TAG_SUOTA
 memcpy(payload+22,EP_BUILD_VERSION,sizeof(EP_BUILD_VERSION));
 for(unsigned i=0;i<4;i++)payload[38+i]=ep_suota_diag.identity>>(8*i);
#else
 memcpy(payload+22,"0.3.1-ram",10);
#endif
 int16_t temp=panel_diag.temperature_centi;payload[42]=temp;payload[43]=temp>>8;
 uint64_t age=temp==INT16_MIN?65535:(core.clock.monotonic_ms-panel_diag.temperature_sample_ms)/1000;
 if(age>65535)age=65535;payload[44]=age;payload[45]=age>>8;
 age=(core.clock.monotonic_ms-supply_sample_ms)/1000;if(age>65535)age=65535;payload[46]=age;payload[47]=age>>8;
 uint32_t full=panel_diag.full_refreshes,partial=panel_diag.refreshes-full;
 for(unsigned i=0;i<4;i++){payload[48+i]=full>>(i*8);payload[52+i]=partial>>(i*8);}
 payload[56]=panel_diag.last_reason;
 payload[10]=panel_diag.lut_size;payload[11]=panel_diag.lut_size>>8;
 uint64_t epoch=ep_clock_epoch(&core.clock);for(unsigned i=0;i<8;i++)payload[12+i]=epoch>>(8*i);
 ack_len=ep_packet(ack_data,sizeof(ack_data),ack.command|0x80,ack.transaction,ack.sequence,payload,sizeof(payload));
}
static void ack_set(uint8_t conidx){
 struct custs1_val_set_req *req=KE_MSG_ALLOC_DYN(CUSTS1_VAL_SET_REQ,prf_get_task_from_id(TASK_ID_CUSTS1),TASK_APP,custs1_val_set_req,ack_len);
 req->conidx=conidx;req->handle=EP_TX_VAL;req->length=ack_len;memcpy(req->value,ack_data,ack_len);KE_MSG_SEND(req);
}
void user_app_init(void){
#ifdef EP_TAG_SUOTA
 ep_suota_init();
#ifdef EP_TAG_FLASH_PROBE
 ep_suota_probe(core.staging,sizeof(core.staging));
#endif
#endif
 ep_init(&core,panel_display,&core);clock_started=false;detected=false;last_minute=0;fraction_us=0;connected_index=GAP_INVALID_CONIDX;supply_mv=65535;supply_sample_ms=0;tick_timer=EASY_TIMER_INVALID_TIMER;default_app_on_init();}
void user_app_db_init_complete(void){panel_detect(0);detected=true;arch_set_sleep_mode(ARCH_EXT_SLEEP_ON);default_app_on_db_init_complete();schedule_tick();}
void user_app_adv_start(void){app_easy_gap_undirected_advertise_start();}
void user_app_connection(uint8_t index,struct gapc_connection_req_ind const *param){connected_index=index;default_app_on_connection(index,param);}
void user_app_disconnect(struct gapc_disconnect_ind const *param){
#ifdef EP_TAG_SUOTA
 ep_suota_disconnect();
#endif
 connected_index=GAP_INVALID_CONIDX;if(!core.display_pending)core.receiving=false;user_app_adv_start();}
void user_app_adv_undirect_complete(uint8_t status){if(status==GAP_ERR_CANCELED)user_app_adv_start();}
void user_catch_rest_hndl(ke_msg_id_t msgid,void const *param,ke_task_id_t dest,ke_task_id_t source){
 if(msgid==CUSTS1_VAL_WRITE_IND){
  const struct custs1_val_write_ind *msg=param;
  if(msg->handle!=EP_RX_VAL)return;
  clock_update();
#ifdef EP_TAG_SUOTA
  if(ep_suota_diag.active)return;
#endif
  ep_ack ack=ep_receive(&core,msg->value,msg->length);
  if(ack.result==EP_OK&&ack.command==EP_CONFIG&&(!panel_diag.group||(core.pin_group&&core.pin_group!=panel_diag.group))){panel_detect(core.pin_group);}
  if(ack.applied&&ack.command==EP_TIME){if(tick_timer!=EASY_TIMER_INVALID_TIMER)app_easy_timer_cancel(tick_timer);schedule_tick();}
  if(ack.applied&&ack.command==EP_COMMIT)last_minute=ep_clock_epoch(&core.clock)/60;
  ack_encode(ack);ack_set(msg->conidx);
 }else if(msgid==CUSTS1_ATT_INFO_REQ){
  const struct custs1_att_info_req *msg=param;struct custs1_att_info_rsp *rsp=KE_MSG_ALLOC(CUSTS1_ATT_INFO_RSP,source,dest,custs1_att_info_rsp);
  rsp->conidx=msg->conidx;rsp->att_idx=msg->att_idx;rsp->length=0;rsp->status=msg->att_idx==EP_RX_VAL?ATT_ERR_NO_ERROR:ATT_ERR_WRITE_NOT_PERMITTED;KE_MSG_SEND(rsp);
 }
}

void panel_done(bool success){clock_update();core.clock.temperature_centi=panel_diag.temperature_centi;bool remote=core.pending_ack.result==EP_BUSY;ep_ack ack=ep_finish_display(&core,success);if(success)last_minute=ep_clock_epoch(&core.clock)/60;if(remote){ack_encode(ack);if(connected_index!=GAP_INVALID_CONIDX)ack_set(connected_index);}}
#ifdef EP_TAG_SUOTA
bool ep_suota_can_start(void){return !core.display_pending&&!core.receiving;}
#endif
