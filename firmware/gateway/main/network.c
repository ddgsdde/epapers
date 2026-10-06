#include "network.h"
#include "network_config.h"
#include "gateway.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <sys/socket.h>
#include <net/if.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "driver/gpio.h"
#include "nvs.h"
#include "esp_wifi.h"
#include "esp_netif.h"
#include "esp_event.h"
#include "esp_timer.h"
#include "esp_system.h"
#include "esp_random.h"
#include "esp_app_desc.h"
#include "esp_http_server.h"
#include "esp_websocket_client.h"
#include "esp_crt_bundle.h"
#include "lwip/inet.h"

static ep_network_config config;
static esp_netif_t *station_netif, *ap_netif;
static esp_websocket_client_handle_t websocket;
static httpd_handle_t server;
static void (*receive_message)(cJSON *);
static void (*server_connected)(void);
static char identity[40], ap_ssid[33], csrf[33], ws_header[180];
static char ws_rx[32768];
static size_t ws_used;
static volatile bool wifi_connected, ws_connected, setup_active, setup_requested, scan_busy;
static bool ws_started, configured, setup_pending;
static volatile uint32_t connected_at, reboot_at;
static uint32_t disconnected_at, reconnect_at;
static unsigned retry_seconds = 1;

extern const char setup_html_start[] asm("_binary_setup_html_start");
extern const char setup_html_end[] asm("_binary_setup_html_end");

static uint32_t seconds(void) { return (uint32_t)(esp_timer_get_time() / 1000000); }

static void read_config(void) {
    nvs_handle_t handle;
    ESP_ERROR_CHECK(nvs_open("epapers", NVS_READWRITE, &handle));
    size_t size = sizeof(config);
    if (nvs_get_blob(handle, "network", &config, &size) != ESP_OK || size != sizeof(config) || config.version != 1) {
        memset(&config, 0, sizeof(config)); config.version = 1;
        size = sizeof(config.ssid); nvs_get_str(handle, "ssid", config.ssid, &size);
        size = sizeof(config.password); nvs_get_str(handle, "password", config.password, &size);
        size = sizeof(config.url); nvs_get_str(handle, "url", config.url, &size);
        size = sizeof(config.token); nvs_get_str(handle, "token", config.token, &size);
    }
    config.ssid[32] = 0; config.password[64] = 0; config.url[255] = 0; config.token[127] = 0;
    uint8_t pending = 0;
    nvs_get_u8(handle, "setup_pending", &pending); setup_pending = pending;
    if (nvs_erase_key(handle, "ap_password") == ESP_OK) nvs_commit(handle);
    nvs_close(handle);
    configured = config.ssid[0] && config.url[0] && config.token[0];
}

bool network_configure(const cJSON *data, char *error, size_t capacity) {
    ep_network_config next;
    if (!ep_network_config_update(&config, data, &next, error, capacity)) {
        memset(&next, 0, sizeof(next)); return false;
    }
    nvs_handle_t handle;
    esp_err_t result = nvs_open("epapers", NVS_READWRITE, &handle);
    if (result == ESP_OK) {
        result = nvs_set_blob(handle, "network", &next, sizeof(next));
        if (result == ESP_OK) result = nvs_set_u8(handle, "setup_pending", 1);
        if (result == ESP_OK) result = nvs_commit(handle);
        nvs_close(handle);
    }
    memset(&next, 0, sizeof(next));
    if (result != ESP_OK) { snprintf(error, capacity, "配置保存失败，请重试"); return false; }
    reboot_at = seconds() + 3;
    return true;
}

void network_add_status(cJSON *data) {
    cJSON_AddBoolToObject(data, "wifi_connected", wifi_connected);
    cJSON_AddBoolToObject(data, "server_connected", ws_connected);
    cJSON_AddBoolToObject(data, "setup_active", setup_active);
    cJSON_AddStringToObject(data, "ssid", config.ssid);
    esp_netif_ip_info_t info;
    char ip[16] = {0};
    if (wifi_connected && esp_netif_get_ip_info(station_netif, &info) == ESP_OK)
        snprintf(ip, sizeof(ip), IPSTR, IP2STR(&info.ip));
    cJSON_AddStringToObject(data, "ip", ip);
    wifi_ap_record_t ap;
    if (wifi_connected && esp_wifi_sta_get_ap_info(&ap) == ESP_OK)
        cJSON_AddNumberToObject(data, "wifi_rssi", ap.rssi);
}

