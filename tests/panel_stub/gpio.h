#ifndef TEST_GPIO_H
#define TEST_GPIO_H
#include <stdint.h>
typedef enum {INPUT,INPUT_PULLUP,OUTPUT} GPIO_PUPD;
#define PID_GPIO 0
void GPIO_SetActive(unsigned port,unsigned pin);
void GPIO_SetInactive(unsigned port,unsigned pin);
int GPIO_GetPinStatus(unsigned port,unsigned pin);
void GPIO_ConfigurePin(unsigned port,unsigned pin,GPIO_PUPD mode,unsigned function,int value);
#endif
