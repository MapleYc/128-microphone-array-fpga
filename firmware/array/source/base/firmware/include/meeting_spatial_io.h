#ifndef MEETING_SPATIAL_IO_H
#define MEETING_SPATIAL_IO_H
#include "meeting_spatial.h"
#include "array128_mono_stream.h"
uint64_t meeting_source_now(void);
int meeting_doa_trigger(void);
int meeting_doa_read(meeting_spatial_result *r);
int meeting_beam_steer(int32_t azimuth_x256,int32_t elevation_x256);
int meeting_beam_read(array128_mono_sample *sample);
/* Caller has reserved a sample by reading the FIFO count; no other consumer. */
void meeting_beam_pop_queued(array128_mono_sample *sample);
uint32_t meeting_beam_drops(void);
#endif
