#ifndef MEETING_RUNTIME_H
#define MEETING_RUNTIME_H
#include "meeting_features.h"
#include "meeting_controller.h"
#include "../kws/kws_cooperative.h"
#include "meeting_voice_gate.h"

#define MEETING_RUNTIME_DEFAULT_PERIOD 19200u /* 400 ms at canonical 48 kHz */
typedef struct {
    meeting_features features;
    KwsCooperative model;
    meeting_controller controller;
    meeting_keyword_result pending;
    meeting_keyword_result decided; /* Last completed inference, never an in-flight window. */
    meeting_voice_gate voice;
    uint64_t last_block_start, last_started_window_end, last_now;
    uint32_t inference_period, started, completed, cancelled, discontinuities;
    uint32_t rejected_blocks, rejected_steps, model_errors;
    uint8_t initialized, have_block, have_started, have_now, inference_active;
} meeting_runtime;

/* Single owner and single frontend instance; no interrupts/heap/MMIO here.
 * cfg NULL selects controller defaults; period 0 selects 400 ms. This is a
 * functional cadence, not a proven real-time budget on the current CPU. */
int meeting_runtime_init(meeting_runtime *s, const meeting_config *cfg,
                         uint32_t epoch, uint32_t period);
/* Explicit discontinuity notification should be called immediately when the
 * acquisition layer detects loss, without waiting for the next full block.
 * Old epochs and backward same-epoch segment boundaries are rejected (-2).
 * out is required and carries any capture-reset RELEASE action. */
int meeting_runtime_reset_segment(meeting_runtime *s, uint32_t epoch,
                                  uint64_t first_valid_frame, meeting_output *out);
/* One already resampled block: 320 mono samples, 16 kHz. Return values match
 * meeting_features_feed (0/1/2), -1 invalid, -2 stale/backward. Every detected
 * forward gap/new epoch cancels in-flight inference and resets feature state.
 * out is required so a reset RELEASE cannot silently disappear. */
int meeting_runtime_feed_audio_block(meeting_runtime *s,
                                     const meeting_audio_block *block, meeting_output *out);
/* Direction is externally supplied. This library implements no DOA estimator. */
int meeting_runtime_feed_direction(meeting_runtime *s, uint32_t epoch,
                                   const meeting_direction *direction);
/* Performs at most ONE cooperative inference step, plus bounded begin-copy
 * or completion/control work. Pump audio between calls. Snapshot timestamps
 * refer to the source window, never the later completion time. */
meeting_output meeting_runtime_step(meeting_runtime *s, uint64_t now48k);
#endif
