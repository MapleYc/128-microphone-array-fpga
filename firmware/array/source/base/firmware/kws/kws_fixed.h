#ifndef ARRAY128_KWS_FIXED_H
#define ARRAY128_KWS_FIXED_H
#include <stdint.h>

enum { KWS_FEATURE_BYTES = 1960, KWS_SCRATCH_BYTES = 4000, KWS_CLASSES = 4 };
enum { KWS_SILENCE = 0, KWS_UNKNOWN = 1, KWS_YES = 2, KWS_NO = 3 };

/* Features MUST match the original 16kHz, 30ms / 20ms microfrontend.
 * This routine does NOT accept PCM, arbitrary mel bins, or arbitrary MFCC.
 * scratch must not overlap features. Labels are silence, unknown, yes, no.
 * logits are exact int8 classifier logits; output uses approximate Q22 softmax.
 * No dynamic allocation, no floating point, and no hardware DIV instruction.
 */
void kws_fixed_infer(const int8_t features[KWS_FEATURE_BYTES],
                     int8_t scratch[KWS_SCRATCH_BYTES],
                     int8_t logits[KWS_CLASSES], int8_t output[KWS_CLASSES]);
#endif
