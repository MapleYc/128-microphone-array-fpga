#ifndef MEETING_CONTROLLER_H
#define MEETING_CONTROLLER_H
#include <stdint.h>

/* All timestamps are canonical 48 kHz PCM frame indices, never CPU cycles.
 * No dynamic allocation, floating point, hardware MMIO or model dependency.
 * An epoch change/gap must call reset_segment before feeding new input.
 */
#define MEETING_DIRECTION_HISTORY 64u
enum meeting_keyword { MEETING_SILENCE, MEETING_UNKNOWN, MEETING_YES, MEETING_NO };
enum meeting_mode { MEETING_SEARCH, MEETING_LOCKED };
enum meeting_action { MEETING_NO_ACTION, MEETING_LOCK, MEETING_RELEASE };
enum meeting_reason { MEETING_REASON_NONE, MEETING_COMMAND, MEETING_CAPTURE_RESET, MEETING_TIMEOUT,
                      MEETING_MANUAL_RELEASE, MEETING_AI_RESET };
typedef struct {
    uint32_t min_vote_spacing, max_vote_gap, max_result_age, max_window;
    uint32_t cooldown, direction_max_age, lock_timeout;
    int32_t direction_tolerance_x256;
    uint16_t keyword_threshold_q15, direction_threshold_q15;
    uint8_t votes_required; /* NO/release confirmations; one voiced result by default. */
    uint8_t yes_votes_required; /* YES/lock confirmations; one by default. */
    uint8_t directions_required; /* One same-window direction by default; two for legacy comparison. */
    uint16_t no_threshold_q15; /* Separate, stricter release threshold. */
} meeting_config;
typedef struct {
    uint64_t frame;
    int32_t azimuth_x256, elevation_x256;
    uint16_t quality_q15; /* Algorithm quality score; not a calibrated probability. */
} meeting_direction;
typedef struct {
    uint32_t epoch;
    uint64_t window_start, window_end;
    enum meeting_keyword keyword;
    uint16_t score_q15;
    uint16_t voice_ms;
    uint8_t voice_active; /* Snapshot of acoustic activity in this exact window. */
} meeting_keyword_result;
typedef struct {
    enum meeting_action action;
    enum meeting_reason reason;
    uint32_t epoch;
    uint64_t source_frame;
    int32_t azimuth_x256, elevation_x256;
} meeting_output;
typedef struct {
    meeting_config cfg;
    meeting_direction history[MEETING_DIRECTION_HISTORY];
    uint32_t epoch, history_count, history_next;
    uint32_t actions, rejected_input, rejected_direction;
    uint32_t rejected_no_voice, rejected_no_score;
    uint64_t last_window, last_vote, cooldown_until, lock_frame;
    uint64_t last_direction;
    uint64_t first_valid_frame;
    int32_t locked_azimuth_x256, locked_elevation_x256;
    enum meeting_mode mode;
    enum meeting_keyword vote_keyword;
    uint8_t votes, have_window, have_direction;
} meeting_controller;

meeting_config meeting_default_config(void);
int meeting_init(meeting_controller *c, const meeting_config *cfg, uint32_t epoch);
meeting_output meeting_reset_epoch(meeting_controller *c, uint32_t epoch);
/* Use on gaps as well as epoch changes. Cancel any in-flight inference.
 * Old windows beginning before first_frame are rejected, even in same epoch. */
meeting_output meeting_reset_segment(meeting_controller *c, uint32_t epoch, uint64_t first_frame);
int meeting_feed_direction(meeting_controller *c, uint32_t epoch, const meeting_direction *d);
meeting_output meeting_feed_keyword(meeting_controller *c, const meeting_keyword_result *k, uint64_t now_frame);
meeting_output meeting_tick(meeting_controller *c, uint64_t now_frame);
#endif
