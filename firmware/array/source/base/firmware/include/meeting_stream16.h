#ifndef MEETING_STREAM16_H
#define MEETING_STREAM16_H
#include "meeting_audio_session.h"
typedef struct {
    meeting_audio_block block;
    uint64_t last_frame;
    uint32_t epoch,count,discontinuities,rejected,blocks;
    uint8_t initialized;
} meeting_stream16;
void meeting_stream16_init(meeting_stream16 *s);
/* 16k PL FIR output. Timestamp is newest 48k input, increments by three.
 * 1 block, 0 partial, -1 argument, -2 stale, -3 gap/epoch (sample accepted).
 * Caller resets runtime immediately on -2/-3; PL already removed warmup.
 */
int meeting_stream16_feed(meeting_stream16 *s,const array128_mono_sample *sample,
                         const meeting_audio_block **block);
#endif
