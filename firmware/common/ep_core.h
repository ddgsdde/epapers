#ifndef EP_CORE_H
#define EP_CORE_H
#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>
#define EP_MAX_BLOB 16384
#define EP_MAX_FRAME 4000
#define EP_MAX_PAYLOAD 192
#define EP_SERVICE_UUID "e3a00001-6e31-4c48-9cb4-94a41bbee001"
#define EP_RX_UUID "e3a00002-6e31-4c48-9cb4-94a41bbee001"
#define EP_TX_UUID "e3a00003-6e31-4c48-9cb4-94a41bbee001"
enum ep_command {EP_INFO=1,EP_TIME,EP_BEGIN,EP_DATA,EP_COMMIT,EP_STATUS,EP_CONFIG,EP_ABORT,EP_REFRESH};
enum ep_result {EP_OK=0,EP_BAD_FRAME,EP_BAD_CRC,EP_BAD_ORDER,EP_TOO_LARGE,EP_BAD_LAYOUT,EP_DISPLAY_FAILED,EP_UNSYNCED,EP_BUSY};
typedef struct {uint64_t epoch_ms,monotonic_ms,last_sync_ms;int32_t offset,ppm;int16_t temperature_centi;bool valid;} ep_clock;
typedef struct {uint32_t transaction;uint16_t sequence;uint8_t command,result;bool applied;} ep_ack;
typedef bool (*ep_display_fn)(const uint8_t *blob,size_t length,void *context);
typedef struct {
 uint8_t staging[EP_MAX_BLOB],active[EP_MAX_BLOB];
 size_t staging_len,expected_len,active_len;
 uint32_t expected_crc,transaction,last_frame_crc;
 uint16_t next_sequence;
 bool receiving,force_full_refresh,display_pending;
 uint8_t panel_model,pin_group;
 ep_ack last_ack;
 ep_ack pending_ack;uint32_t pending_frame_crc;
 ep_clock clock;
 ep_display_fn display;void *context;
} ep_core;
uint32_t ep_crc32(const uint8_t *data,size_t length);
size_t ep_packet(uint8_t *out,size_t capacity,uint8_t command,uint32_t transaction,uint16_t sequence,const uint8_t *payload,uint16_t length);
void ep_init(ep_core *core,ep_display_fn display,void *context);
ep_ack ep_receive(ep_core *core,const uint8_t *data,size_t length);
ep_ack ep_finish_display(ep_core *core,bool success);
void ep_clock_tick(ep_clock *clock,uint32_t elapsed_ms);
bool ep_clock_sync(ep_clock *clock,uint64_t epoch,int32_t offset);
uint64_t ep_clock_epoch(const ep_clock *clock);
bool ep_clock_needs_sync(const ep_clock *clock);
bool ep_blob_valid(const uint8_t *blob,size_t length);
bool ep_compose(const uint8_t *blob,size_t length,const ep_clock *clock,uint8_t *frame,size_t capacity,uint16_t *width,uint16_t *height,uint8_t *rotation);
typedef struct {bool valid,clock_valid;uint16_t width,height;uint8_t rotation;uint64_t full_hour,full_ms;} ep_refresh_history;
enum {EP_REFRESH_NONE,EP_REFRESH_SMALL,EP_REFRESH_FIRST,EP_REFRESH_MANUAL,EP_REFRESH_GEOMETRY,EP_REFRESH_HOUR,EP_REFRESH_LARGE};
typedef struct {bool changed,full;uint8_t reason;uint16_t x0,x1,y0,y1;} ep_refresh_window;
uint8_t ep_native_byte(const uint8_t *frame,uint16_t w,uint16_t h,uint8_t rotation,unsigned y,unsigned byte);
#define EP_REFRESH_TILES 128 /* 8 x 32 pixel tiles, up to a 128 x 256 native panel. */
ep_refresh_window ep_plan_refresh(const uint8_t *frame,uint16_t w,uint16_t h,uint8_t rotation,const uint32_t *previous,const ep_refresh_history *history,const ep_clock *clock,bool force);
void ep_record_refresh(ep_refresh_history *history,uint32_t *previous,const uint8_t *frame,uint16_t w,uint16_t h,uint8_t rotation,const ep_clock *clock,bool full);
#endif
