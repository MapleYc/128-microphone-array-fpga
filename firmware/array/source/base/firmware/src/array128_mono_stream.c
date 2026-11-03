#include "../include/array128_mono_stream.h"

static uint32_t read_reg(unsigned offset)
{
    return *(volatile uint32_t *)(uintptr_t)(ARRAY128_MONO_BASE+offset);
}
static void write_reg(unsigned offset,uint32_t value)
{
    *(volatile uint32_t *)(uintptr_t)(ARRAY128_MONO_BASE+offset)=value;
}
int array128_mono_present(void)
{
    uint32_t id=read_reg(MONO_ID);
    return id==ARRAY128_MONO_ID_VALUE || id==ARRAY128_MONO_16K_ID_VALUE;
}
unsigned array128_mono_rate(void){return read_reg(MONO_RATE);}
int array128_mono_start(unsigned channel,int broadside,unsigned shift)
{
    if(channel>=128 || shift>8 || (broadside!=0 && broadside!=1)) return -1;
    write_reg(MONO_CONFIG,UINT32_C(1)|((uint32_t)broadside<<1)|
        ((uint32_t)channel<<8)|((uint32_t)shift<<16));
    return 0;
}
void array128_mono_stop(void)
{
    write_reg(MONO_CONFIG,read_reg(MONO_CONFIG)&~UINT32_C(1));
}
int array128_mono_read(array128_mono_sample *sample)
{
    if(!sample) return -1;
    if(!array128_mono_queued()) return 0;
    array128_mono_pop_queued(sample);
    return 1;
}
unsigned array128_mono_queued(void)
{
    return (read_reg(MONO_STATUS)&(MONO_RUNNING|MONO_PENDING))==MONO_RUNNING ? read_reg(MONO_COUNT):0;
}
void array128_mono_pop_queued(array128_mono_sample *sample)
{
    sample->pcm16=(int16_t)(uint16_t)read_reg(MONO_DATA);
    sample->frame=(uint64_t)read_reg(MONO_FRAME_LOW)|((uint64_t)read_reg(MONO_FRAME_HIGH)<<32);
    sample->epoch=read_reg(MONO_SAMPLE_EPOCH);
}
size_t array128_mono_read_block(array128_mono_sample *samples,size_t capacity)
{
    size_t count=0;
    if(!samples) return 0;
    while(count<capacity && array128_mono_read(samples+count)==1) ++count;
    return count;
}
void array128_mono_diagnose(array128_mono_diagnostic *d)
{
    if(!d) return;
    /* Counters are individual live reads, not an atomic all-field snapshot. */
    d->status=read_reg(MONO_STATUS);d->queued=read_reg(MONO_COUNT);
    d->epoch=read_reg(MONO_CURRENT_EPOCH);d->dropped=read_reg(MONO_DROPPED);
    d->saturated=read_reg(MONO_SATURATED);d->protocol_errors=read_reg(MONO_PROTOCOL_ERRORS);
    d->produced=read_reg(MONO_PRODUCED);
}
