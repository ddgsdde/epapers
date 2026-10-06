#include "captive_portal.h"
#include <string.h>

bool ep_portal_is_local_host(const char *host) {
    return host && (!strcmp(host, EP_SETUP_HOST) || !strcmp(host, EP_SETUP_HOST ":80"));
}

bool ep_portal_is_root(const char *uri) {
    return uri && uri[0] == '/' && (uri[1] == 0 || uri[1] == '?');
}

size_t ep_portal_dns_reply(uint8_t *packet, size_t length, size_t capacity) {
    /* One uncompressed question, standard query. Do not amplify malformed input. */
    if (!packet || length < 17 || length > capacity || packet[2] & 0xf8 ||
        packet[4] || packet[5] != 1 || packet[6] || packet[7] || packet[8] || packet[9]) return 0;
    size_t end = 12;
    while (end < length && packet[end]) {
        unsigned label = packet[end];
        if (label > 63 || end + label + 1 >= length) return 0;
        end += label + 1;
    }
    if (end - 12 > 254 || end + 5 > length) return 0;
    ++end;
    if (packet[end + 2] || packet[end + 3] != 1) return 0;
    bool ipv4 = packet[end] == 0 && packet[end + 1] == 1;
    end += 4;
    if (ipv4 && capacity - end < 16) return 0;
    /* Preserve the ID and question, with no stale additional or EDNS records. */
    packet[2] = 0x80 | (packet[2] & 1); packet[3] = 0x80;
    packet[6] = 0; packet[7] = ipv4;
    memset(packet + 8, 0, 4);
    if (ipv4) {
        const uint8_t answer[] = {0xc0, 0x0c, 0, 1, 0, 1, 0, 0, 0, 0, 0, 4, 192, 168, 4, 1};
        memcpy(packet + end, answer, sizeof(answer)); end += sizeof(answer);
    }
    return end;
}
