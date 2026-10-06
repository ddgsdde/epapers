/* Project-owned board adapter and validation around SDK6's SUOTA receiver. */
#include "ep_suota.h"
#include "ep_build.h"
#include "ep_core.h"
#include "app_suotar.h"
#include "spi.h"
#include "spi_flash.h"
#include "gpio.h"
#include "arch.h"
#include <string.h>
ep_suota_diagnostics ep_suota_diag __attribute__((section("retention_mem_area0")));
static const spi_cfg_t config={.spi_ms=SPI_MS_MODE_MASTER,.spi_cp=SPI_CP_MODE_0,.spi_speed=SPI_SPEED_MODE_4MHz,.spi_wsz=SPI_MODE_8BIT,.spi_cs=SPI_CS_0,.cs_pad={GPIO_PORT_0,GPIO_PIN_3}};
static uint32_t word(const uint8_t *p){return p[0]|(uint32_t)p[1]<<8|(uint32_t)p[2]<<16|(uint32_t)p[3]<<24;}
static void open_flash(void){
 GPIO_ConfigurePin(GPIO_PORT_2,GPIO_PIN_1,OUTPUT,PID_GPIO,true);
 GPIO_ConfigurePin(GPIO_PORT_0,GPIO_PIN_3,OUTPUT,PID_SPI_EN,true);
 GPIO_ConfigurePin(GPIO_PORT_0,GPIO_PIN_0,OUTPUT,PID_SPI_CLK,false);
 GPIO_ConfigurePin(GPIO_PORT_0,GPIO_PIN_6,OUTPUT,PID_SPI_DO,false);
 GPIO_ConfigurePin(GPIO_PORT_0,GPIO_PIN_5,INPUT,PID_SPI_DI,false);
 spi_initialize(&config);spi_flash_release_from_power_down();
}
static void close_flash(void){spi_flash_power_down();spi_release();GPIO_ConfigurePin(GPIO_PORT_0,GPIO_PIN_3,OUTPUT,PID_GPIO,true);}
static bool read_flash(uint32_t address,uint8_t *data,uint32_t length){uint32_t read=0;return spi_flash_read_data(data,address,length,&read)==SPI_FLASH_ERR_OK&&read==length;}
static bool verify(uint32_t address,const uint8_t *header){
 uint32_t length=word(header+4),crc=~0u;uint8_t block[128];
 if(length<8||length>0x18000-64||address+64+length>PRODUCT_HEADER_POSITION)return false;
 for(uint32_t offset=0;offset<length;offset+=sizeof(block)){
  unsigned n=length-offset<sizeof(block)?length-offset:sizeof(block);
  if(!read_flash(address+64+offset,block,n))return false;
  for(unsigned i=0;i<n;i++){crc^=block[i];for(unsigned bit=0;bit<8;bit++)crc=(crc>>1)^(0xedb88320u&-(crc&1));}
 }
 return ~crc==word(header+8);
}
void ep_suota_init(void){
 memset(&ep_suota_diag,0,sizeof(ep_suota_diag));open_flash();
 uint8_t id;ep_suota_diag.flash_status=spi_flash_auto_detect(&id);
 spi_flash_read_jedec_id(&ep_suota_diag.jedec);
 /* Fudan FM25Q04: 4 Mbit, 256-byte pages, standard 4 KB sector commands. */
 if(ep_suota_diag.jedec==0xa14013){const spi_flash_cfg_t cfg={.jedec_id=0xa14013,.chip_size=524288};spi_flash_configure_env(&cfg);ep_suota_diag.flash_status=SPI_FLASH_ERR_OK;}
 if(ep_suota_diag.flash_status==SPI_FLASH_ERR_OK&&read_flash(PRODUCT_HEADER_POSITION,ep_suota_diag.product,12)){
  uint8_t *p=ep_suota_diag.product;uint32_t a=word(p+4),b=word(p+8);
  ep_suota_diag.offsets[0]=a;ep_suota_diag.offsets[1]=b;
  if(p[0]==0x70&&p[1]==0x52&&a>=0x4000&&b>a&&!(a&4095)&&!(b&4095)&&b<PRODUCT_HEADER_POSITION){
   ep_suota_diag.ready=true;
   for(unsigned bank=0;bank<2;bank++){
    uint8_t *h=ep_suota_diag.headers+bank*64;uint32_t address=ep_suota_diag.offsets[bank];
    if(read_flash(address,h,64)&&h[0]==0x70&&h[1]==0x51&&h[2]==0xaa&&!h[32]&&!memcmp(h+12,EP_BUILD_VERSION,sizeof(EP_BUILD_VERSION))&&verify(address,h)){
     ep_suota_diag.identity=word(h+8);ep_suota_diag.bank=bank+1;
    }
   }
  }
 }
 close_flash();
}
bool ep_suota_valid_header(const uint8_t *data,uint32_t length){
 if(!ep_suota_diag.active||!ep_suota_diag.ready||!ep_suota_diag.identity||length<72||data[32])return false;
 uint32_t size=word(data+4),span=ep_suota_diag.offsets[1]-ep_suota_diag.offsets[0],last=PRODUCT_HEADER_POSITION-ep_suota_diag.offsets[1];
 if(span>last)span=last;
 uint32_t sp=word(data+64),pc=word(data+68);
 return size>=8&&size<=span-64&&sp>=0x07fc0000&&sp<0x07fd8000&&!(sp&7)&&(pc&1)&&pc>=0x07fc0000&&pc<0x07fc0000+size;
}
bool ep_suota_verify_image(void){
 uint8_t h[64];uint32_t address=suota_state.mem_base_add;
 if(!ep_suota_diag.active||(address!=ep_suota_diag.offsets[0]&&address!=ep_suota_diag.offsets[1])||address==ep_suota_diag.offsets[ep_suota_diag.bank-1])return false;
 return suota_state.suota_img_idx==suota_state.suota_image_len&&read_flash(address,h,64)&&verify(address,h);
}
void ep_suota_status(uint8_t event){
 if(event==SUOTAR_START){
  if(!ep_suota_diag.identity||!ep_suota_diag.ready||!ep_suota_can_start()){suota_state.mem_dev=SUOTAR_MEM_INVAL_DEV;return;}
  ep_suota_diag.active=true;arch_set_sleep_mode(ARCH_SLEEP_OFF);open_flash();
 }else {if(ep_suota_diag.active)close_flash();ep_suota_diag.active=false;arch_set_sleep_mode(ARCH_EXT_SLEEP_ON);}
}
void ep_suota_disconnect(void){
 if(suota_state.reboot_requested)platform_reset(RESET_AFTER_SUOTA_UPDATE);
 if(ep_suota_diag.active){app_suotar_stop();app_suotar_reset();}
}
#ifdef EP_TAG_FLASH_PROBE
#include "arch_wdg.h"
#include "hw_otpc.h"
volatile uint32_t ep_otp_boot;
volatile ep_flash_mailbox ep_flash_probe;
void ep_suota_probe(uint8_t *buffer,uint32_t capacity){
 wdg_freeze();hw_otpc_init();hw_otpc_manual_read_on(false);ep_otp_boot=*(volatile uint32_t*)0x07f8fe00;hw_otpc_disable();open_flash();const spi_flash_cfg_t cfg={.chip_size=524288};spi_flash_configure_env(&cfg);
 for(;;){
  if(ep_flash_probe.request!=ep_flash_probe.done){
   uint32_t request=ep_flash_probe.request,address=ep_flash_probe.address,length=ep_flash_probe.length;
   ep_flash_probe.status=1;
   if(ep_flash_probe.operation==0)ep_flash_probe.status=length&&length<=capacity&&address<=524288&&length<=524288-address&&read_flash(address,buffer,length)?0:1;
#ifdef EP_TAG_FLASH_INSTALL
   /* Explicit factory install tool only; not included in runtime or read-only probe. */
   else if(ep_suota_diag.jedec==0xa14013&&address<0x39000&&length<=0x39000-address){
    if(ep_flash_probe.operation==1&&length==4096&&!(address&4095))ep_flash_probe.status=(uint32_t)spi_flash_block_erase(address,SPI_FLASH_OP_SE);
    else if(ep_flash_probe.operation==2&&length&&length<=capacity&&!(address&255)){uint32_t written=0;int status=spi_flash_write_data(buffer,address,length,&written);ep_flash_probe.status=status?status:written==length?0:1;}
   }
#endif
   ep_flash_probe.done=request;
  }
 }
}
#endif
