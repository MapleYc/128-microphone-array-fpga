#include "../include/array128_camera.h"
#include "../include/ov5640_720p_table.h"

static camera_init_diagnostics init_diagnostics;
const camera_init_diagnostics *array128_camera_init_diagnostics(void)
{
    return &init_diagnostics;
}
static void init_stage(uint32_t stage,uint32_t reg,uint32_t expected,uint32_t observed)
{
    init_diagnostics.stage=stage;init_diagnostics.reg=reg;
    init_diagnostics.expected=expected;init_diagnostics.observed=observed;
}

#ifdef ARRAY128_CAMERA_DIAGNOSTIC
#define SCCB_DELAY_US array128_camera_diagnostic_delay_us
#define CAMERA_TRACE(p,r,s,v) array128_camera_trace(p,r,s,v)
#else
#define SCCB_DELAY_US 5u
#define CAMERA_TRACE(p,r,s,v) ((void)0)
#endif

/* Test hooks allow the same driver to run against a host SCCB/MMIO model. */
#ifdef ARRAY128_CAMERA_TEST_IO
extern uint32_t camera_test_read(uint32_t address);
extern void camera_test_write(uint32_t address, uint32_t value);
#define IO_READ(a) camera_test_read(a)
#define IO_WRITE(a,v) camera_test_write(a,v)
#define IO_FENCE() ((void)0)
#else
#define IO_READ(a) (*(volatile uint32_t *)(uintptr_t)(a))
#define IO_WRITE(a,v) (*(volatile uint32_t *)(uintptr_t)(a)=(v))
#define IO_FENCE() __asm__ volatile("fence iorw,iorw" ::: "memory")
#endif

