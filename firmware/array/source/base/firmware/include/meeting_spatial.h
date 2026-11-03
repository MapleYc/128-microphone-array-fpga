#ifndef MEETING_SPATIAL_H
#define MEETING_SPATIAL_H
#include <stdint.h>
#include "meeting_controller.h"
typedef struct {int32_t lag;uint32_t correlation,energy_a,energy_b;} meeting_pair_result;
typedef struct {
    meeting_direction direction;
    int32_t ux_q15,uy_q15;
    uint32_t accepted_pairs,residual_q10;
} meeting_spatial_result;
/* Single dominant far-field source, positive-Z hemisphere. Quality is
 * correlation coherence and consistency, not a probability. No reflection
 * immunity is claimed; reject an ill-conditioned/inconsistent estimate. */
int meeting_spatial_fit(const meeting_pair_result p[64],uint64_t frame,meeting_spatial_result *result);
int meeting_spatial_delays(int32_t azimuth_x256,int32_t elevation_x256,uint16_t delays_q8[128]);
#endif
