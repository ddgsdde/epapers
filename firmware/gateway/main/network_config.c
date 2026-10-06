#include "network_config.h"
#include "http_parser.h"
#include <ctype.h>
#include <stdio.h>
#include <string.h>
#include <strings.h>

static bool fail(char *error, size_t capacity, const char *message) {
    snprintf(error, capacity, "%s", message);
    return false;
}

static bool field(const cJSON *data, const char *key, char *out, size_t capacity) {
    const cJSON *value = cJSON_GetObjectItemCaseSensitive(data, key);
    if (!value) return true;
    if (!cJSON_IsString(value) || strlen(value->valuestring) >= capacity) return false;
    memcpy(out, value->valuestring, strlen(value->valuestring) + 1);
    return true;
}

static bool printable(const char *value) {
    for (const unsigned char *p = (const unsigned char *)value; *p; ++p)
        if (*p < 32 || *p == 127) return false;
    return true;
}

static bool server_url(const char *input, char *out, size_t capacity) {
    char clean[256], url[256];
    while (isspace((unsigned char)*input)) ++input;
    size_t n = strlen(input);
    while (n && isspace((unsigned char)input[n - 1])) --n;
    if (!n || n >= sizeof(clean)) return false;
    memcpy(clean, input, n); clean[n] = 0;
    for (size_t i = 0; i < n; ++i)
        if ((unsigned char)clean[i] <= 32 || (unsigned char)clean[i] >= 127 || clean[i] == '\\') return false;
    int length;
    if (!strncasecmp(clean, "http://", 7)) length = snprintf(url, sizeof(url), "ws://%s", clean + 7);
    else if (!strncasecmp(clean, "https://", 8)) length = snprintf(url, sizeof(url), "wss://%s", clean + 8);
    else if (!strncasecmp(clean, "ws://", 5)) length = snprintf(url, sizeof(url), "ws://%s", clean + 5);
    else if (!strncasecmp(clean, "wss://", 6)) length = snprintf(url, sizeof(url), "wss://%s", clean + 6);
    else if (strstr(clean, "://")) return false;
    else length = snprintf(url, sizeof(url), "ws://%s", clean);
    if (length < 0 || (size_t)length >= sizeof(url)) return false;
    struct http_parser_url parsed;
    http_parser_url_init(&parsed);
    if (http_parser_parse_url(url, strlen(url), 0, &parsed) || !(parsed.field_set & (1 << UF_HOST)) ||
        (parsed.field_set & ((1 << UF_USERINFO) | (1 << UF_QUERY) | (1 << UF_FRAGMENT))) ||
        ((parsed.field_set & (1 << UF_PORT)) && !parsed.port)) return false;
    const char *host = url + parsed.field_data[UF_HOST].off;
    size_t host_length = parsed.field_data[UF_HOST].len;
    if ((host_length == 9 && !strncmp(host, "localhost", 9)) ||
        (host_length == 9 && !strncmp(host, "127.0.0.1", 9)) ||
        (host_length == 3 && !strncmp(host, "::1", 3))) return false;
    n = strlen(url);
    while (n && url[n - 1] == '/') url[--n] = 0;
    const char *suffix = "/ws/gateway";
    if (n >= strlen(suffix) && !strcmp(url + n - strlen(suffix), suffix))
        length = snprintf(out, capacity, "%s", url);
    else length = snprintf(out, capacity, "%s%s", url, suffix);
    return length > 0 && (size_t)length < capacity;
}

bool ep_network_config_update(const ep_network_config *current, const cJSON *data,
                              ep_network_config *out, char *error, size_t capacity) {
    if (!cJSON_IsObject(data)) return fail(error, capacity, "配置格式无效");
    *out = *current;
    out->version = 1;
    char password[65] = {0}, token[128] = {0}, url[256];
    memcpy(url, current->url, sizeof(url));
    if (!field(data, "ssid", out->ssid, sizeof(out->ssid)) || !out->ssid[0] || !printable(out->ssid))
        return fail(error, capacity, "Wi-Fi 名称需为 1～32 字节");
    if (!field(data, "password", password, sizeof(password)))
        return fail(error, capacity, "Wi-Fi 密码过长或格式无效");
    const cJSON *open = cJSON_GetObjectItemCaseSensitive(data, "open_wifi");
    if (open && !cJSON_IsBool(open)) return fail(error, capacity, "无密码网络选项无效");
    if (cJSON_IsTrue(open)) out->password[0] = 0;
    else if (password[0]) memcpy(out->password, password, sizeof(password));
    else if (strcmp(out->ssid, current->ssid) || !current->ssid[0])
        return fail(error, capacity, "请填写新 Wi-Fi 的密码，或选择无密码网络");
    if (out->password[0]) {
        size_t n = strlen(out->password);
        if (n < 8) return fail(error, capacity, "Wi-Fi 密码至少需要 8 字节");
        if (n == 64) for (size_t i = 0; i < n; ++i)
            if (!isxdigit((unsigned char)out->password[i]))
                return fail(error, capacity, "64 位 Wi-Fi 密码须为十六进制 PSK");
    }
    if (!field(data, "url", url, sizeof(url)) || !server_url(url, out->url, sizeof(out->url)))
        return fail(error, capacity, "请输入工作台的局域网地址，例如 http://10.0.0.10:8099");
    if (!field(data, "token", token, sizeof(token))) return fail(error, capacity, "Token 最多为 127 个字符");
    if (token[0]) memcpy(out->token, token, sizeof(token));
    if (!out->token[0]) return fail(error, capacity, "请填写工作台的网关 Token");
    for (const unsigned char *p = (const unsigned char *)out->token; *p; ++p)
        if (*p < 33 || *p > 126) return fail(error, capacity, "Token 不能包含空格、换行或非 ASCII 字符");
    if (error && capacity) error[0] = 0;
    memset(password, 0, sizeof(password)); memset(token, 0, sizeof(token));
    return true;
}
