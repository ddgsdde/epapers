#include "ep_core.h"
#include <string.h>
static uint16_t u16(const uint8_t *p){return (uint16_t)p[0]|(uint16_t)p[1]<<8;}
static uint32_t u32(const uint8_t *p){return (uint32_t)u16(p)|(uint32_t)u16(p+2)<<16;}
static uint64_t u64(const uint8_t *p){return (uint64_t)u32(p)|(uint64_t)u32(p+4)<<32;}
static void put16(uint8_t *p,uint16_t v){p[0]=(uint8_t)v;p[1]=(uint8_t)(v>>8);}
static void put32(uint8_t *p,uint32_t v){put16(p,(uint16_t)v);put16(p+2,(uint16_t)(v>>16));}
uint32_t ep_crc32(const uint8_t *data,size_t length){uint32_t crc=0xffffffffu;for(size_t i=0;i<length;i++){crc^=data[i];for(int b=0;b<8;b++)crc=(crc>>1)^((crc&1)?0xedb88320u:0);}return ~crc;}
size_t ep_packet(uint8_t *out,size_t capacity,uint8_t command,uint32_t transaction,uint16_t sequence,const uint8_t *payload,uint16_t length){
 if(length>EP_MAX_PAYLOAD||capacity<(size_t)length+16)return 0;
 out[0]='E';out[1]='P';out[2]=1;out[3]=command;put32(out+4,transaction);put16(out+8,sequence);put16(out+10,length);
 if(length)memcpy(out+12,payload,length);put32(out+12+length,ep_crc32(out,12+length));return 16+length;
}
void ep_init(ep_core *core,ep_display_fn display,void *context){memset(core,0,sizeof(*core));core->display=display;core->context=context;core->clock.temperature_centi=INT16_MIN;}
void ep_clock_tick(ep_clock *clock,uint32_t elapsed_ms){clock->monotonic_ms+=elapsed_ms;}
bool ep_clock_sync(ep_clock *clock,uint64_t epoch,int32_t offset){
 if(epoch<1704067200ULL||epoch>4102444800ULL||offset < -50400||offset>50400)return false;
 clock->epoch_ms=epoch*1000;clock->last_sync_ms=clock->monotonic_ms;clock->offset=offset;clock->valid=true;return true;
}
uint64_t ep_clock_epoch(const ep_clock *clock){
 if(!clock->valid)return 0;
 uint64_t elapsed=clock->monotonic_ms-clock->last_sync_ms;
 int64_t correction=(int64_t)(elapsed/1000)*clock->ppm/1000;
 return (clock->epoch_ms+elapsed+correction)/1000;
}
bool ep_clock_needs_sync(const ep_clock *clock){return !clock->valid||clock->monotonic_ms-clock->last_sync_ms>=86400000ULL;}

