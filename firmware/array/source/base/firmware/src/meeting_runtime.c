#include "../include/meeting_runtime.h"

static void inc(uint32_t *value) { if (*value != UINT32_MAX) ++*value; }
static meeting_output none(const meeting_runtime *s, uint64_t frame)
{
    meeting_output o = {MEETING_NO_ACTION, MEETING_REASON_NONE,
        s ? s->controller.epoch : 0u, frame, 0, 0};
    return o;
}
static int stale_epoch(uint32_t current, uint32_t next)
{ return next != current && next - current >= UINT32_C(0x80000000); }
static void cancel_and_clear(meeting_runtime *s)
{
    if (s->inference_active) inc(&s->cancelled);
    s->inference_active = s->have_block = s->have_started = s->have_now = 0;
    s->last_block_start = s->last_started_window_end = s->last_now = 0;
    kws_cooperative_init(&s->model);
    meeting_features_reset(&s->features);
    meeting_voice_reset(&s->voice);
    s->pending=(meeting_keyword_result){0};s->decided=(meeting_keyword_result){0};
}
int meeting_runtime_init(meeting_runtime *s, const meeting_config *cfg,
                         uint32_t epoch, uint32_t period)
{
    meeting_config defaults;
    if (!s) return -1;
    s->initialized = 0;
    if (!cfg) { defaults = meeting_default_config(); cfg = &defaults; }
    if (meeting_init(&s->controller, cfg, epoch) != 0) return -1;
    s->inference_period = period ? period : MEETING_RUNTIME_DEFAULT_PERIOD;
    s->started = s->completed = s->cancelled = s->discontinuities = 0;
    s->rejected_blocks = s->rejected_steps = s->model_errors = 0;
    s->inference_active = 0;
    cancel_and_clear(s);
    s->initialized = 1;
    return 0;
}
int meeting_runtime_reset_segment(meeting_runtime *s, uint32_t epoch,
                                  uint64_t first, meeting_output *out)
{
    if (!s || !out || !s->initialized) return -1;
    *out = none(s, first);
    if (stale_epoch(s->controller.epoch, epoch) ||
        (epoch == s->controller.epoch &&
         (first < s->controller.first_valid_frame ||
          (s->have_block && first <= s->last_block_start - MEETING_AUDIO_GROUP_DELAY_FRAMES)))) {
        inc(&s->rejected_blocks); return -2;
    }
    cancel_and_clear(s);
    inc(&s->discontinuities);
    *out = meeting_reset_segment(&s->controller, epoch, first);
    return 0;
}
int meeting_runtime_feed_audio_block(meeting_runtime *s,
                                     const meeting_audio_block *b, meeting_output *out)
{
    int status;
    uint64_t first;
    if (!s || !out || !s->initialized) return -1;
    *out = none(s, b ? b->source_frame_end : 0);
    if (!b || b->source_frame_start < MEETING_AUDIO_GROUP_DELAY_FRAMES ||
        b->source_frame_end < b->source_frame_start ||
        b->source_frame_end - b->source_frame_start != 957u) {
        inc(&s->rejected_blocks); return -1;
    }
    first = b->source_frame_start - MEETING_AUDIO_GROUP_DELAY_FRAMES;
    if (stale_epoch(s->controller.epoch, b->epoch) ||
        (b->epoch == s->controller.epoch &&
         (first < s->controller.first_valid_frame ||
          (s->have_block && b->source_frame_start <= s->last_block_start)))) {
        inc(&s->rejected_blocks); return -2;
    }
    if (b->epoch != s->controller.epoch ||
        (s->have_block && b->source_frame_start - s->last_block_start != 960u)) {
        status = meeting_runtime_reset_segment(s, b->epoch, first, out);
        if (status != 0) return status;
    } else if (!s->have_block) {
        /* Establish the first valid acoustic sample even when starting at a
           nonzero hardware frame index. Explicit earlier resets remain valid. */
        *out = meeting_reset_segment(&s->controller, b->epoch, first);
    }
    status = meeting_features_feed(&s->features, b);
    if (status < 0) { inc(&s->rejected_blocks); return status; }
    meeting_voice_feed(&s->voice,b);
    s->last_block_start = b->source_frame_start;
    s->have_block = 1;
    return status;
}
int meeting_runtime_feed_direction(meeting_runtime *s, uint32_t epoch,
                                   const meeting_direction *direction)
{
    if (!s || !s->initialized) return 0;
    return meeting_feed_direction(&s->controller, epoch, direction);
}
meeting_output meeting_runtime_step(meeting_runtime *s, uint64_t now)
{
    meeting_output result;
    int status, best, i;
    if (!s || !s->initialized) return none(0, now);
    result = none(s, now);
    if (now < s->controller.first_valid_frame || (s->have_now && now < s->last_now)) {
        inc(&s->rejected_steps); return result;
    }
    s->last_now = now; s->have_now = 1;
    result = meeting_tick(&s->controller, now);
    if (result.action != MEETING_NO_ACTION) return result;
    if (!s->inference_active) {
        if (s->features.rows != MEETING_FEATURE_ROWS ||
            s->features.epoch != s->controller.epoch ||
            s->features.window_start < s->controller.first_valid_frame ||
            now < s->features.window_end ||
            (s->have_started &&
             (s->features.window_end <= s->last_started_window_end ||
              s->features.window_end - s->last_started_window_end < s->inference_period)))
            return result;
        if (!kws_cooperative_begin(&s->model, s->features.features)) {
            inc(&s->model_errors); return result;
        }
        s->pending.epoch = s->controller.epoch;
        s->pending.window_start = s->features.window_start;
        s->pending.window_end = s->features.window_end;
        s->pending.voice_ms=meeting_voice_window(&s->voice,s->pending.window_start,
            s->pending.window_end,&s->pending.voice_active);
        s->last_started_window_end = s->features.window_end;
        s->have_started = s->inference_active = 1;
        inc(&s->started);
    }
    status = kws_cooperative_step(&s->model);
    if (status < 0) {
        inc(&s->model_errors); s->inference_active = 0;
        kws_cooperative_init(&s->model);
        return result;
    }
    if (status == 0) return result;
    s->inference_active = 0; inc(&s->completed);
    best = 0;
    for (i = 1; i < KWS_CLASSES; ++i)
        if (s->model.output[i] > s->model.output[best]) best = i;
    s->pending.keyword = (enum meeting_keyword)best;
    s->pending.score_q15 = (uint16_t)(((int)s->model.output[best] + 128) * 128);
    s->decided=s->pending;
    return meeting_feed_keyword(&s->controller, &s->pending, now);
}
