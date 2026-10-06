idf_component_register(SRCS "gateway.c" "ble.c" "ota.c" "../../common/ep_core.c" INCLUDE_DIRS "." "../../common" REQUIRES app_update esp_app_format bt nvs_flash esp_wifi esp_netif esp_event esp_timer cjson mbedtls esp_websocket_client)
target_compile_options(${COMPONENT_LIB} PRIVATE -Wno-misleading-indentation)
