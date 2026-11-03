#ifndef MEETING_FEATURES_H
#define MEETING_FEATURES_H
#include "meeting_audio_session.h"
#include <stdint.h>
#define MEETING_FEATURE_ROWS 49u
#define MEETING_FEATURE_BINS 40u
typedef struct {
    int16_t window[480];
    int8_t features[1960];
    uint32_t epoch,rows;
    uint64_t previous_block_start,window_start,window_end;
    uint8_t have_block;
} meeting_features;
/* Uses the official frontend's single static instance. One owner only. */
void meeting_features_reset(meeting_features *state);
/* -1 invalid metadata, 0 first block buffered, 1 partial feature history,
 * 2 complete 49x40 window. Call reset on source discontinuities.
 * Source PCM blocks are 320 samples at 16k. Returned metadata is corrected
 * by the 63-source-frame FIR group delay; windows overlap by 160 samples. */
int meeting_features_feed(meeting_features *state,const meeting_audio_block *block);
#endif
