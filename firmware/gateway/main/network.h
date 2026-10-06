#ifndef EP_NETWORK_H
#define EP_NETWORK_H
#include "cJSON.h"
#include <stdbool.h>
#include <stddef.h>

void network_start(const char *gateway_id, void (*message)(cJSON *), void (*connected)(void));
void network_send(const char *text);
void network_add_status(cJSON *data);
bool network_configure(const cJSON *data, char *error, size_t capacity);
void network_open_setup(void);
#endif
