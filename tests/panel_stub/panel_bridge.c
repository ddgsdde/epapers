/* Simulate a controller which loses its RAM on power-off and scans the whole
 * configured gate range. This reproduces undefined pixels outside a dirty window. */
#include "panel.h"
#include "gpio.h"
#include "app_easy_timer.h"
#include <string.h>
static ep_core core;
static uint8_t pins[48],cmd,bits,value,args[8],ram[4000],initialized[4000],shown[4000];
static unsigned argc,read_bit,busy,x0,x1,y0,y1,x,y,mode,refresh_count;
static unsigned controller_lut_size=70,native_stride=13,native_height=212,panel_model=1;
static bool done,success,all_initialized,data_input;
static uint8_t lut[100],gate,source[3],frame_rate,line_time,vcom;
static unsigned lut_written;
static bool partial_setup_valid;
static timer_callback pending;
static void controller_command(uint8_t byte){
 cmd=byte;argc=0;read_bit=0;
 if(cmd==0x32)lut_written=0;
 if(cmd==0x12){busy=2;}
 if(cmd==0x20){busy=2;if(mode==0xf7||mode==0xc7){
  all_initialized=true;for(unsigned i=0;i<native_stride*native_height;i++)if(!initialized[i])all_initialized=false;
  partial_setup_valid=true;
  if(mode==0xc7){
   unsigned groups=controller_lut_size==100?10:7;
   bool short_a41=panel_model==1;
   if(lut_written!=controller_lut_size||gate!=0x15||source[0]!=0x41||source[1]!=0xa8||source[2]!=0x32)partial_setup_valid=false;
   if(short_a41?(vcom!=0x28||frame_rate!=0x30||line_time!=0x0a):controller_lut_size==100?(vcom!=0x50||frame_rate!=0x0f||line_time!=0x0c):(frame_rate!=0x0a||line_time!=0x2e))partial_setup_valid=false;
   for(unsigned i=0;i<controller_lut_size;i++){
    uint8_t expected=i<groups*5?(i%groups==0?(i/groups==0||i/groups==2?0x40:i/groups==1||i/groups==3?0x80:0):0):((i-groups*5)%5==4?(short_a41?0:1):i==groups*5?(short_a41?10:15):0);
    if(lut[i]!=expected)partial_setup_valid=false;
   }
  }
  memcpy(shown,ram,native_stride*native_height);refresh_count++;
 }}
}
static void controller_data(uint8_t byte){
 if(cmd==0x32){if(lut_written<100)lut[lut_written]=byte;lut_written++;}
 if(cmd==0x24){unsigned offset=y*native_stride+x;if(offset<4000){ram[offset]=byte;initialized[offset]=1;}if(++x>x1){x=x0;y++;}return;}
 if(argc<8)args[argc++]=byte;
 if(cmd==0x03)gate=byte;
 if(cmd==0x04&&argc<=3)source[argc-1]=byte;
 if(cmd==0x3a)frame_rate=byte;
 if(cmd==0x3b)line_time=byte;
 if(cmd==0x2c)vcom=byte;
 if(cmd==0x22)mode=byte;
 if(cmd==0x44&&argc==2){x0=args[0];x1=args[1];}
 if(cmd==0x45&&argc==4){y0=args[0]|args[1]<<8;y1=args[2]|args[3]<<8;}
 if(cmd==0x4e)x=byte;
 if(cmd==0x4f&&argc==2)y=args[0]|args[1]<<8;
}
static void set(unsigned pin,int high){
 int old=pins[pin];pins[pin]=high;
 if(pin==0x23&&high&&!old){memset(ram,0xa5,sizeof(ram));memset(initialized,0,sizeof(initialized));}
 if(pin==0x00&&high&&!old&&!pins[0x21]&&!data_input){
  value=(value<<1)|pins[0x06];if(++bits==8){if(pins[0x05])controller_data(value);else controller_command(value);bits=value=0;}
 }
}
void GPIO_SetActive(unsigned p,unsigned n){set(p*16+n,1);}
void GPIO_SetInactive(unsigned p,unsigned n){set(p*16+n,0);}
void GPIO_ConfigurePin(unsigned p,unsigned n,GPIO_PUPD direction,unsigned function,int high){(void)function;if(p*16+n==0x06){data_input=direction==INPUT_PULLUP;if(data_input){read_bit=0;bits=value=0;}}set(p*16+n,high);}
int GPIO_GetPinStatus(unsigned p,unsigned n){
 unsigned pin=p*16+n;if(pin==0x20){if(busy){busy--;return 1;}return 0;}
 if(pin==0x06){unsigned index=read_bit/8;uint8_t byte=cmd==0x33?(index<controller_lut_size?0xa5:0xff):cmd==0x1b?(index==0?26:0):0xff;unsigned bit=7-read_bit%8;read_bit++;return (byte>>bit)&1;}
 return pins[pin];
}
void app_easy_timer(unsigned timeout,timer_callback callback){(void)timeout;pending=callback;}
void panel_done(bool ok){success=ok;done=true;core.display_pending=false;core.clock.temperature_centi=panel_diag.temperature_centi;}
void start_panel_model(unsigned model){panel_model=model;controller_lut_size=model==2?100:70;native_stride=model==3?16:13;native_height=model==3?250:212;ep_init(&core,panel_display,&core);ep_clock_sync(&core.clock,1750000000,0);core.panel_model=model;panel_detect(6);}
void start_panel(void){start_panel_model(1);}
unsigned lut_byte(unsigned i){return i<sizeof(lut)?lut[i]:255;}
unsigned vcom_value(void){return vcom;}
unsigned full_count(void){return panel_diag.full_refreshes;}
unsigned partial_count(void){return panel_diag.refreshes-panel_diag.full_refreshes;}
unsigned last_reason(void){return panel_diag.last_reason;}
int display(const uint8_t *blob,size_t length,uint32_t elapsed,int force){
 core.clock.monotonic_ms=elapsed;core.force_full_refresh=force;done=success=false;pending=0;
 unsigned count=refresh_count;
 if(panel_display(blob,length,&core))return 0;
 for(unsigned i=0;pending&&i<1000;i++){timer_callback call=pending;pending=0;call();}
 if(!done||!success)return -1;
 if(refresh_count==count)return 0;
 if(!all_initialized)return -2;
 if(!partial_setup_valid)return -5;
 uint8_t expected[4000];uint16_t w,h;uint8_t rotation;
 if(!ep_compose(blob,length,&core.clock,expected,sizeof(expected),&w,&h,&rotation))return -3;
 /* The test pages contain static widgets, unaffected by the newly sampled temperature. */
 for(unsigned yy=0;yy<native_height;yy++)for(unsigned xx=0;xx<native_stride;xx++)if(shown[yy*native_stride+xx]!=ep_native_byte(expected,w,h,rotation,yy,xx))return -4;
 return mode;
}
