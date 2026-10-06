#include "ep_core.h"
static ep_core core;
static uint8_t frame[EP_MAX_FRAME];
static ep_refresh_history history;
static uint32_t previous[EP_REFRESH_TILES];
static bool success(const uint8_t *blob,size_t length,void *context){(void)context;return ep_blob_valid(blob,length);}
void reset(void){ep_init(&core,success,0);history=(ep_refresh_history){0};}
void set_temperature(int16_t value){core.clock.temperature_centi=value;}
int window(const uint8_t *data,uint16_t w,uint16_t h,uint8_t rotation,int force,uint16_t *out){ep_refresh_window r=ep_plan_refresh(data,w,h,rotation,previous,&history,&core.clock,force);out[0]=r.x0;out[1]=r.x1;out[2]=r.y0;out[3]=r.y1;return r.full?2:r.changed?1:0;}
void record(const uint8_t *data,uint16_t w,uint16_t h,uint8_t rotation,int full){ep_record_refresh(&history,previous,data,w,h,rotation,&core.clock,full);}
int receive(const uint8_t *data,size_t length){return ep_receive(&core,data,length).result;}
size_t active_length(void){return core.active_len;}
void tick(uint32_t ms){ep_clock_tick(&core.clock,ms);}
int sync_clock(uint64_t epoch,int32_t offset){return ep_clock_sync(&core.clock,epoch,offset);}
uint64_t epoch(void){return ep_clock_epoch(&core.clock);}
int needs_sync(void){return ep_clock_needs_sync(&core.clock);}
int compose(const uint8_t *blob,size_t length,uint8_t *out){uint16_t w,h;uint8_t rotation;if(!ep_compose(blob,length,&core.clock,frame,sizeof(frame),&w,&h,&rotation))return 0;size_t n=(w+7)/8*h;for(size_t i=0;i<n;i++)out[i]=frame[i];return (int)n;}
