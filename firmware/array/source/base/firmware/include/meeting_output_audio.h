#ifndef MEETING_OUTPUT_AUDIO_H
#define MEETING_OUTPUT_AUDIO_H
#include <stdint.h>
/* Output copy only: the recognition and localization inputs are untouched.
 * PCM16 -> Q8 DC blocker -> x16 -> saturating PCM16. No lost ADC bits recovered. */
#define MEETING_OUTPUT_GAIN_SHIFT 4u
#define MEETING_OUTPUT_LIMIT 30000
typedef struct {
    int32_t previous, filtered_q8;
    uint64_t frame;
    uint32_t epoch, samples, clipped;
    unsigned ready;
} meeting_output_audio;
int16_t meeting_output_audio_sample(meeting_output_audio *state, int16_t input,
    uint64_t frame, uint32_t epoch, unsigned enabled);
#endif
