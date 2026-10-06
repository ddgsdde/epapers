#ifndef EP_NETWORK_CONFIG_H
#define EP_NETWORK_CONFIG_H
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include "cJSON.h"

typedef struct {
    uint32_t version;
    char ssid[33];
    char password[65];
    char url[256];
    char token[128];
} ep_network_config;

bool ep_network_config_update(const ep_network_config *current, const cJSON *data,
                              ep_network_config *out, char *error, size_t capacity);
#endif