uint32_t array128_camera_read(uint32_t offset)
{
    uint32_t result;
    IO_FENCE(); result=IO_READ(ARRAY128_CAMERA_BASE+offset); IO_FENCE();
    return result;
}
void array128_camera_write(uint32_t offset, uint32_t value)
{
    IO_FENCE(); IO_WRITE(ARRAY128_CAMERA_BASE+offset,value); IO_FENCE();
}
static uint32_t cycles(void) { return IO_READ(UINT32_C(0x20002000)); }
static void delay_us(uint32_t us)
{
    const uint32_t start=cycles();
    const uint32_t ticks=us*(ARRAY128_CAMERA_CLOCK_HZ/UINT32_C(1000000));
    while ((uint32_t)(cycles()-start)<ticks) { }
}
static void lines(int scl_release, int sda_release)
{
    const uint32_t gpio=array128_camera_read(CAMERA_GPIO);
    array128_camera_write(CAMERA_GPIO,(gpio & ~UINT32_C(3)) | (scl_release ? 1u : 0u) | (sda_release ? 2u : 0u));
}
static int scl_high(void)
{
    const uint32_t start=cycles();
    while (!(array128_camera_read(CAMERA_INPUT)&1u))
        if ((uint32_t)(cycles()-start)>=ARRAY128_CAMERA_CLOCK_HZ/500u) return CAMERA_ETIMEOUT;
    return CAMERA_OK;
}
static int sccb_start(void)
{
    lines(1,1);
    if (scl_high()!=0) { CAMERA_TRACE(7,0,CAMERA_ETIMEOUT,0); return CAMERA_ETIMEOUT; }
    delay_us(SCCB_DELAY_US);
    if (!(array128_camera_read(CAMERA_INPUT)&2u)) { CAMERA_TRACE(8,0,CAMERA_EIO,0); return CAMERA_EIO; }
    lines(1,0); delay_us(SCCB_DELAY_US); lines(0,0);
    return CAMERA_OK;
}
static int sccb_stop(void)
{
    int status;
    lines(0,0); delay_us(SCCB_DELAY_US); lines(1,0);
    status=scl_high(); delay_us(SCCB_DELAY_US); lines(1,1); delay_us(SCCB_DELAY_US);
    return status;
}
static int write_bit(unsigned bit)
{
    lines(0,bit!=0); delay_us(SCCB_DELAY_US); lines(1,bit!=0);
    if (scl_high()!=0) return CAMERA_ETIMEOUT;
    delay_us(SCCB_DELAY_US); lines(0,bit!=0);
    return CAMERA_OK;
}
static int read_bit(unsigned *bit)
{
    lines(0,1); delay_us(SCCB_DELAY_US); lines(1,1);
    if (scl_high()!=0) return CAMERA_ETIMEOUT;
    delay_us(SCCB_DELAY_US); *bit=(array128_camera_read(CAMERA_INPUT)>>1)&1u;
    lines(0,1);
    return CAMERA_OK;
}
static int write_byte(uint8_t value)
{
    unsigned bit;
    for (int i=7;i>=0;--i)
        if (write_bit(((unsigned)value>>(unsigned)i)&1u)!=0) { CAMERA_TRACE(7,0,CAMERA_ETIMEOUT,value); return CAMERA_ETIMEOUT; }
    if (read_bit(&bit)!=0) { CAMERA_TRACE(7,0,CAMERA_ETIMEOUT,value); return CAMERA_ETIMEOUT; }
    if (bit) CAMERA_TRACE(6,0,CAMERA_EIO,value);
#ifdef ARRAY128_CAMERA_DIAGNOSTIC
    /* Retain strict mode for the controlled diagnostic A/B comparison. */
    return bit && !array128_camera_diagnostic_ignore_nack ? CAMERA_EIO : CAMERA_OK;
#else
    /* SCCB v2.1 3.2.2/3.2.3 defines a master-issued ninth bit as Don't-Care,
     * not I2C ACK. Real-board strict/Don't-Care/strict comparison reproduced
     * rejection at 0x4740 despite successful register programming and images.
     * Presence and configuration are checked by ID and register readback. */
    return CAMERA_OK;
#endif
}
static int read_byte(uint8_t *value)
{
    unsigned bit, data=0;
    for (unsigned i=0;i<8;++i) {
        if (read_bit(&bit)!=0) return CAMERA_ETIMEOUT;
        data=(data<<1)|bit;
    }
    if (write_bit(1)!=0) return CAMERA_ETIMEOUT; /* Last byte: master NACK. */
    *value=(uint8_t)data;
    return CAMERA_OK;
}
int array128_camera_write_sensor(uint16_t reg, uint8_t value)
{
    int status=sccb_start();
    if (status==0) status=write_byte(0x78);
    if (status==0) status=write_byte((uint8_t)(reg>>8));
    if (status==0) status=write_byte((uint8_t)reg);
    if (status==0) status=write_byte(value);
    const int stop_status=sccb_stop();
    return status ? status : stop_status;
}
int array128_camera_read_sensor(uint16_t reg, uint8_t *value)
{
    if (!value) return CAMERA_EINVAL;
    int status=sccb_start();
    if (status==0) status=write_byte(0x78);
    if (status==0) status=write_byte((uint8_t)(reg>>8));
    if (status==0) status=write_byte((uint8_t)reg);
    int stop_status=sccb_stop();
    if (status==0) status=stop_status;
    if (status!=0) return status;
    status=sccb_start();
    if (status==0) status=write_byte(0x79);
    if (status==0) status=read_byte(value);
    stop_status=sccb_stop();
    return status ? status : stop_status;
}
int array128_camera_initialize(void)
{
    uint8_t high=0,low=0;
    init_diagnostics.table_writes=0;
    uint32_t value=array128_camera_read(CAMERA_ID);
    init_stage(1,0,UINT32_C(0xc5640001),value);
    if (value!=UINT32_C(0xc5640001)) { CAMERA_TRACE(1,0,CAMERA_EIO,0); return CAMERA_EIO; }
    value=array128_camera_read(CAMERA_STATUS);
    init_stage(2,0,0,value);
    if (value&CAMERA_BUSY) { CAMERA_TRACE(1,0,CAMERA_EBUSY,0); return CAMERA_EBUSY; }
    CAMERA_TRACE(1,0,CAMERA_OK,0);
    init_stage(3,0,0,0);
    array128_camera_write(CAMERA_COMMAND,6); /* Release old frame and clear errors. */
    array128_camera_write(CAMERA_GPIO,0x0b); delay_us(5000);
    array128_camera_write(CAMERA_GPIO,0x03); delay_us(2000);
    array128_camera_write(CAMERA_GPIO,0x07); delay_us(25000);
    init_stage(4,0x300a,0x56,0);
    int status=array128_camera_read_sensor(0x300a,&high);
    init_diagnostics.observed=high;
    CAMERA_TRACE(2,0x300a,status,high);
    if (status==0) {
        init_stage(5,0x300b,0x40,0);
        status=array128_camera_read_sensor(0x300b,&low);init_diagnostics.observed=low;
        CAMERA_TRACE(3,0x300b,status,low);
    }
    if (status!=0) return status;
    init_stage(6,0x300a,0x5640,((uint32_t)high<<8)|low);
    if (high!=0x56 || low!=0x40) { CAMERA_TRACE(4,0x300b,CAMERA_EIO,low); return CAMERA_EIO; }
    for (unsigned i=0;i<OV5640_720P_TABLE_COUNT;++i) {
        const ov5640_reg_entry *entry=&ov5640_720p_table[i];
        init_stage(7,entry->reg,entry->value,0);
        status=array128_camera_write_sensor(entry->reg,entry->value);
        CAMERA_TRACE(5,entry->reg,status,entry->value);
        if (status!=0) return status;
        ++init_diagnostics.table_writes;
        if (entry->delay_ms) delay_us((uint32_t)entry->delay_ms*1000u);
    }
#ifndef ARRAY128_CAMERA_DIAGNOSTIC
    /* A completed SCCB write alone cannot prove that a device accepted it.
     * Verify stable DVP mode/geometry registers before enabling snapshots.
     * This runs only at boot, before audio acquisition is enabled. */
    static const struct { uint16_t reg; uint8_t value; } required[] = {
        {0x3008,0x02},{0x3017,0xff},{0x3018,0xff},
        {0x3808,0x05},{0x3809,0x00},{0x380a,0x02},{0x380b,0xd0},
        {0x4740,0x20},{0x4300,0x61},{0x501f,0x01}
    };
    delay_us(10000);
    for (unsigned i=0;i<sizeof(required)/sizeof(required[0]);++i) {
        uint8_t value=0;
        init_stage(8,required[i].reg,required[i].value,0);
        status=array128_camera_read_sensor(required[i].reg,&value);
        init_diagnostics.observed=value;
        if (status!=0) return status;
        if (value!=required[i].value) return CAMERA_EIO;
    }
#endif
    init_stage(9,0,0,0);
    CAMERA_TRACE(9,0,CAMERA_OK,0);
    return CAMERA_OK;
}
int array128_camera_trigger(void)
{
    if (array128_camera_read(CAMERA_STATUS)&CAMERA_BUSY) return CAMERA_EBUSY;
    array128_camera_write(CAMERA_COMMAND,5); /* Request plus clear stale errors. */
    return CAMERA_OK;
}
int array128_camera_wait(uint32_t timeout_cycles)
{
    if (!timeout_cycles || timeout_cycles>UINT32_C(0x7fffffff)) return CAMERA_EINVAL;
    const uint32_t start=cycles();
    for (;;) {
        const uint32_t status=array128_camera_read(CAMERA_STATUS);
        if (status&CAMERA_BUSY) {
            if ((uint32_t)(cycles()-start)>=timeout_cycles) return CAMERA_ETIMEOUT;
        } else {
            if (status&CAMERA_FAILED) return CAMERA_EFRAME;
            return status&CAMERA_FRAME_READY ? CAMERA_OK : CAMERA_EFRAME;
        }
    }
}
int array128_camera_copy(uint16_t *destination, uint32_t capacity, uint32_t *frame_id)
{
    const uint32_t status=array128_camera_read(CAMERA_STATUS);
    if (status&CAMERA_BUSY) return CAMERA_EBUSY;
    if (!(status&CAMERA_FRAME_READY)) return CAMERA_EFRAME;
    const uint32_t count=array128_camera_read(CAMERA_PIXEL_COUNT);
    if (!destination || capacity<count) return CAMERA_EINVAL;
    array128_camera_write(CAMERA_INDEX,0);
    for (uint32_t i=0;i<count;++i) destination[i]=(uint16_t)array128_camera_read(CAMERA_DATA);
    if (frame_id) *frame_id=array128_camera_read(CAMERA_FRAME_ID);
    return CAMERA_OK;
}
