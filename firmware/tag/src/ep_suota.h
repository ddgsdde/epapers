#ifndef EP_SUOTA_H
#define EP_SUOTA_H
#include <stdbool.h>
#include <stdint.h>
#ifdef EP_TAG_SUOTA
typedef struct {uint32_t jedec,identity;uint32_t offsets[2];uint8_t product[12],headers[128];int8_t flash_status;bool ready,active;uint8_t bank;} ep_suota_diagnostics;
extern ep_suota_diagnostics ep_suota_diag;
void ep_suota_init(void);
void ep_suota_status(uint8_t event);
bool ep_suota_verify_image(void);
bool ep_suota_valid_header(const uint8_t *data,uint32_t length);
bool ep_suota_can_start(void);
void ep_suota_disconnect(void);
#ifdef EP_TAG_FLASH_PROBE
typedef struct {uint32_t address,length,request,done,status,operation;} ep_flash_mailbox;
extern volatile ep_flash_mailbox ep_flash_probe;
void ep_suota_probe(uint8_t *buffer,uint32_t capacity);
#endif
#endif
#endif
