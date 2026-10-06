#ifndef EP_USER_PERIPHERAL_H
#define EP_USER_PERIPHERAL_H
#include "app.h"
#include "app_task.h"
#include "gapc_task.h"
#include "app_default_handlers.h"
#include "app_callback.h"
void user_app_init(void);
void user_app_db_init_complete(void);
void user_app_adv_start(void);
void user_app_connection(uint8_t index,struct gapc_connection_req_ind const *param);
void user_app_disconnect(struct gapc_disconnect_ind const *param);
void user_app_adv_undirect_complete(uint8_t status);
void user_catch_rest_hndl(ke_msg_id_t msgid,void const *param,ke_task_id_t dest,ke_task_id_t source);
#endif