void network_send(const char *text) {
    if (websocket && esp_websocket_client_is_connected(websocket))
        esp_websocket_client_send_text(websocket, text, strlen(text), pdMS_TO_TICKS(3000));
}

static void socket_event(void *arg, esp_event_base_t base, int32_t event, void *raw) {
    if (event == WEBSOCKET_EVENT_CONNECTED) {
        ws_used = 0; ws_connected = true; connected_at = seconds(); server_connected();
    }
    if (event == WEBSOCKET_EVENT_DISCONNECTED || event == WEBSOCKET_EVENT_ERROR) {
        ws_connected = false; connected_at = 0;
    }
    if (event != WEBSOCKET_EVENT_DATA) return;
    esp_websocket_event_data_t *data = raw;
    if (data->op_code != 1 && data->op_code != 0) return;
    if (data->payload_len >= sizeof(ws_rx) || data->payload_offset + data->data_len >= sizeof(ws_rx)) {
        ws_used = 0; return;
    }
    if (data->payload_offset == 0) ws_used = 0;
    memcpy(ws_rx + data->payload_offset, data->data_ptr, data->data_len);
    ws_used = data->payload_offset + data->data_len;
    if (ws_used == data->payload_len) {
        ws_rx[ws_used] = 0;
        cJSON *item = cJSON_Parse(ws_rx);
        if (item) { receive_message(item); cJSON_Delete(item); }
        ws_used = 0;
    }
}

static void wifi_event(void *arg, esp_event_base_t base, int32_t event, void *data) {
    if (base == WIFI_EVENT && event == WIFI_EVENT_STA_START && configured) esp_wifi_connect();
    if (base == WIFI_EVENT && event == WIFI_EVENT_STA_DISCONNECTED) {
        wifi_connected = false; ws_connected = false; connected_at = 0;
    }
    if (base == IP_EVENT && event == IP_EVENT_STA_GOT_IP) {
        wifi_connected = true; retry_seconds = 1;
        if (websocket && !ws_started) {
            if (esp_websocket_client_start(websocket) == ESP_OK) ws_started = true;
        }
    }
}

static esp_err_t json_response(httpd_req_t *req, cJSON *data, const char *status) {
    char *text = cJSON_PrintUnformatted(data); cJSON_Delete(data);
    if (!text) return httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "Out of memory");
    httpd_resp_set_status(req, status);
    httpd_resp_set_type(req, "application/json; charset=utf-8");
    httpd_resp_set_hdr(req, "Cache-Control", "no-store");
    httpd_resp_set_hdr(req, "X-Content-Type-Options", "nosniff");
    esp_err_t result = httpd_resp_send(req, text, HTTPD_RESP_USE_STRLEN);
    free(text); return result;
}

static esp_err_t json_error(httpd_req_t *req, const char *status, const char *message) {
    cJSON *data = cJSON_CreateObject(); cJSON_AddStringToObject(data, "error", message);
    return json_response(req, data, status);
}

static esp_err_t page_get(httpd_req_t *req) {
    httpd_resp_set_type(req, "text/html; charset=utf-8");
    httpd_resp_set_hdr(req, "Cache-Control", "no-store");
    httpd_resp_set_hdr(req, "X-Content-Type-Options", "nosniff");
    httpd_resp_set_hdr(req, "Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'");
    return httpd_resp_send(req, setup_html_start, setup_html_end - setup_html_start - 1);
}

static esp_err_t status_get(httpd_req_t *req) {
    cJSON *data = cJSON_CreateObject(); network_add_status(data);
    cJSON_AddStringToObject(data, "gateway_id", identity);
    cJSON_AddStringToObject(data, "firmware", esp_app_get_description()->version);
    cJSON_AddStringToObject(data, "server_url", config.url);
    cJSON_AddBoolToObject(data, "token_configured", config.token[0]);
    cJSON_AddBoolToObject(data, "password_configured", config.password[0]);
    cJSON_AddStringToObject(data, "csrf", csrf);
    cJSON_AddStringToObject(data, "ap_ssid", ap_ssid);
    unsigned remaining = connected_at && seconds() < connected_at + 60 ? connected_at + 60 - seconds() : 0;
    cJSON_AddNumberToObject(data, "hotspot_seconds", remaining);
    cJSON_AddBoolToObject(data, "restarting", reboot_at != 0);
    return json_response(req, data, "200 OK");
}

