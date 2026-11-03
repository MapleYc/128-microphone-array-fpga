#include "../include/meeting_features.h"
#include "../kws/kws_frontend.h"
void meeting_features_reset(meeting_features *s)
{
    if(!s)return;
    s->epoch=0;s->rows=0;s->previous_block_start=0;
    s->window_start=0;s->window_end=0;s->have_block=0;
    kws_frontend_reset();
}
int meeting_features_feed(meeting_features *s,const meeting_audio_block *b)
{
    unsigned i;
    int8_t feature[40];
    if(!s || !b || b->source_frame_start<MEETING_AUDIO_GROUP_DELAY_FRAMES ||
       b->source_frame_end<b->source_frame_start ||
       b->source_frame_end-b->source_frame_start!=957u)return -1;
    if(s->have_block && (b->epoch!=s->epoch ||
       b->source_frame_start<s->previous_block_start ||
       b->source_frame_start-s->previous_block_start!=960u))meeting_features_reset(s);
    if(!s->have_block) {
        s->epoch=b->epoch;s->previous_block_start=b->source_frame_start;s->have_block=1;
        for(i=0;i<320u;++i)s->window[i]=b->pcm16[i];
        return 0;
    }
    for(i=0;i<160u;++i)s->window[320u+i]=b->pcm16[i];
    kws_frontend_frame(s->window,feature);
    if(s->rows==MEETING_FEATURE_ROWS) {
        for(i=0;i<1920u;++i)s->features[i]=s->features[40u+i];
    } else ++s->rows;
    for(i=0;i<40u;++i)s->features[(s->rows-1u)*40u+i]=feature[i];
    s->window_end=b->source_frame_start+159u*3u-MEETING_AUDIO_GROUP_DELAY_FRAMES;
    s->window_start=s->window_end-((s->rows-1u)*960u+479u*3u);
    for(i=0;i<320u;++i)s->window[i]=b->pcm16[i];
    s->previous_block_start=b->source_frame_start;
    return s->rows==MEETING_FEATURE_ROWS ? 2 : 1;
}
