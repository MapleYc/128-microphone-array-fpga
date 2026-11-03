#ifndef MEETING_AUDIO_SESSION_H
#define MEETING_AUDIO_SESSION_H
#include "array128_mono_decimator.h"
#include "array128_mono_stream.h"
#include <stdint.h>

#define MEETING_AUDIO_BLOCK_SAMPLES 320u
#define MEETING_AUDIO_WARMUP_OUTPUTS 42u
#define MEETING_AUDIO_GROUP_DELAY_FRAMES 63u

typedef struct {
    int16_t pcm16[MEETING_AUDIO_BLOCK_SAMPLES];
    uint32_t epoch;
    /* Latest 48k input frame used by the first/last output in this block.
     * Subtract GROUP_DELAY_FRAMES for the acoustic center timestamps.
     * support_frame_start includes the oldest FIR history contribution.
     */
    uint64_t source_frame_start,source_frame_end,support_frame_start;
} meeting_audio_block;

typedef struct {
    array128_mono_decimator decimator;
    meeting_audio_block block;
    uint64_t last_frame;
    uint32_t epoch,count,warmup_remaining;
    uint32_t discontinuities,rejected_samples,discarded_partial_samples,completed_blocks;
    uint8_t have_epoch,have_frame;
} meeting_audio_session;

void meeting_audio_session_init(meeting_audio_session *session);
/* 1 complete block, 0 accepted/no block, -1 invalid argument,
 * -2 rejected duplicate/backward frame or stale epoch.
 * *ready is NULL except on return 1. Returned data belongs to session and must
 * be consumed/copied before the next feed call. One owner; zero allocation.
 */
int meeting_audio_session_feed(meeting_audio_session *session,
    const array128_mono_sample *input,const meeting_audio_block **ready);
#endif
