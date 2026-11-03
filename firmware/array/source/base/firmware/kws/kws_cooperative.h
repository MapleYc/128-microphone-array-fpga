#ifndef ARRAY128_KWS_COOPERATIVE_H
#define ARRAY128_KWS_COOPERATIVE_H
#include "kws_fixed.h"

enum { KWS_IDLE = 0, KWS_CONV = 1, KWS_FC = 2, KWS_SOFTMAX = 3, KWS_DONE = 4 };
typedef struct {
    int8_t features[KWS_FEATURE_BYTES]; /* immutable per-inference snapshot */
    int8_t scratch[KWS_SCRATCH_BYTES];
    int8_t logits[KWS_CLASSES];
    int8_t output[KWS_CLASSES];
    uint32_t stage;
    uint32_t row_or_class;
} KwsCooperative;

/* Init before first use. Keep audio input pumping between every step.
 * A step processes one convolution row, one FC class, or final softmax.
 * These boundaries bound instruction work, not a verified wall-clock deadline.
 * Link this implementation OR kws_fixed.c in production, not both: each holds
 * its own trained constants. The fixed implementation is a regression oracle.
 */
void kws_cooperative_init(KwsCooperative* state);
int kws_cooperative_begin(KwsCooperative* state, const int8_t* features);
int kws_cooperative_step(KwsCooperative* state); /* 1 done, 0 more, -1 idle/error */
#endif