static bool mutation_allowed(httpd_req_t *req) {
    char value[256], expected[256], host[200];
    if (!setup_active || httpd_req_get_hdr_value_str(req, "X-Setup-Token", value, sizeof(value)) != ESP_OK || strcmp(value, csrf)) return false;
    if (httpd_req_get_hdr_value_len(req, "Origin")) {
        if (httpd_req_get_hdr_value_str(req, "Host", host, sizeof(host)) != ESP_OK ||
            httpd_req_get_hdr_value_str(req, "Origin", value, sizeof(value)) != ESP_OK) return false;
        snprintf(expected, sizeof(expected), "http://%s", host);
        if (strcmp(value, expected)) return false;
    }
    return true;
}

static esp_err_t config_post(httpd_req_t *req) {
    if (!mutation_allowed(req)) return json_error(req, "403 Forbidden", "配网页面已过期，请刷新后重试");
    if (ota_busy() || ble_busy() || reboot_at) return json_error(req, "409 Conflict", "网关正在工作，请稍后重试");
    char content_type[64];
    if (httpd_req_get_hdr_value_str(req, "Content-Type", content_type, sizeof(content_type)) != ESP_OK ||
        strncmp(content_type, "application/json", 16)) return json_error(req, "415 Unsupported Media Type", "请求格式无效");
    if (!req->content_len || req->content_len > 2048) return json_error(req, "413 Payload Too Large", "配置内容过长");
    char *body = calloc(1, req->content_len + 1);
    if (!body) return json_error(req, "503 Service Unavailable", "网关内存不足，请稍后重试");
    size_t used = 0;
    while (used < req->content_len) {
        int count = httpd_req_recv(req, body + used, req->content_len - used);
        if (count <= 0) { memset(body, 0, req->content_len); free(body); return ESP_FAIL; }
        used += count;
    }
    const char *end = NULL;
    cJSON *data = strstr(body, "\\u0000") || memchr(body, 0, used) ? NULL : cJSON_ParseWithLengthOpts(body, used + 1, &end, true);
    char error[192] = "配置格式无效";
    bool ok = data && network_configure(data, error, sizeof(error));
    cJSON_Delete(data); memset(body, 0, used); free(body);
    if (!ok) return json_error(req, "422 Unprocessable Entity", error);
    cJSON *result = cJSON_CreateObject(); cJSON_AddBoolToObject(result, "saved", true);
    return json_response(req, result, "200 OK");
}

static esp_err_t scan_get(httpd_req_t *req) {
    if (!setup_active || reboot_at) return json_error(req, "409 Conflict", "请稍后再扫描");
    scan_busy = true;
    if (configured && !wifi_connected) esp_wifi_disconnect();
    wifi_scan_config_t scan = {.show_hidden = false};
    esp_err_t result = esp_wifi_scan_start(&scan, true);
    scan_busy = false; reconnect_at = seconds() + 3;
    if (result != ESP_OK) return json_error(req, "503 Service Unavailable", "Wi-Fi 正在连接，请稍后再扫描或手动输入名称");
    uint16_t count = 24;
    wifi_ap_record_t *records = calloc(count, sizeof(*records));
    if (!records) { esp_wifi_clear_ap_list(); return json_error(req, "503 Service Unavailable", "网关内存不足"); }
    result = esp_wifi_scan_get_ap_records(&count, records);
    cJSON *data = cJSON_CreateObject(), *networks = cJSON_AddArrayToObject(data, "networks");
    if (result == ESP_OK) for (unsigned i = 0; i < count; ++i) {
        records[i].ssid[32] = 0;
        if (!records[i].ssid[0]) continue;
        bool duplicate = false;
        for (unsigned j = 0; j < i; ++j) if (!strcmp((const char *)records[i].ssid, (const char *)records[j].ssid)) duplicate = true;
        if (duplicate) continue;
        cJSON *ap = cJSON_CreateObject();
        cJSON_AddStringToObject(ap, "ssid", (const char *)records[i].ssid);
        cJSON_AddNumberToObject(ap, "rssi", records[i].rssi);
        cJSON_AddBoolToObject(ap, "open", records[i].authmode == WIFI_AUTH_OPEN);
        cJSON_AddItemToArray(networks, ap);
    }
    free(records); return json_response(req, data, "200 OK");
}

