#ifndef MEETING_VOICE_GATE_H
#define MEETING_VOICE_GATE_H
#include "meeting_audio_session.h"

/* Acoustic activity gate, not a speech/content classifier. Measures unamplified
 * model PCM, so neither board output gain nor PC volume affects decisions. */
#define MEETING_VOICE_HISTORY 64u
#define MEETING_VOICE_FLOOR_Q8 (8u * 256u)
#define MEETING_VOICE_MIN_FRAMES 3840u /* 80 ms in the exact inference window */
#define MEETING_VOICE_RUN_FRAMES 2880u /* at least 60 ms continuous activity */
typedef struct {
    uint8_t active[MEETING_VOICE_HISTORY];
    uint32_t next,count,warmup,level_q8,noise_q8;
    uint64_t newest_start;
} meeting_voice_gate;
void meeting_voice_reset(meeting_voice_gate *s);
/* Feed each validated contiguous 320-sample/20-ms input block once. */
void meeting_voice_feed(meeting_voice_gate *s,const meeting_audio_block *b);
/* Snapshot before inference begins. Returns active milliseconds; active is
 * false for short spikes, missing history or windows outside the same segment. */
uint16_t meeting_voice_window(const meeting_voice_gate *s,uint64_t start,
                             uint64_t end,uint8_t *active);
#endif
