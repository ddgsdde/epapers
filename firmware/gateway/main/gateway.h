#ifndef EP_GATEWAY_H
#define EP_GATEWAY_H
#include "cJSON.h"
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
void gateway_emit(cJSON *message);
void gateway_ack(const char *job,const char *state,const char *error);
void gateway_time(uint64_t *epoch,int32_t *offset);
void ble_start(void);
void ble_command(cJSON *command);
void ble_discover(void);

void ota_start(void);
void ota_message(cJSON *data);
bool ota_busy(void);
bool ble_busy(void);
bool ble_update(cJSON *meta,uint8_t *image,size_t length);
void gateway_ota_status(const char *id,const char *state,size_t offset,float progress,const char *error,const char *identity);
#endif
