#ifndef ARRAY128_MONO_DECIMATOR_H
#define ARRAY128_MONO_DECIMATOR_H
#include <stddef.h>
#include <stdint.h>

#define ARRAY128_DECIMATOR_TAPS 127u
#define ARRAY128_DECIMATOR_HISTORY 128u
#define ARRAY128_DECIMATOR_STORAGE (2u * ARRAY128_DECIMATOR_HISTORY)
#define ARRAY128_DECIMATOR_FACTOR 3u
#define ARRAY128_DECIMATOR_INPUT_HZ 48000u
#define ARRAY128_DECIMATOR_OUTPUT_HZ 16000u
#define ARRAY128_DECIMATOR_DELAY_INPUT_SAMPLES 63u

typedef struct {
    /* Mirrored ring: logical sample i is also stored at i+128. */
    int16_t history[ARRAY128_DECIMATOR_STORAGE];
    uint32_t write_index,phase,saturated;
} array128_mono_decimator;

/* Reset on every input epoch/frame discontinuity. Initial history is zero.
 * Outputs occur on input indices 2,5,8,... from reset, after FIR filtering.
 * The FIR has 63 input samples (1.3125 ms) group delay; initial outputs contain
 * zero-padding until input index 126. Do not confuse reset padding with audio.
 */
void array128_mono_decimator_reset(array128_mono_decimator *state);
/* 1=output available; 0=accepted input, no output; -1=bad argument. */
int array128_mono_decimator_push(array128_mono_decimator *state,int16_t input,int16_t *output);
/* Process up to input_count while never dropping an output at capacity.
 * Returns output count and writes consumed input count. The caller must retry
 * any unconsumed input. Separate input/output buffers are required.
 */
size_t array128_mono_decimate_block(array128_mono_decimator *state,
    const int16_t *input,size_t input_count,int16_t *output,size_t output_capacity,size_t *consumed);
#endif