static void dns_task(void *arg) {
    int fd = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
    struct sockaddr_in bind_address = {.sin_family = AF_INET, .sin_port = htons(53), .sin_addr.s_addr = inet_addr("192.168.4.1")};
    struct timeval timeout = {.tv_sec = 1};
    setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));
    if (fd < 0 || bind(fd, (struct sockaddr *)&bind_address, sizeof(bind_address))) {
        if (fd >= 0) close(fd); vTaskDelete(NULL); return;
    }
    uint8_t packet[512];
    while (setup_active) {
        struct sockaddr_in client; socklen_t client_size = sizeof(client);
        int n = recvfrom(fd, packet, sizeof(packet) - 16, 0, (struct sockaddr *)&client, &client_size);
        if (n < 17 || packet[4] != 0 || packet[5] != 1 || packet[2] & 0x80) continue;
        size_t end = 12;
        while (end < n && packet[end]) {
            unsigned label = packet[end];
            if (label > 63 || end + label + 1 >= n) { end = n; break; }
            end += label + 1;
        }
        if (end + 5 > n) continue;
        ++end;
        bool ipv4 = packet[end] == 0 && packet[end + 1] == 1 && packet[end + 2] == 0 && packet[end + 3] == 1;
        end += 4;
        packet[2] = 0x81; packet[3] = 0x80; packet[6] = 0; packet[7] = ipv4;
        memset(packet + 8, 0, 4);
        if (ipv4) {
            const uint8_t answer[] = {0xc0, 0x0c, 0, 1, 0, 1, 0, 0, 0, 30, 0, 4, 192, 168, 4, 1};
            memcpy(packet + end, answer, sizeof(answer)); end += sizeof(answer);
        }
        sendto(fd, packet, end, 0, (struct sockaddr *)&client, client_size);
    }
    close(fd); vTaskDelete(NULL);
}

static void start_setup(void) {
    if (setup_active || reboot_at) return;
    wifi_config_t ap = {0};
    memcpy(ap.ap.ssid, ap_ssid, strlen(ap_ssid));
    ap.ap.ssid_len = strlen(ap_ssid); ap.ap.channel = 1; ap.ap.max_connection = 3;
    ap.ap.authmode = WIFI_AUTH_OPEN;
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_APSTA));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_AP, &ap));
    esp_netif_dns_info_t dns = {.ip = {.type = ESP_IPADDR_TYPE_V4}};
    dns.ip.u_addr.ip4.addr = inet_addr("192.168.4.1");
    uint8_t offer = 2; /* DHCPS_OFFER_DNS */
    esp_netif_dhcps_stop(ap_netif);
    esp_netif_dhcps_option(ap_netif, ESP_NETIF_OP_SET, ESP_NETIF_DOMAIN_NAME_SERVER, &offer, sizeof(offer));
    esp_netif_set_dns_info(ap_netif, ESP_NETIF_DNS_MAIN, &dns);
    esp_netif_dhcps_start(ap_netif);
    setup_active = true;
    struct ifreq iface = {0};
    ESP_ERROR_CHECK(esp_netif_get_netif_impl_name(ap_netif, iface.ifr_name));
    httpd_config_t http = HTTPD_DEFAULT_CONFIG();
    http.stack_size = 8192; http.max_uri_handlers = 5; http.max_open_sockets = 4;
    http.lru_purge_enable = true; http.recv_wait_timeout = 5; http.send_wait_timeout = 5;
    http.uri_match_fn = httpd_uri_match_wildcard; http.if_name = &iface;
    ESP_ERROR_CHECK(httpd_start(&server, &http));
    const httpd_uri_t handlers[] = {
        {.uri = "/api/status", .method = HTTP_GET, .handler = status_get},
        {.uri = "/api/scan", .method = HTTP_GET, .handler = scan_get},
        {.uri = "/api/config", .method = HTTP_POST, .handler = config_post},
        {.uri = "/*", .method = HTTP_GET, .handler = page_get}
    };
    for (unsigned i = 0; i < sizeof(handlers) / sizeof(handlers[0]); ++i) ESP_ERROR_CHECK(httpd_register_uri_handler(server, &handlers[i]));
    xTaskCreate(dns_task, "setup-dns", 3072, NULL, 3, NULL);
    /* Setup hotspot is intentionally open, per the device owner preference. */
    printf("EP_SETUP:{\"ssid\":\"%s\",\"open\":true,\"url\":\"http://192.168.4.1\"}\n", ap_ssid); fflush(stdout);
}

void network_open_setup(void) {
    setup_requested = true;
    if (setup_active) {
        printf("EP_SETUP:{\"ssid\":\"%s\",\"open\":true,\"url\":\"http://192.168.4.1\"}\n", ap_ssid); fflush(stdout);
    }
}

