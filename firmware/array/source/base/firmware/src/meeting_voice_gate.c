#include "../include/meeting_voice_gate.h"
void meeting_voice_reset(meeting_voice_gate *s)
{
    unsigned i;
    for(i=0;i<MEETING_VOICE_HISTORY;++i)s->active[i]=0;
    s->next=s->count=s->warmup=s->level_q8=0;
    s->noise_q8=256u;s->newest_start=0;
}
void meeting_voice_feed(meeting_voice_gate *s,const meeting_audio_block *b)
{
    int32_t sum=0,mean;uint32_t deviation=0,threshold,level;unsigned i,active;
    for(i=0;i<320u;++i)sum+=b->pcm16[i];
    mean=sum/320;
    for(i=0;i<320u;++i){int32_t d=(int32_t)b->pcm16[i]-mean;deviation+=(uint32_t)(d<0?-d:d);}
    /* mean absolute AC deviation in Q8: 256/320 = 4/5; no 64-bit division. */
    level=(deviation*4u)/5u;s->level_q8=level;
    if(!s->warmup)s->noise_q8=level>256u?level:256u;
    threshold=s->noise_q8*3u;
    if(threshold<MEETING_VOICE_FLOOR_Q8)threshold=MEETING_VOICE_FLOOR_Q8;
    active=s->warmup>=25u && level>=threshold;
    /* First 0.5 s learns the room; then fast downward/slow upward tracking.
     * Above-threshold sounds move the baseline very slowly, avoiding learning
     * a short word as background. Long new noise still needs field validation. */
    if(level<s->noise_q8)s->noise_q8-=(s->noise_q8-level+7u)/8u;
    else if(s->warmup<25u)s->noise_q8+=(level-s->noise_q8)/8u;
    else s->noise_q8+=(level-s->noise_q8)>>(active?10u:7u);
    if(s->noise_q8<256u)s->noise_q8=256u;
    if(s->warmup<25u)++s->warmup;
    s->active[s->next]=(uint8_t)active;s->next=(s->next+1u)%MEETING_VOICE_HISTORY;
    if(s->count<MEETING_VOICE_HISTORY)++s->count;
    s->newest_start=b->source_frame_start-MEETING_AUDIO_GROUP_DELAY_FRAMES;
}
uint16_t meeting_voice_window(const meeting_voice_gate *s,uint64_t start,uint64_t end,uint8_t *active)
{
    uint32_t total=0,run=0,longest=0,n;uint64_t lo,hi,begin,finish,span;
    *active=0;
    if(!s->count || end<start || end-start>=48000u || end>s->newest_start+957u ||
       s->newest_start<(uint64_t)(s->count-1u)*960u ||
       start<s->newest_start-(uint64_t)(s->count-1u)*960u)return 0;
    /* Walk latest to oldest, clipping both ends to the inference window. */
    for(n=0;n<s->count;++n){
        uint64_t offset=(uint64_t)n*960u;
        uint32_t i=(s->next+MEETING_VOICE_HISTORY-1u-n)%MEETING_VOICE_HISTORY;
        if(offset>s->newest_start)break;
        begin=s->newest_start-offset;finish=begin+957u;
        if(begin>end)continue;
        if(finish<start)break;
        lo=begin>start?begin:start;hi=finish<end?finish:end;
        span=hi-lo+3u;
        if(s->active[i]){total+=(uint32_t)span;run+=(uint32_t)span;if(run>longest)longest=run;}
        else run=0;
    }
    *active=(uint8_t)(total>=MEETING_VOICE_MIN_FRAMES && longest>=MEETING_VOICE_RUN_FRAMES);
    return (uint16_t)(total/48u);
}
