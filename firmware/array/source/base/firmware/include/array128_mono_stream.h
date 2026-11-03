#ifndef ARRAY128_MONO_STREAM_H
#define ARRAY128_MONO_STREAM_H
#include <stddef.h>
#include <stdint.h>

#define ARRAY128_MONO_BASE UINT32_C(0x20006100)
#define ARRAY128_MONO_ID_VALUE UINT32_C(0x4d128001)
#define ARRAY128_MONO_16K_ID_VALUE UINT32_C(0x4d128002)
#define ARRAY128_MONO_RATE_HZ UINT32_C(48000)
enum array128_mono_register {
    MONO_ID=0x00, MONO_CONFIG=0x04, MONO_ACTIVE_CONFIG=0x08, MONO_STATUS=0x0c,
    MONO_COUNT=0x10, MONO_DEPTH=0x14, MONO_RATE=0x18, MONO_DATA=0x1c,
    MONO_FRAME_LOW=0x20, MONO_FRAME_HIGH=0x24, MONO_SAMPLE_EPOCH=0x28,
    MONO_CURRENT_EPOCH=0x2c, MONO_DROPPED=0x30, MONO_SATURATED=0x34,
    MONO_PROTOCOL_ERRORS=0x38, MONO_PRODUCED=0x3c, MONO_IRQ_ENABLE=0x40
};
enum array128_mono_status {
    MONO_RUNNING=1, MONO_PENDING=2, MONO_OVERFLOW=4, MONO_PROTOCOL_FAULT=8
};
typedef struct {
    int16_t pcm16;
    uint32_t epoch;
    uint64_t frame;
} array128_mono_sample;
typedef struct {
    uint32_t status,queued,epoch,dropped,saturated,protocol_errors,produced;
} array128_mono_diagnostic;

/* One CPU consumer owns this FIFO and configuration, including IRQ contexts.
 * Frame is source canonical PCM frame; an epoch change or frame gap breaks a
 * continuous model window. Average mode is fixed broadside, not steering.
 * shift=8 converts Q1.23 to Q1.15; smaller shifts add gain and saturate.
 */
int array128_mono_present(void);
unsigned array128_mono_rate(void);
int array128_mono_start(unsigned channel,int broadside,unsigned shift);
void array128_mono_stop(void);
/* Nonblocking: 1 sample, 0 empty/not running/configuration pending, -1 bad arg. */
int array128_mono_read(array128_mono_sample *sample);
/* Snapshot a safe batch length, then pop no more than that many samples.
 * The caller must remain the sole consumer and must not reconfigure mid-batch. */
unsigned array128_mono_queued(void);
void array128_mono_pop_queued(array128_mono_sample *sample);
/* Returns number read, at most capacity. Inspect each sample's frame/epoch. */
size_t array128_mono_read_block(array128_mono_sample *samples,size_t capacity);
void array128_mono_diagnose(array128_mono_diagnostic *diagnostic);
#endif
