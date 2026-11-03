#include "../include/meeting_output_audio.h"
int16_t meeting_output_audio_sample(meeting_output_audio *s, int16_t x,
    uint64_t frame, uint32_t epoch, unsigned enabled)
{
    if(!s)return x;
    /* Prime the filter at boot/gaps; a DC offset must not become a loud click.
     * Frame is the 48 kHz source counter, so adjacent 16 kHz samples differ by 3. */
    if(!s->ready || s->epoch!=epoch || frame!=s->frame+3u){
        s->previous=x;s->filtered_q8=0;s->ready=1;
    }
    s->filtered_q8=(x-s->previous)*256+s->filtered_q8-s->filtered_q8/256;
    s->previous=x;s->frame=frame;s->epoch=epoch;++s->samples;
    if(!enabled)return x;
    /* Bounds: |filtered_q8| < 2^25 and gain 16, safely inside signed int32.
     * Divide toward zero symmetrically; signed left shifts are never used. */
    int32_t y=(s->filtered_q8*(1<<MEETING_OUTPUT_GAIN_SHIFT))/256;
    if(y>MEETING_OUTPUT_LIMIT){++s->clipped;return MEETING_OUTPUT_LIMIT;}
    if(y< -MEETING_OUTPUT_LIMIT){++s->clipped;return -MEETING_OUTPUT_LIMIT;}
    return (int16_t)y;
}
