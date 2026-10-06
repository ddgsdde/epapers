/* Project-owned GATT schema; SDK6 supplies attribute database creation. */
#include "user_custs1_def.h"
#include "att.h"
#include "co_utils.h"
static const uint16_t service_decl = ATT_DECL_PRIMARY_SERVICE;
static const uint16_t char_decl = ATT_DECL_CHARACTERISTIC;
static const att_svc_desc128_t service = {0x01,0xe0,0xbe,0x1b,0xa4,0x94,0xb4,0x9c,0x48,0x4c,0x31,0x6e,0x01,0x00,0xa0,0xe3};
static const uint8_t rx[16] = {0x01,0xe0,0xbe,0x1b,0xa4,0x94,0xb4,0x9c,0x48,0x4c,0x31,0x6e,0x02,0x00,0xa0,0xe3};
static const uint8_t tx[16] = {0x01,0xe0,0xbe,0x1b,0xa4,0x94,0xb4,0x9c,0x48,0x4c,0x31,0x6e,0x03,0x00,0xa0,0xe3};
const uint8_t custs1_services[] = {SVC1_IDX_SVC,CUSTS1_IDX_NB};
const uint8_t custs1_services_size = 1;
const uint16_t custs1_att_max_nb = CUSTS1_IDX_NB;
const struct attm_desc_128 custs1_att_db[CUSTS1_IDX_NB] = {
 [SVC1_IDX_SVC] = {(uint8_t*)&service_decl,ATT_UUID_128_LEN,PERM(RD,ENABLE),sizeof(service),sizeof(service),(uint8_t*)&service},
 [EP_RX_CHAR] = {(uint8_t*)&char_decl,ATT_UUID_16_LEN,PERM(RD,ENABLE),0,0,NULL},
 [EP_RX_VAL] = {rx,ATT_UUID_128_LEN,PERM(WR,ENABLE)|PERM(WRITE_REQ,ENABLE),208,0,NULL},
 [EP_TX_CHAR] = {(uint8_t*)&char_decl,ATT_UUID_16_LEN,PERM(RD,ENABLE),0,0,NULL},
 [EP_TX_VAL] = {tx,ATT_UUID_128_LEN,PERM(RD,ENABLE),80,0,NULL},
};
