#ifndef EP_PANEL_H
#define EP_PANEL_H
#include "ep_core.h"
/* Nonzero model is required before any refresh. A detector never activates HV. */
typedef struct {uint16_t lut_size;uint8_t group,error;uint32_t refreshes,full_refreshes;int16_t temperature_centi;uint8_t last_reason;uint64_t temperature_sample_ms;} panel_diagnostic;
extern volatile panel_diagnostic panel_diag;
void panel_detect(uint8_t group);
bool panel_display(const uint8_t *blob,size_t length,void *context);
void panel_done(bool success);
#endif