uint8_t ep_native_byte(const uint8_t *frame,uint16_t w,uint16_t h,uint8_t rotation,unsigned y,unsigned byte){
 unsigned nw=rotation%2?w:h,nh=rotation%2?h:w;uint8_t result=255;
 for(unsigned bit=0;bit<8&&byte*8+bit<nw;bit++){
  unsigned px=byte*8+bit,lx,ly;
  if(rotation==0){lx=nh-1-y;ly=px;}
  else if(rotation==1){lx=nw-1-px;ly=nh-1-y;}
  else if(rotation==2){lx=y;ly=nw-1-px;}
  else {lx=px;ly=y;}
  if(!(frame[ly*((w+7)/8)+lx/8]&(128>>(lx%8))))result&=~(128>>bit);
 }return result;
}
static uint64_t local_hour(const ep_clock *clock){return ((int64_t)ep_clock_epoch(clock)+clock->offset)/3600;}
static uint32_t tile_hash(const uint8_t *frame,uint16_t w,uint16_t h,uint8_t rotation,unsigned y,unsigned x){
 unsigned nh=rotation%2?h:w;uint8_t bytes[32];unsigned n=nh-y<32?nh-y:32;
 for(unsigned i=0;i<n;i++)bytes[i]=ep_native_byte(frame,w,h,rotation,y+i,x);
 return ep_crc32(bytes,n);
}
ep_refresh_window ep_plan_refresh(const uint8_t *frame,uint16_t w,uint16_t h,uint8_t rotation,const uint32_t *previous,const ep_refresh_history *history,const ep_clock *clock,bool force){
 unsigned nw=rotation%2?w:h,nh=rotation%2?h:w,stride=(nw+7)/8;
 ep_refresh_window result={0};
 if(force)result.reason=EP_REFRESH_MANUAL;
 else if(!history->valid)result.reason=EP_REFRESH_FIRST;
 else if(history->width!=w||history->height!=h||history->rotation!=rotation)result.reason=EP_REFRESH_GEOMETRY;
 else if(clock->monotonic_ms-history->full_ms>=3600000ULL||(clock->valid&&(!history->clock_valid||local_hour(clock)!=history->full_hour)))result.reason=EP_REFRESH_HOUR;
 result.full=result.reason!=EP_REFRESH_NONE;
 if(result.full){result.changed=true;result.x1=stride-1;result.y1=nh-1;return result;}
 result.x0=stride;result.y0=nh;
 unsigned changed_area=0;
 for(unsigned y=0;y<nh;y+=32)for(unsigned x=0;x<stride;x++)if(tile_hash(frame,w,h,rotation,y,x)!=previous[(y/32)*stride+x]){
  unsigned end=y+31<nh?y+31:nh-1;
  unsigned tile_width=nw-x*8<8?nw-x*8:8;changed_area+=tile_width*(end-y+1);
  result.changed=true;if(x<result.x0)result.x0=x;if(x>result.x1)result.x1=x;if(y<result.y0)result.y0=y;if(end>result.y1)result.y1=end;
 }
 /* Conservative coverage of changed 8x32 pixel tiles, not just the bounding box.
  * Multiple distant small fields can remain partial; large page changes clear ghosts. */
 if(changed_area*4>=nw*nh){result.full=true;result.reason=EP_REFRESH_LARGE;result.x0=result.y0=0;result.x1=stride-1;result.y1=nh-1;}
 else if(result.changed)result.reason=EP_REFRESH_SMALL;
 return result;
}
void ep_record_refresh(ep_refresh_history *history,uint32_t *previous,const uint8_t *frame,uint16_t w,uint16_t h,uint8_t rotation,const ep_clock *clock,bool full){
 unsigned nw=rotation%2?w:h,nh=rotation%2?h:w,stride=(nw+7)/8;
 for(unsigned y=0;y<nh;y+=32)for(unsigned x=0;x<stride;x++)previous[(y/32)*stride+x]=tile_hash(frame,w,h,rotation,y,x);
 history->valid=true;history->width=w;history->height=h;history->rotation=rotation;
 if(full){history->clock_valid=clock->valid;history->full_hour=clock->valid?local_hour(clock):0;history->full_ms=clock->monotonic_ms;}
}

/* Parse once with strict bounds, including glyph footprints and logical panel size. */
static bool ep_page_valid(const uint8_t *blob,size_t length){
 if(length<16||length>EP_MAX_BLOB||memcmp(blob,"EPD1",4)||blob[4]!=1||blob[5]>3)return false;
 uint16_t w=u16(blob+6),h=u16(blob+8),regions=u16(blob+14);
 bool landscape=(w==250&&h==122)||(w==212&&h==104);
 bool portrait=(w==122&&h==250)||(w==104&&h==212);
 if((blob[5]%2? !portrait:!landscape)||regions>4)return false;
 size_t background=(size_t)((w+7)/8)*h;
 if(u32(blob+10)!=background||background>EP_MAX_FRAME||16+background>length)return false;
 size_t pos=16+background;
 uint16_t boxes[4][4];
 for(unsigned r=0;r<regions;r++){
  if(pos+22>length)return false;
  const uint8_t *p=blob+pos;
  uint16_t x=u16(p+2),y=u16(p+4),rw=u16(p+6),rh=u16(p+8),font=u16(p+10),count=u16(p+20);
  if(p[0]<1||p[0]>5||(p[1]&~7u)||(p[1]&3)>2||!rw||!rh||x+rw>w||y+rh>h||count>32)return false;
  if(p[0]==5){if(!font||font>1440||rw>128||rh>96||!count||count>8)return false;}
  else if(font<6||font>96)return false;
  for(unsigned j=0;j<r;j++)if(x<boxes[j][0]+boxes[j][2]&&boxes[j][0]<x+rw&&y<boxes[j][1]+boxes[j][3]&&boxes[j][1]<y+rh)return false;
  boxes[r][0]=x;boxes[r][1]=y;boxes[r][2]=rw;boxes[r][3]=rh;
  pos+=22;
  for(unsigned g=0;g<count;g++){
   if(pos+12>length)return false;
   uint16_t gw=u16(blob+pos+4),gh=u16(blob+pos+6),advance=u16(blob+pos+8),bytes=u16(blob+pos+10);
   if(!gw||gw>128||!gh||gh>96||advance>128||bytes!=((gw+7)/8)*gh||pos+12+bytes>length)return false;
   if(p[0]==5&&(gw!=rw||gh!=rh))return false;
   pos+=12+bytes;
  }
 }
 return pos==length;
}

