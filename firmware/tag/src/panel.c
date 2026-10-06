/* Bit-banged SPI for the two board pin maps documented by HMCLOCK.
 * Controller selection is checked using LUT readback, with bounded BUSY waits.
 */
#include "panel.h"
#include "gpio.h"
#include "arch.h"
#include "arch_wdg.h"
#include "app_easy_timer.h"
#include <string.h>
#ifdef EP_PANEL_HOST_TEST
volatile panel_diagnostic panel_diag;
#else
volatile panel_diagnostic panel_diag __attribute__((section("retention_mem_area0")));
#endif
static uint8_t pins[7]; /* power, busy, reset, dc, cs, clock, data */
static uint8_t frame[EP_MAX_FRAME];
static uint32_t previous[EP_REFRESH_TILES];
static ep_refresh_history history;
static uint64_t temperature_attempt_ms;
static bool temperature_attempted;
static void set(unsigned pin,bool value){if(value)GPIO_SetActive(pins[pin]>>4,pins[pin]&15);else GPIO_SetInactive(pins[pin]>>4,pins[pin]&15);}
static bool get(unsigned pin){return GPIO_GetPinStatus(pins[pin]>>4,pins[pin]&15);}
static void mode(unsigned pin,GPIO_PUPD mode,bool value){GPIO_ConfigurePin(pins[pin]>>4,pins[pin]&15,mode,PID_GPIO,value);}
static void delay(unsigned ms){while(ms--){wdg_reload(WATCHDOG_DEFAULT_PERIOD);arch_asm_delay_us(1000);}}
static bool idle(unsigned limit){while(get(1)&&limit){delay(1);limit--;}if(get(1)){panel_diag.error=1;return false;}return true;}
static void write_byte(uint8_t v){for(unsigned i=0;i<8;i++){set(5,0);set(6,v&128);arch_asm_delay_us(1);set(5,1);v<<=1;arch_asm_delay_us(1);}}
static uint8_t read_byte(void){uint8_t v=0;for(unsigned i=0;i<8;i++){set(5,0);arch_asm_delay_us(1);set(5,1);arch_asm_delay_us(1);v=(v<<1)|get(6);}return v;}
static void command(uint8_t v){set(4,0);set(3,0);write_byte(v);set(4,1);}
static void data(uint8_t v){set(4,0);set(3,1);write_byte(v);set(4,1);}
static void reg1(uint8_t cmd,uint8_t a){command(cmd);data(a);}
static void reg2(uint8_t cmd,uint8_t a,uint8_t b){command(cmd);data(a);data(b);}
static void power_off(void){set(0,0);set(2,0);set(3,0);set(4,0);set(5,0);set(6,0);}
static bool open(uint8_t group){
 const uint8_t map6[7]={0x23,0x20,0x07,0x05,0x21,0x00,0x06};
 const uint8_t map5[7]={0x23,0x11,0x10,0x07,0x21,0x01,0x20};
 memcpy(pins,group==5?map5:map6,7);
 for(unsigned i=0;i<7;i++)mode(i,i==1?INPUT:OUTPUT,i==4);
 set(0,1);delay(5);set(2,0);delay(10);set(2,1);delay(10);
 command(0x12);delay(2);return idle(1000);
}
static uint16_t lut_readback(void){
 command(0x32);for(unsigned i=0;i<256;i++)data(0xa5);
 set(4,0);set(3,0);write_byte(0x33);set(3,1);mode(6,INPUT_PULLUP,0);
 unsigned count=0;bool ended=false;
 for(unsigned i=0;i<256;i++){uint8_t v=read_byte();if(!ended&&v==0xa5)count++;else ended=true;}
 set(4,1);mode(6,OUTPUT,0);return count;
}
void panel_detect(uint8_t group){
 panel_diag.error=0;panel_diag.lut_size=0;panel_diag.group=0;panel_diag.temperature_centi=INT16_MIN;
 uint8_t candidates[2]={group?group:6,5};unsigned total=group?1:2;
 for(unsigned i=0;i<total;i++){
  bool ok=open(candidates[i]);uint16_t n=ok?lut_readback():0;power_off();
  if(n==70||n==100){panel_diag.group=candidates[i];panel_diag.lut_size=n;panel_diag.error=0;return;}
 }
 panel_diag.error=2;
}
static bool compatible(uint8_t model,uint16_t w,uint16_t h){
 if(model==1)return panel_diag.lut_size==70&&w==104&&h==212;
 if(model==2)return panel_diag.lut_size==100&&w==104&&h==212;
 /* OPM has a 70-byte LUT, but still requires explicit dimensions/profile. */
 return model==3&&panel_diag.lut_size==70&&w==122&&h==250;
}
static void partial_lut(uint8_t model){
 /* Controller drive parameters from the reference panel configuration.
  * Both unchanged-pixel transitions receive a settling pulse. */
 bool short_a41=model==1&&panel_diag.lut_size==70;
 unsigned groups=panel_diag.lut_size==100?10:7;
 command(0x32);
 for(unsigned state=0;state<5;state++)for(unsigned phase=0;phase<groups;phase++)data(phase==0?(state==0||state==2?0x40:state==1||state==3?0x80:0):0);
 /* V26 short waveform: TP0A=10, RP=0 (one run), versus the prior 15*2 frames.
  * Its RAM26 is the complement of RAM24: only LUT1/2 can be selected.
  * Duplicating those rows into LUT3/0 produces the same source/VCOM waveform
  * for either auxiliary RAM bit, without needing a second framebuffer write. */
 for(unsigned phase=0;phase<groups;phase++)for(unsigned byte=0;byte<5;byte++)data(byte==4?(short_a41?0:1):phase==0&&byte==0?(short_a41?0x0a:0x0f):0);
 reg1(0x03,0x15);command(0x04);data(0x41);data(0xa8);data(0x32);
 if(short_a41){reg1(0x2c,0x28);reg1(0x3a,0x30);reg1(0x3b,0x0a);}
 else if(panel_diag.lut_size==70){reg1(0x3a,0x0a);reg1(0x3b,0x2e);}
 else {reg1(0x2c,0x50);reg1(0x3a,0x0f);reg1(0x3b,0x0c);}
}
typedef struct {ep_core *core;uint16_t w,h,nw,nh,row,byte;uint8_t rotation,phase;unsigned waits;bool full,busy_seen,sensor_only,sample_temperature;ep_refresh_window window;} refresh_state;
static refresh_state refresh;
static void read_temperature(void){
  /* Signed 12-bit, 1/16 degree, MSB first. Reject POR 0x7FF and bad reads. */
  set(4,0);set(3,0);write_byte(0x1b);set(3,1);mode(6,INPUT_PULLUP,0);
  uint8_t hi=read_byte(),lo=read_byte();set(4,1);mode(6,OUTPUT,0);
  int16_t raw=((uint16_t)hi<<4)|(lo>>4);if(raw&0x800)raw|=0xf000;
  if(!(lo&15)&&raw>=-25*16&&raw<=85*16){panel_diag.temperature_centi=raw*100/16;panel_diag.temperature_sample_ms=refresh.core->clock.monotonic_ms;}
  else panel_diag.temperature_centi=INT16_MIN;
  temperature_attempt_ms=refresh.core->clock.monotonic_ms;temperature_attempted=true;
}
static void finish(bool success){
 if(success&&refresh.full)read_temperature();
 reg1(0x10,0x01);power_off();refresh.phase=0;
 if(success){
  if(!refresh.sensor_only){
   ep_record_refresh(&history,previous,frame,refresh.w,refresh.h,refresh.rotation,&refresh.core->clock,refresh.full);
   panel_diag.refreshes++;
   if(refresh.full)panel_diag.full_refreshes++;
   panel_diag.last_reason=refresh.window.reason;
  }
  panel_diag.error=0;
 }else history.valid=false; /* The next attempt must recover the whole panel. */
 panel_done(success);arch_set_sleep_mode(ARCH_EXT_SLEEP_ON);
}
static void begin_pixels(void){
 ep_refresh_window *r=&refresh.window;
 reg2(0x44,r->x0,r->x1);command(0x45);data(r->y0);data(r->y0>>8);data(r->y1);data(r->y1>>8);
 reg1(0x4e,r->x0);reg2(0x4f,r->y0,r->y0>>8);command(0x24);
 refresh.phase=4;refresh.row=r->y0;refresh.byte=r->x0;
}
static void step(void){
 if(!refresh.phase)return;
 if(refresh.phase==1){set(2,1);refresh.phase=2;}
 else if(refresh.phase==2){command(0x12);refresh.phase=3;refresh.waits=0;}
 else if(refresh.phase==3){
  if(get(1)){if(++refresh.waits>100){panel_diag.error=1;finish(false);return;}}
  else {
   uint16_t nh=refresh.nh;
   reg1(0x74,0x54);reg1(0x7e,0x3b);reg2(0x2b,0x04,0x63);
   command(0x0c);data(0x8b);data(0x9c);data(0x96);data(0x0f);
   command(0x01);data(nh-1);data((nh-1)>>8);data(0);
   reg1(0x11,0x03);reg1(0x3c,0x05);reg1(0x18,0x80);
   if(!refresh.full&&refresh.sample_temperature){
    /* Clock + sensor + LUT only, without enabling the analog/HV power stage. */
    reg1(0x22,0xb1);command(0x20);refresh.phase=6;refresh.waits=0;
   }else begin_pixels();
  }
 }else if(refresh.phase==6){
  if(get(1)){if(++refresh.waits>100){panel_diag.error=1;finish(false);return;}}
  else {read_temperature();if(refresh.sensor_only){finish(true);return;}begin_pixels();}
 }else if(refresh.phase==4){
  /* Yield between small SPI batches so SDK6 can drain its BLE message heap. */
  for(unsigned i=0;i<64&&refresh.row<=refresh.window.y1;i++){
   data(ep_native_byte(frame,refresh.w,refresh.h,refresh.rotation,refresh.row,refresh.byte));
   if(++refresh.byte>refresh.window.x1){refresh.byte=refresh.window.x0;refresh.row++;}
  }
  if(refresh.row>refresh.window.y1){
   if(!refresh.full)partial_lut(refresh.core->panel_model);reg1(0x22,refresh.full?0xf7:0xc7);command(0x20);
   refresh.phase=5;refresh.waits=0;refresh.busy_seen=get(1);
  }
 }else if(refresh.phase==5){
  refresh.waits++;if(get(1))refresh.busy_seen=true;
  else if(refresh.busy_seen){finish(true);return;}
  else if(refresh.waits>5){panel_diag.error=5;finish(false);return;}
  if(refresh.waits>500){panel_diag.error=1;finish(false);return;}
 }
 app_easy_timer(1,step);
}
bool panel_display(const uint8_t *blob,size_t length,void *context){
 ep_core *core=context;uint16_t w,h;uint8_t rotation;
 if(refresh.phase)return false;
 arch_set_sleep_mode(ARCH_SLEEP_OFF);
 if(!core->panel_model||!ep_compose(blob,length,&core->clock,frame,sizeof(frame),&w,&h,&rotation)){panel_diag.error=3;arch_set_sleep_mode(ARCH_EXT_SLEEP_ON);return false;}
 uint16_t nw=rotation%2?w:h,nh=rotation%2?h:w;
 if(!compatible(core->panel_model,nw,nh)){panel_diag.error=4;arch_set_sleep_mode(ARCH_EXT_SLEEP_ON);return false;}
 ep_refresh_window window=ep_plan_refresh(frame,w,h,rotation,previous,&history,&core->clock,core->force_full_refresh);
 /* RAM is lost when panel power is cut. Seed all pixels even for the fast waveform;
  * writing only a dirty RAM window would drive undefined pixels outside it. */
 if(window.changed){window.x0=window.y0=0;window.x1=(nw-1)/8;window.y1=nh-1;}
 bool sample=!temperature_attempted||core->clock.monotonic_ms-temperature_attempt_ms>=600000;
 if(!window.changed&&!sample){arch_set_sleep_mode(ARCH_EXT_SLEEP_ON);return true;}
 const uint8_t map6[7]={0x23,0x20,0x07,0x05,0x21,0x00,0x06};
 const uint8_t map5[7]={0x23,0x11,0x10,0x07,0x21,0x01,0x20};
 memcpy(pins,panel_diag.group==5?map5:map6,7);
 for(unsigned i=0;i<7;i++)mode(i,i==1?INPUT:OUTPUT,i==4);
 set(0,1);set(2,0);
 refresh=(refresh_state){.core=core,.w=w,.h=h,.nw=nw,.nh=nh,.rotation=rotation,.phase=1,.full=window.full,.sensor_only=!window.changed,.sample_temperature=sample,.window=window};
 core->display_pending=true;app_easy_timer(1,step);return false;
}
