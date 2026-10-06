#ifndef EP_CAPTIVE_PORTAL_H
#define EP_CAPTIVE_PORTAL_H
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define EP_SETUP_HOST "192.168.4.1"
#define EP_SETUP_URL "http://" EP_SETUP_HOST "/"
#define EP_CAPTIVE_API_URL EP_SETUP_URL "captive-portal"

bool ep_portal_is_local_host(const char *host);
bool ep_portal_is_root(const char *uri);
size_t ep_portal_dns_reply(uint8_t *packet, size_t length, size_t capacity);
#endif
