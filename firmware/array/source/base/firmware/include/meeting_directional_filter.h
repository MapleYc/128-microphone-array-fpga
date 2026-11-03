#ifndef MEETING_DIRECTIONAL_FILTER_H
#define MEETING_DIRECTIONAL_FILTER_H
#include <stdint.h>
#define MEETING_DF_TAPS 8u
#define MEETING_DF_LOOKAHEAD 4u
#define MEETING_DF_HISTORY 128u
typedef struct {
    int16_t reference[MEETING_DF_HISTORY],blocking[MEETING_DF_TAPS],desired[MEETING_DF_TAPS];
    int32_t weights_q12[MEETING_DF_TAPS],ref_dc_q8,beam_dc_q8;
    uint64_t reference_frame,beam_frame;
    uint32_t reference_epoch,beam_epoch,reference_count,beam_count;
    unsigned reference_next,tap_next,enabled,locked,shift,primed,reference_delay_q8;
    uint32_t processed,filtered,unmatched,reference_clipped,resets,gain_mean_q15;
    uint32_t blocking_norm,beam_power,blocking_power,reference_power;
    int32_t covariance;unsigned target_protected;
} meeting_directional_filter;
void meeting_df_reference(meeting_directional_filter *,int16_t,uint64_t,uint32_t,unsigned);
int16_t meeting_df_sample(meeting_directional_filter *,int16_t,uint64_t,uint32_t,unsigned,unsigned);
void meeting_df_steering(meeting_directional_filter *,unsigned);
#endif
