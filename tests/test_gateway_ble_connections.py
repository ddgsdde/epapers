"""Run actual gateway connection code with delayed and reused BLE links."""

import ctypes
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def links(tmp_path_factory):
    source = (ROOT / "firmware/gateway/main/ble.c").read_text()
    # Keep the real event cases and connection functions; mock only NimBLE calls.
    events = source[source.index(" case BLE_GAP_EVENT_CONNECT:"):
                    source.index(" case BLE_GAP_EVENT_DISC:")]
    connect = source[source.index("static void disconnect_link("):
                     source.index("static uint32_t read32(")]
    work = tmp_path_factory.mktemp("gateway-links")
    harness = work / "links.c"
    harness.write_text("""
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <strings.h>
#define BLE_HS_CONN_HANDLE_NONE 65535
#define BLE_ADDR_PUBLIC 0
#define BLE_ADDR_RANDOM 1
#define BLE_HS_ENOTCONN 7
#define BLE_ERR_REM_USER_CONN_TERM 19
#define BLE_ERR_ACL_CONN_EXISTS 11
#define BLE_HS_HCI_ERR(x) (512+(x))
#define BLE_GAP_EVENT_CONNECT 1
#define BLE_GAP_EVENT_DISCONNECT 2
#define pdMS_TO_TICKS(x) (x)
typedef struct {uint8_t type,val[6];} ble_addr_t;
struct ble_gap_event {int type;struct {int status;uint16_t conn_handle;} connect;
 struct {struct {uint16_t conn_handle;} conn;} disconnect;};
struct ble_gap_conn_desc {uint16_t conn_handle;};
typedef struct {ble_addr_t addr;char text[18];} seen_device;
static seen_device seen[20];static unsigned seen_count;
static uint16_t connection,service_start,service_end,discovered_handle,rx_handle,tx_handle;
static uint8_t own_type;static const char *ota_phase;
static struct {int u;} service_uuid,rx_uuid,tx_uuid;
static int operation_error,mode,signals,connects,terminates,delays,finds;
static void signal(int error){operation_error=error;++signals;}
static void clear_wait(void){operation_error=0;}
static bool wait_for(int rc,int timeout){(void)timeout;if(rc)operation_error=rc;return !rc;}
static int gap_event(struct ble_gap_event *event,void *arg){(void)arg;switch(event->type){
""" + events + """
 default:break;}return 0;}
static void inject_disconnect(uint16_t handle){struct ble_gap_event e={.type=2};e.disconnect.conn.conn_handle=handle;gap_event(&e,NULL);}
static void vTaskDelay(unsigned ms){(void)ms;if(++delays==3&&mode!=3)inject_disconnect(11);}
static int ble_gap_terminate(uint16_t handle,int reason){(void)handle;(void)reason;++terminates;return 0;}
static int ble_gap_disc_cancel(void){return 0;}
static int ble_gap_conn_cancel(void){return 0;}
static int ble_gap_conn_find_by_addr(const ble_addr_t *addr,struct ble_gap_conn_desc *out){
 (void)addr;++finds;if(mode==1||(mode==2&&connects)){out->conn_handle=42;return 0;}return 7;}
static int ble_gap_connect(uint8_t own,const ble_addr_t *addr,int timeout,void *params,int (*cb)(struct ble_gap_event *,void *),void *arg){
 (void)own;(void)addr;(void)timeout;(void)params;++connects;
 if(mode==2)return BLE_HS_HCI_ERR(BLE_ERR_ACL_CONN_EXISTS);
 if(connection!=BLE_HS_CONN_HANDLE_NONE)return 99;
 struct ble_gap_event e={.type=1};e.connect.conn_handle=43;cb(&e,arg);return 0;}
static int mtu_cb,service_cb,char_cb;
static int ble_gattc_exchange_mtu(uint16_t handle,int cb,void *arg){(void)handle;(void)cb;(void)arg;return 0;}
static unsigned ble_att_mtu(uint16_t handle){(void)handle;return 247;}
static int ble_gattc_disc_svc_by_uuid(uint16_t handle,int *uuid,int cb,void *arg){
 (void)handle;(void)uuid;(void)cb;(void)arg;service_start=1;service_end=10;return 0;}
static int ble_gattc_disc_chrs_by_uuid(uint16_t handle,uint16_t start,uint16_t end,int *uuid,int cb,void *arg){
 (void)handle;(void)start;(void)end;(void)uuid;(void)cb;(void)arg;discovered_handle=5;return 0;}
""" + connect + """
void reset(int scenario,uint16_t handle){mode=scenario;connection=handle;operation_error=signals=connects=terminates=delays=finds=0;}
int open_link(void){return connect_address("D1:B0:51:BC:41:58");}
void disconnect_event(uint16_t handle){inject_disconnect(handle);}
unsigned current_handle(void){return connection;}
unsigned signal_count(void){return signals;}
unsigned connect_count(void){return connects;}
unsigned terminate_count(void){return terminates;}
unsigned delay_count(void){return delays;}
""")
    library = work / "links.so"
    subprocess.run([
        "cc", "-shared", "-fPIC", "-Wall", "-Wextra", "-Werror",
        str(harness), "-o", str(library),
    ], check=True)
    lib = ctypes.CDLL(str(library))
    lib.reset.argtypes = [ctypes.c_int, ctypes.c_uint16]
    lib.disconnect_event.argtypes = [ctypes.c_uint16]
    return lib


def test_late_disconnect_does_not_clear_newer_link_or_signal_waiter(links):
    links.reset(0, 17)
    links.disconnect_event(16)
    assert links.current_handle() == 17 and links.signal_count() == 0
    links.disconnect_event(17)
    assert links.current_handle() == 65535 and links.signal_count() == 1


def test_existing_peer_is_reused_without_connecting_twice(links):
    links.reset(1, 65535)
    assert links.open_link() == 1
    assert links.current_handle() == 42 and links.connect_count() == 0


def test_other_peer_disconnect_completes_before_new_connection(links):
    links.reset(0, 11)
    assert links.open_link() == 1
    assert links.terminate_count() == 1 and links.delay_count() == 3
    assert links.current_handle() == 43 and links.connect_count() == 1


def test_connection_exists_race_reuses_host_link(links):
    links.reset(2, 65535)
    assert links.open_link() == 1
    assert links.current_handle() == 42 and links.connect_count() == 1


def test_failed_disconnect_prevents_another_connection(links):
    links.reset(3, 11)
    assert links.open_link() == 0
    assert links.terminate_count() == 1 and links.delay_count() == 250
    assert links.current_handle() == 11 and links.connect_count() == 0