bool ep_blob_valid(const uint8_t *blob,size_t length){
 if(length>EP_MAX_BLOB||length<16)return false;
 if(memcmp(blob,"EPS1",4))return ep_page_valid(blob,length);
 unsigned count=blob[5];if(blob[4]!=1||count<1||count>6||!u16(blob+6)||u16(blob+6)>1440||length<16+count*2)return false;
 size_t pos=16+count*2;uint16_t physical_w=0,physical_h=0;
 for(unsigned i=0;i<count;i++){
  size_t n=u16(blob+16+i*2);if(n>length-pos||!ep_page_valid(blob+pos,n))return false;
  const uint8_t *page=blob+pos;uint16_t w=u16(page+6),h=u16(page+8);
  if(page[5]%2){uint16_t tmp=w;w=h;h=tmp;}
  if(i&&(w!=physical_w||h!=physical_h))return false;physical_w=w;physical_h=h;pos+=n;
 }
 return pos==length;
}

ep_ack ep_receive(ep_core *c,const uint8_t *data,size_t length){
 ep_ack a={0,0,0,EP_BAD_FRAME,false};
 if(length<16||data[0]!='E'||data[1]!='P'||data[2]!=1)return a;
 a.transaction=u32(data+4);a.sequence=u16(data+8);a.command=data[3];
 uint16_t n=u16(data+10);if(n>EP_MAX_PAYLOAD||length!=(size_t)n+16)return a;
 uint32_t crc=u32(data+12+n);
 if(ep_crc32(data,12+n)!=crc){a.result=EP_BAD_CRC;return a;}
 if(a.transaction==c->last_ack.transaction&&a.sequence==c->last_ack.sequence&&a.command==c->last_ack.command&&crc==c->last_frame_crc)return c->last_ack;
 const uint8_t *p=data+12;a.result=EP_OK;
 if(c->display_pending&&a.command!=EP_INFO&&a.command!=EP_STATUS&&a.command!=EP_TIME){a.result=EP_BUSY;return a;}
 switch(a.command){
 case EP_INFO:case EP_STATUS: if(n)a.result=EP_BAD_FRAME;break;
 case EP_TIME: if(n!=12||!ep_clock_sync(&c->clock,u64(p),(int32_t)u32(p+8)))a.result=EP_BAD_FRAME;else a.applied=true;break;
 case EP_CONFIG:
  if(n!=2||p[0]<1||p[0]>3||(p[1]!=0&&p[1]!=5&&p[1]!=6))a.result=EP_BAD_FRAME;
  else {c->panel_model=p[0];c->pin_group=p[1];a.applied=true;}break;
 case EP_REFRESH:
  if(n||!c->active_len){a.result=EP_BAD_ORDER;break;}
  c->force_full_refresh=true;
  if(!c->display||!c->display(c->active,c->active_len,c->context))a.result=c->display_pending?EP_BUSY:EP_DISPLAY_FAILED;
  else a.applied=true;
  c->force_full_refresh=false;break;
 case EP_BEGIN:
  if(n!=8||a.sequence!=0){a.result=EP_BAD_FRAME;break;}
  if(u32(p)>EP_MAX_BLOB||u32(p)<16){a.result=EP_TOO_LARGE;break;}
  c->transaction=a.transaction;c->expected_len=u32(p);c->expected_crc=u32(p+4);c->staging_len=0;c->next_sequence=1;c->receiving=true;break;
 case EP_DATA:
  if(!c->receiving||a.transaction!=c->transaction||a.sequence!=c->next_sequence){a.result=EP_BAD_ORDER;break;}
  if(!n||c->staging_len+n>c->expected_len){a.result=EP_TOO_LARGE;break;}
  memcpy(c->staging+c->staging_len,p,n);c->staging_len+=n;c->next_sequence++;break;
 case EP_COMMIT:
  if(n||!c->receiving||a.transaction!=c->transaction||a.sequence!=c->next_sequence||c->staging_len!=c->expected_len){a.result=EP_BAD_ORDER;break;}
  if(ep_crc32(c->staging,c->staging_len)!=c->expected_crc){a.result=EP_BAD_CRC;break;}
  if(!ep_blob_valid(c->staging,c->staging_len)){a.result=EP_BAD_LAYOUT;break;}
  if(!c->display||!c->display(c->staging,c->staging_len,c->context)){a.result=c->display_pending?EP_BUSY:EP_DISPLAY_FAILED;break;}
  memcpy(c->active,c->staging,c->staging_len);c->active_len=c->staging_len;c->receiving=false;a.applied=true;break;
 case EP_ABORT: c->receiving=false;c->staging_len=0;break;
 default:a.result=EP_BAD_FRAME;break;
 }
 if(a.result==EP_BUSY&&c->display_pending&&(a.command==EP_COMMIT||a.command==EP_REFRESH)){c->pending_ack=a;c->pending_frame_crc=crc;}
 if(a.result==EP_OK){c->last_ack=a;c->last_frame_crc=crc;}
 return a;
}
ep_ack ep_finish_display(ep_core *c,bool success){
 ep_ack a=c->pending_ack;c->display_pending=false;
 if(a.result!=EP_BUSY)return a;
 a.result=success?EP_OK:EP_DISPLAY_FAILED;a.applied=success;
 if(success&&a.command==EP_COMMIT){memcpy(c->active,c->staging,c->staging_len);c->active_len=c->staging_len;c->receiving=false;}
 c->last_ack=a;c->last_frame_crc=c->pending_frame_crc;memset(&c->pending_ack,0,sizeof(c->pending_ack));return a;
}