static void monitor(void *arg) {
    uint32_t button_since = 0; bool button_handled = false;
    disconnected_at = seconds();
    while (true) {
        uint32_t now = seconds();
        if (reboot_at && now >= reboot_at && !ota_busy() && !ble_busy()) esp_restart();
        if (!gpio_get_level(GPIO_NUM_0)) {
            if (!button_since) button_since = now;
            if (!button_handled && now - button_since >= 3) { setup_requested = true; button_handled = true; }
        } else { button_since = 0; button_handled = false; }
        if (!ws_connected) {
            if (!disconnected_at) disconnected_at = now;
            if (now - disconnected_at >= 60) setup_requested = true;
        } else disconnected_at = 0;
        if (setup_requested) { start_setup(); setup_requested = false; }
        if (configured && !wifi_connected && !scan_busy && now >= reconnect_at) {
            esp_wifi_connect(); reconnect_at = now + retry_seconds;
            retry_seconds = retry_seconds < 15 ? retry_seconds * 2 : 30;
        }
        if (setup_active && ws_connected && connected_at && now - connected_at >= 60 && !reboot_at && !scan_busy) {
            setup_active = false;
            if (server) { httpd_stop(server); server = NULL; }
            esp_wifi_set_mode(WIFI_MODE_STA);
            nvs_handle_t handle;
            if (nvs_open("epapers", NVS_READWRITE, &handle) == ESP_OK) {
                nvs_set_u8(handle, "setup_pending", 0); nvs_commit(handle); nvs_close(handle);
            }
        }
        vTaskDelay(pdMS_TO_TICKS(200));
    }
}

void network_start(const char *gateway_id, void (*message)(cJSON *), void (*connected)(void)) {
    snprintf(identity, sizeof(identity), "%s", gateway_id);
    snprintf(ap_ssid, sizeof(ap_ssid), "epapers-%s", gateway_id + strlen(gateway_id) - 6);
    receive_message = message; server_connected = connected; read_config();
    for (unsigned i = 0; i < 4; ++i) snprintf(csrf + i * 8, 9, "%08lx", (unsigned long)esp_random());
    ESP_ERROR_CHECK(esp_netif_init()); ESP_ERROR_CHECK(esp_event_loop_create_default());
    station_netif = esp_netif_create_default_wifi_sta(); ap_netif = esp_netif_create_default_wifi_ap();
    wifi_init_config_t init = WIFI_INIT_CONFIG_DEFAULT(); ESP_ERROR_CHECK(esp_wifi_init(&init));
    ESP_ERROR_CHECK(esp_wifi_set_storage(WIFI_STORAGE_RAM));
    ESP_ERROR_CHECK(esp_event_handler_register(WIFI_EVENT, ESP_EVENT_ANY_ID, wifi_event, NULL));
    ESP_ERROR_CHECK(esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP, wifi_event, NULL));
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    if (configured) {
        wifi_config_t station = {0};
        memcpy(station.sta.ssid, config.ssid, strlen(config.ssid));
        memcpy(station.sta.password, config.password, strlen(config.password));
        station.sta.threshold.authmode = config.password[0] ? WIFI_AUTH_WPA2_PSK : WIFI_AUTH_OPEN;
        station.sta.sae_pwe_h2e = WPA3_SAE_PWE_BOTH;
        ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &station));
        snprintf(ws_header, sizeof(ws_header), "Authorization: Bearer %s\r\n", config.token);
        esp_websocket_client_config_t ws = {.uri = config.url, .headers = ws_header, .reconnect_timeout_ms = 5000,
            .network_timeout_ms = 10000, .buffer_size = 2048, .crt_bundle_attach = esp_crt_bundle_attach};
        websocket = esp_websocket_client_init(&ws);
        ESP_ERROR_CHECK(esp_websocket_register_events(websocket, WEBSOCKET_EVENT_ANY, socket_event, NULL));
    }
    ESP_ERROR_CHECK(esp_wifi_start());
    gpio_config_t button = {.pin_bit_mask = 1ULL << GPIO_NUM_0, .mode = GPIO_MODE_INPUT, .pull_up_en = GPIO_PULLUP_ENABLE};
    ESP_ERROR_CHECK(gpio_config(&button));
    setup_requested = !configured || setup_pending;
    xTaskCreate(monitor, "network-monitor", 4096, NULL, 4, NULL);
}
