#include "../include/meeting_controller.h"

static meeting_output output(const meeting_controller *c, enum meeting_action a,
                             enum meeting_reason r, uint64_t frame)
{
    meeting_output o = {a, r, c->epoch, frame,
                        c->locked_azimuth_x256, c->locked_elevation_x256};
    return o;
}
static void increment(uint32_t *n) { if (*n != UINT32_MAX) ++*n; }
static int close_angle(int32_t a, int32_t b, int32_t tolerance)
{ int32_t delta = a - b; return delta >= -tolerance && delta <= tolerance; }

meeting_config meeting_default_config(void)
{
    /* Allow sparse DOA updates across the keyword's one-second audio window.
     * direction_for_window still excludes readings outside that exact window.
     * This relaxed matching policy still requires acoustic validation on board. */
    meeting_config cfg = {2400u, 24000u, 48000u, 48000u, 48000u, 48000u,
                          1440000u, 10 * 256, 24576u, 16384u, 1u, 1u, 1u, 27853u};
    return cfg;
}
int meeting_init(meeting_controller *c, const meeting_config *cfg, uint32_t epoch)
{
    if (!c || !cfg || !cfg->min_vote_spacing || cfg->max_vote_gap < cfg->min_vote_spacing ||
        !cfg->max_window || !cfg->max_result_age || !cfg->direction_max_age ||
        !cfg->lock_timeout || !cfg->votes_required || !cfg->yes_votes_required ||
        !cfg->directions_required || cfg->directions_required > 2u ||
        cfg->keyword_threshold_q15 > 32767u || cfg->direction_threshold_q15 > 32767u ||
        cfg->no_threshold_q15 < cfg->keyword_threshold_q15 || cfg->no_threshold_q15 > 32767u ||
        cfg->direction_tolerance_x256 <= 0 || cfg->direction_tolerance_x256 > 180 * 256)
        return -1;
    c->cfg = *cfg;
    c->actions = c->rejected_input = c->rejected_direction = 0;
    c->rejected_no_voice = c->rejected_no_score = 0;
    c->mode = MEETING_SEARCH;
    (void)meeting_reset_epoch(c, epoch);
    return 0;
}
meeting_output meeting_reset_epoch(meeting_controller *c, uint32_t epoch)
{
    return meeting_reset_segment(c, epoch, 0);
}
meeting_output meeting_reset_segment(meeting_controller *c, uint32_t epoch, uint64_t first_frame)
{
    enum meeting_action a = c->mode == MEETING_LOCKED ? MEETING_RELEASE : MEETING_NO_ACTION;
    c->epoch = epoch;
    c->history_count = c->history_next = 0;
    c->have_direction = c->have_window = c->votes = 0;
    c->last_direction = c->last_window = c->last_vote = c->cooldown_until = c->lock_frame = 0;
    c->first_valid_frame = first_frame;
    c->vote_keyword = MEETING_UNKNOWN;
    c->mode = MEETING_SEARCH;
    c->locked_azimuth_x256 = c->locked_elevation_x256 = 0;
    if (a != MEETING_NO_ACTION) increment(&c->actions);
    return output(c, a, a == MEETING_RELEASE ? MEETING_CAPTURE_RESET : MEETING_REASON_NONE, first_frame);
}
int meeting_feed_direction(meeting_controller *c, uint32_t epoch, const meeting_direction *d)
{
    if (!d || epoch != c->epoch || d->frame < c->first_valid_frame || d->quality_q15 > 32767u ||
        d->quality_q15 < c->cfg.direction_threshold_q15 ||
        d->azimuth_x256 < -80 * 256 || d->azimuth_x256 > 80 * 256 ||
        d->elevation_x256 < -40 * 256 || d->elevation_x256 > 40 * 256 ||
        (c->have_direction && d->frame <= c->last_direction)) {
        increment(&c->rejected_input); return 0;
    }
    c->history[c->history_next] = *d;
    c->history_next = (c->history_next + 1u) % MEETING_DIRECTION_HISTORY;
    if (c->history_count < MEETING_DIRECTION_HISTORY) ++c->history_count;
    c->last_direction = d->frame; c->have_direction = 1;
    return 1;
}
static int direction_for_window(const meeting_controller *c, const meeting_keyword_result *k,
                                meeting_direction *chosen)
{
    uint32_t n, found = 0;
    /* Choose the newest valid direction INSIDE the inference audio window.
     * Newer sensor readings arriving during inference must not move its target.
     * The fixed view is -80..80 degrees, so no circular-angle discontinuity.
     * The optional two-sample profile is retained only for regression/A-B use.
     */
    for (n = 0; n < c->history_count; ++n) {
        uint32_t i = (c->history_next + MEETING_DIRECTION_HISTORY - 1u - n) % MEETING_DIRECTION_HISTORY;
        const meeting_direction *d = &c->history[i];
        if (d->frame > k->window_end) continue;
        if (d->frame < k->window_start || k->window_end - d->frame > c->cfg.direction_max_age) break;
        if (!found) {
            *chosen = *d; found = 1;
            if (c->cfg.directions_required == 1u) return 1;
        }
        else {
            return close_angle(chosen->azimuth_x256, d->azimuth_x256, c->cfg.direction_tolerance_x256) &&
                   close_angle(chosen->elevation_x256, d->elevation_x256, c->cfg.direction_tolerance_x256);
        }
    }
    return 0;
}
meeting_output meeting_tick(meeting_controller *c, uint64_t now_frame)
{
    if (c->mode == MEETING_LOCKED && now_frame >= c->lock_frame &&
        now_frame - c->lock_frame >= c->cfg.lock_timeout) {
        c->mode = MEETING_SEARCH; c->votes = 0; increment(&c->actions);
        return output(c, MEETING_RELEASE, MEETING_TIMEOUT, now_frame);
    }
    return output(c, MEETING_NO_ACTION, MEETING_REASON_NONE, now_frame);
}
meeting_output meeting_feed_keyword(meeting_controller *c, const meeting_keyword_result *k, uint64_t now_frame)
{
    meeting_direction d;
    uint8_t required;
    meeting_output o = output(c, MEETING_NO_ACTION, MEETING_REASON_NONE, now_frame);
    if (!k || k->epoch != c->epoch || k->keyword > MEETING_NO || k->keyword < MEETING_SILENCE ||
        k->score_q15 > 32767u || k->voice_active > 1u || k->voice_ms > 1000u ||
        k->window_start < c->first_valid_frame || k->window_end < k->window_start ||
        k->window_end - k->window_start >= c->cfg.max_window || now_frame < k->window_end ||
        now_frame - k->window_end > c->cfg.max_result_age ||
        (c->have_window && k->window_end <= c->last_window)) {
        increment(&c->rejected_input); return o;
    }
    c->last_window = k->window_end; c->have_window = 1;
    /* Raw model output is kept for audit. Only control acceptance is gated.
     * A quiet NO must clear any previous vote, not become its confirmation. */
    if (k->keyword == MEETING_NO) {
        if (!k->voice_active || k->voice_ms < 80u) { increment(&c->rejected_no_voice); c->votes=0; return o; }
        if (k->score_q15 < c->cfg.no_threshold_q15) { increment(&c->rejected_no_score); c->votes=0; return o; }
    }
    if (k->score_q15 < c->cfg.keyword_threshold_q15 || k->keyword < MEETING_YES ||
        now_frame < c->cooldown_until) { c->votes = 0; return o; }
    required = k->keyword == MEETING_YES ? c->cfg.yes_votes_required : c->cfg.votes_required;
    if (!c->votes || c->vote_keyword != k->keyword || k->window_end - c->last_vote > c->cfg.max_vote_gap) {
        c->votes = 1; c->vote_keyword = k->keyword; c->last_vote = k->window_end;
    } else {
        if (k->window_end - c->last_vote < c->cfg.min_vote_spacing) return o;
        c->last_vote = k->window_end;
        if (c->votes < required) ++c->votes;
    }
    if (c->votes < required) return o;
    c->votes = 0;
    if (k->keyword == MEETING_YES && c->mode == MEETING_SEARCH) {
        if (!direction_for_window(c, k, &d)) { increment(&c->rejected_direction); return o; }
        c->mode = MEETING_LOCKED;
        c->locked_azimuth_x256 = d.azimuth_x256; c->locked_elevation_x256 = d.elevation_x256;
        c->lock_frame = now_frame; o.action = MEETING_LOCK;
    } else if (k->keyword == MEETING_NO && c->mode == MEETING_LOCKED) {
        c->mode = MEETING_SEARCH; o.action = MEETING_RELEASE;
    } else return o;
    c->cooldown_until = now_frame + c->cfg.cooldown;
    increment(&c->actions);
    return output(c, o.action, MEETING_COMMAND, k->window_end);
}
