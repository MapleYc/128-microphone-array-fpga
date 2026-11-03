#include "../include/meeting_stream16.h"
void meeting_stream16_init(meeting_stream16 *s)
{
    if(!s)return;
    s->last_frame=0;s->epoch=0;s->count=0;s->discontinuities=0;
    s->rejected=0;s->blocks=0;s->initialized=0;
}
int meeting_stream16_feed(meeting_stream16 *s,const array128_mono_sample *v,const meeting_audio_block **block)
{
    int gap=0;
    if(block)*block=0;
    if(!s || !v || !block || v->frame<126u)return -1;
    if(s->initialized) {
        if(v->epoch!=s->epoch) {
            if(v->epoch-s->epoch>=UINT32_C(0x80000000)) {++s->rejected;s->count=0;return -2;}
            gap=1;
        } else {
            if(v->frame<=s->last_frame) {++s->rejected;s->count=0;return -2;}
            if(v->frame-s->last_frame!=3u)gap=1;
        }
    }
    if(gap){s->count=0;++s->discontinuities;}
    s->initialized=1;s->epoch=v->epoch;s->last_frame=v->frame;
    if(s->count==0) {
        s->block.epoch=v->epoch;s->block.source_frame_start=v->frame;
        s->block.support_frame_start=v->frame-126u;
    }
    s->block.pcm16[s->count++]=v->pcm16;s->block.source_frame_end=v->frame;
    if(s->count==320) {s->count=0;++s->blocks;*block=&s->block;return 1;}
    return gap ? -3 : 0;
}