static void pixel(uint8_t *frame,unsigned stride,unsigned x,unsigned y,bool white){uint8_t bit=0x80u>>(x&7);if(white)frame[y*stride+x/8]|=bit;else frame[y*stride+x/8]&=(uint8_t)~bit;}
/* Gregorian conversion without libc/timezone dependencies. */
static void month_day(int64_t days,unsigned *month,unsigned *day){
 int64_t z=days+719468,era=(z>=0?z:z-146096)/146097;
 unsigned doe=(unsigned)(z-era*146097),yoe=(doe-doe/1460+doe/36524-doe/146096)/365;
 unsigned doy=doe-(365*yoe+yoe/4-yoe/100),mp=(5*doy+2)/153;
 *day=doy-(153*mp+2)/5+1;*month=mp+(mp<10?3:-9);
}
bool ep_compose(const uint8_t *blob,size_t length,const ep_clock *clock,uint8_t *frame,size_t capacity,uint16_t *width,uint16_t *height,uint8_t *rotation){
 if(!ep_blob_valid(blob,length))return false;
 if(!memcmp(blob,"EPS1",4)){
  unsigned count=blob[5],selected=0;uint64_t start=u64(blob+8),now=ep_clock_epoch(clock);
  if(clock->valid&&now>=start)selected=((now-start)/(60*u16(blob+6)))%count;
  size_t pos=16+count*2;for(unsigned i=0;i<selected;i++)pos+=u16(blob+16+i*2);
  return ep_compose(blob+pos,u16(blob+16+selected*2),clock,frame,capacity,width,height,rotation);
 }
 *width=u16(blob+6);*height=u16(blob+8);*rotation=blob[5];unsigned stride=(*width+7)/8;
 size_t bg=u32(blob+10);if(capacity<bg)return false;memcpy(frame,blob+16,bg);size_t pos=16+bg;
 for(unsigned r=0;r<u16(blob+14);r++){
  const uint8_t *p=blob+pos;pos+=22;unsigned x=u16(p+2),y=u16(p+4),rw=u16(p+6),rh=u16(p+8),count=u16(p+20);bool invert=(p[1]&4)!=0;
  if(p[0]==5){
   unsigned selected=clock->valid?(ep_clock_epoch(clock)/(60*u16(p+10)))%count:0;
   for(unsigned g=0;g<count;g++){const uint8_t *gp=blob+pos;pos+=12+u16(gp+10);if(g!=selected)continue;unsigned gs=(rw+7)/8;
    for(unsigned yy=0;yy<rh;yy++)for(unsigned xx=0;xx<rw;xx++)pixel(frame,stride,x+xx,y+yy,(gp[12+yy*gs+xx/8]&(0x80u>>(xx&7)))!=0);
   }continue;
  }
  for(unsigned yy=0;yy<rh;yy++)for(unsigned xx=0;xx<rw;xx++)pixel(frame,stride,x+xx,y+yy,!invert);
  uint32_t chars[7]={'-','-',':','-','-',0,0};unsigned nchars=5;
  if(p[0]==4){
   int16_t temp=clock->temperature_centi;chars[0]='-';chars[1]='-';chars[2]='.';chars[3]='-';chars[4]=0xb0;chars[5]='C';nchars=6;
   if(temp!=INT16_MIN){unsigned value=temp<0?-temp:temp;value=(value+5)/10;unsigned i=0;if(temp<0)chars[i++]='-';if(value>=100)chars[i++]='0'+value/100;chars[i++]='0'+value/10%10;chars[i++]='.';chars[i++]='0'+value%10;chars[i++]=0xb0;chars[i++]='C';nchars=i;}
  }else if(clock->valid){
   uint64_t epoch=ep_clock_epoch(clock);int64_t local=(int64_t)epoch+clock->offset;unsigned h=(unsigned)(local/3600%24),m=(unsigned)(local/60%60);
   if(p[0]==2){unsigned month,day;month_day(local/86400,&month,&day);chars[0]='0'+month/10;chars[1]='0'+month%10;chars[2]=0x6708;chars[3]='0'+day/10;chars[4]='0'+day%10;chars[5]=0x65e5;nchars=6;}
   else {if(p[0]==3){uint64_t deadline=u64(p+12),remaining=deadline>epoch?deadline-epoch:0;h=(unsigned)(remaining/3600);if(h>99)h=99;m=(unsigned)(remaining/60%60);}chars[0]='0'+h/10;chars[1]='0'+h%10;chars[3]='0'+m/10;chars[4]='0'+m%10;}
  }
  const uint8_t *glyphs[7]={0};unsigned total=0;
  for(unsigned g=0;g<count;g++){const uint8_t *gp=blob+pos;for(unsigned j=0;j<nchars;j++)if(u32(gp)==chars[j])glyphs[j]=gp;pos+=12+u16(gp+10);}
  for(unsigned j=0;j<nchars;j++)if(glyphs[j])total+=u16(glyphs[j]+8);
  unsigned cursor=(p[1]&3)==1&&total<rw?(rw-total)/2:(p[1]&3)==2&&total<rw?rw-total:0;
  for(unsigned j=0;j<nchars;j++){const uint8_t *g=glyphs[j];if(!g)continue;unsigned gw=u16(g+4),gh=u16(g+6),gs=(gw+7)/8;
   for(unsigned gy=0;gy<gh&&gy<rh;gy++)for(unsigned gx=0;gx<gw&&cursor+gx<rw;gx++){bool white=(g[12+gy*gs+gx/8]&(0x80u>>(gx&7)))!=0;pixel(frame,stride,x+cursor+gx,y+gy,white^invert);}
   cursor+=u16(g+8);
  }
 }
 return true;
}
