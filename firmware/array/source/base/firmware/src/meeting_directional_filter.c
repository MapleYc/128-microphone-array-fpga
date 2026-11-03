#include "../include/meeting_directional_filter.h"
/* Two-input generalized sidelobe canceller: align the reference microphone to
 * the selected beam, subtract to block the target, and adapt a bounded NLMS
 * interference estimate. PL delay-sum and KWS input remain unchanged.
 * This is directional cancellation, not independent-speaker neural separation. */
static int clamp(int v,int limit){return v>limit?limit:v< -limit?-limit:v;}
static uint32_t smooth(uint32_t old,uint32_t p)
{return p>=old?old+(p-old+15u)/16u:old-(old-p+15u)/16u;}
static void reset_beam(meeting_directional_filter *s)
{
    for(unsigned i=0;i<MEETING_DF_TAPS;++i){s->blocking[i]=s->desired[i]=0;s->weights_q12[i]=0;}
    s->tap_next=0;s->beam_count=0;s->primed=0;s->blocking_norm=64;s->beam_power=s->blocking_power=0;++s->resets;
    s->reference_power=0;s->covariance=0;s->target_protected=1;
}
void meeting_df_steering(meeting_directional_filter *s,unsigned delay_q8)
{if(s){s->reference_delay_q8=delay_q8;reset_beam(s);}}
void meeting_df_reference(meeting_directional_filter *s,int16_t input,uint64_t frame,uint32_t epoch,unsigned shift)
{
    if(!s)return;
    if(shift>8u){s->reference_count=0;return;}
    if(!s->reference_count || s->reference_epoch!=epoch || frame!=s->reference_frame+3u || s->shift!=shift){
        s->reference_count=0;s->reference_next=0;reset_beam(s);
    }
    s->reference_frame=frame;s->reference_epoch=epoch;s->shift=shift;
    if(input>=32760 || input<=-32760){++s->reference_clipped;s->reference_count=0;return;}
    /* Explicit power-of-two scaling preserves C's truncation toward zero and
     * avoids a software divide on this RV32 core for every reference sample. */
    unsigned bits=8u-shift;int value=input;
    s->reference[s->reference_next]=(int16_t)(value<0?-((-value)>>bits):(value>>bits));
    s->reference_next=(s->reference_next+1u)&(MEETING_DF_HISTORY-1u);
    if(s->reference_count<MEETING_DF_HISTORY)++s->reference_count;
}
int16_t meeting_df_sample(meeting_directional_filter *s,int16_t x,uint64_t frame,uint32_t epoch,unsigned locked,unsigned enabled)
{
    if(!s)return x;
    if(!s->beam_count || s->beam_epoch!=epoch || frame!=s->beam_frame+3u || s->locked!=locked || s->enabled!=enabled){
        reset_beam(s);s->beam_dc_q8=(int32_t)x*256;s->ref_dc_q8=(int32_t)x*256;
    }
    s->beam_frame=frame;s->beam_epoch=epoch;s->locked=locked;s->enabled=enabled;++s->processed;++s->beam_count;
    s->gain_mean_q15=32768;
    if(!enabled || !locked)return x;
    /* Both streams use source-frame units and identical FIR delay. PL labels
     * the beam frame after subtracting its 64-frame causal bias. Mono config
     * and steering epochs are independent counters: check each stream's own
     * continuity, never compare their numeric epoch values with each other. */
    int64_t distance=((int64_t)s->reference_frame-(int64_t)frame)*256+(int)s->reference_delay_q8-16384;
    if(distance<0 || distance>=(int64_t)(s->reference_count>1?s->reference_count-1:0)*768){
        ++s->unmatched;reset_beam(s);return x;
    }
    unsigned age=(unsigned)distance/768u,frac=(unsigned)distance%768u;
    unsigned ri=(s->reference_next-1u-age)&(MEETING_DF_HISTORY-1u);
    int ref=(s->reference[ri]*(int)(768u-frac)+s->reference[(ri-1u)&(MEETING_DF_HISTORY-1u)]*(int)frac)/768;
    s->ref_dc_q8+=(ref*256-s->ref_dc_q8)/128;
    s->beam_dc_q8+=((int)x*256-s->beam_dc_q8)/128;
    int r=ref-s->ref_dc_q8/256,b=x-s->beam_dc_q8/256;
    int z=clamp(r-b,8191);unsigned j=s->tap_next;
    int old=s->blocking[j];s->blocking_norm=s->blocking_norm-(uint32_t)(old*old)+(uint32_t)(z*z);
    s->blocking[j]=(int16_t)z;s->desired[j]=(int16_t)clamp(b,32767);
    /* Avoid the FIR and adaptation work on protected samples between energy
     * checks. History still advances, so entering cancellation stays causal. */
    if((s->beam_count&15u)!=0u && s->target_protected){
        s->tap_next=(j+1u)&(MEETING_DF_TAPS-1u);s->primed=s->beam_count>=128u;return x;
    }
    /* 0.25 ms lookahead within the canceller; raw stream is not delayed. */
    int desired=s->desired[(j+MEETING_DF_TAPS-MEETING_DF_LOOKAHEAD)&(MEETING_DF_TAPS-1u)];
    int32_t estimate=0;
    #pragma GCC unroll 8
    for(unsigned k=0;k<MEETING_DF_TAPS;++k){
        int v=s->blocking[(j-k)&(MEETING_DF_TAPS-1u)];
        estimate+=s->weights_q12[k]*v;
    }
    estimate/=4096;
    int error=desired-estimate;
    /* Freeze target-dominant adaptation. A new lock/epoch/gap clears the
     * previous speaker's coefficients. No host audio processing is involved. */
    if((s->beam_count&15u)==0u){
        int level=clamp(b,8191);
        int rl=clamp(r,8191);
        s->beam_power=smooth(s->beam_power,(uint32_t)(level*level));
        s->reference_power=smooth(s->reference_power,(uint32_t)(rl*rl));
        s->covariance+=(rl*level-s->covariance)/16;
        s->blocking_power=smooth(s->blocking_power,(uint32_t)(z*z));
        uint64_t cross=(int64_t)s->covariance*s->covariance;
        uint64_t product=(uint64_t)s->beam_power*s->reference_power;
        /* A useful noise reference must contain more spatially incoherent
         * power than the selected beam. Otherwise gain mismatch / a nearly
         * isolated target can leak speech into the blocking channel. */
        s->target_protected=!product || s->reference_power<s->beam_power+s->beam_power/4u || cross*32u>product*31u;
        int factor=0;
        if(!s->target_protected && s->beam_count>=64u && s->blocking_power>s->beam_power/16u && s->blocking_power>2u)
            factor=clamp(clamp(error,8191)*65536/(int32_t)s->blocking_norm,2048);
        for(unsigned k=0;k<MEETING_DF_TAPS;++k){
            int v=s->blocking[(j-k)&(MEETING_DF_TAPS-1u)];
            int delta=clamp(factor*v/64,128);int w=s->weights_q12[k];
            s->weights_q12[k]=clamp(w+delta-w/1024,16384);
        }
    }
    s->tap_next=(j+1u)&(MEETING_DF_TAPS-1u);
    s->primed=s->beam_count>=128u;
    if(!s->primed)return x;
    if(s->target_protected)return x;
    ++s->filtered;
    return (int16_t)clamp(error+s->beam_dc_q8/256,32767);
}
