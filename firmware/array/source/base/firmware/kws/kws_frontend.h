#ifndef ARRAY128_KWS_FRONTEND_H
#define ARRAY128_KWS_FRONTEND_H
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
/* Fixed official frontend: mono signed16 PCM, 16kHz, 480-sample window,
 * 320-sample hop, 40 int8 bins. Retain noise state across overlapping windows.
 * Single instance, non-reentrant, no heap. Reset after discontinuity.
 */
void kws_frontend_reset(void);
void kws_frontend_frame(const int16_t pcm[480], int8_t feature[40]);
#ifdef __cplusplus
}
#endif
#endif
