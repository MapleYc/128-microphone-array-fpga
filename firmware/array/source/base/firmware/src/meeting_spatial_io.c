#include "../include/meeting_spatial_io.h"
static uint32_t rd(unsigned off){return *(volatile uint32_t *)(uintptr_t)(UINT32_C(0x20006000)+off);}
static void wr(unsigned off,uint32_t v){*(volatile uint32_t *)(uintptr_t)(UINT32_C(0x20006000)+off)=v;}
uint64_t meeting_source_now(void)
{
    uint32_t hi,lo,again;do{hi=rd(0x22c);lo=rd(0x228);again=rd(0x22c);}while(hi!=again);
    return ((uint64_t)hi<<32)|lo;
}
int meeting_doa_trigger(void){if(rd(0x208)&1u)return 0;wr(0x204,1);return 1;}
int meeting_doa_read(meeting_spatial_result *r)
{
    meeting_pair_result pairs[64];uint64_t frame;unsigned i;
    if(!r)return -1;
    if((rd(0x208)&7u)!=2u)return 0;
    frame=((uint64_t)rd(0x224)<<32)|rd(0x220);
    for(i=0;i<64;++i){wr(0x20c,i);pairs[i].lag=(int32_t)rd(0x210);
        pairs[i].correlation=rd(0x214);pairs[i].energy_a=rd(0x218);pairs[i].energy_b=rd(0x21c);}
    return meeting_spatial_fit(pairs,frame,r);
}
int meeting_beam_steer(int32_t az,int32_t el)
{
    uint16_t d[128];unsigned i;
    if(rd(0x300)&1u)return 0;
    if(meeting_spatial_delays(az,el,d))return -1;
    wr(0x304,0);for(i=0;i<128;++i)wr(0x308,d[i]);wr(0x30c,1);return 1;
}
int meeting_beam_read(array128_mono_sample *s)
{
    if(!s)return -1;
    if(!rd(0x400))return 0;
    meeting_beam_pop_queued(s);return 1;
}
void meeting_beam_pop_queued(array128_mono_sample *s)
{
    s->pcm16=(int16_t)(uint16_t)rd(0x410);
    s->frame=((uint64_t)rd(0x418)<<32)|rd(0x414);s->epoch=rd(0x41c);
}
uint32_t meeting_beam_drops(void){return rd(0x404);}
