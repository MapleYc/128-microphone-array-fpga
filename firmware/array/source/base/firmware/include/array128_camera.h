#ifndef ARRAY128_CAMERA_H
#define ARRAY128_CAMERA_H
#include <stdint.h>

#define ARRAY128_CAMERA_BASE UINT32_C(0x20007000)
#ifndef ARRAY128_CAMERA_CLOCK_HZ
#define ARRAY128_CAMERA_CLOCK_HZ UINT32_C(63000000)
#endif
enum {
    CAMERA_ID=0x00, CAMERA_COMMAND=0x04, CAMERA_STATUS=0x08,
    CAMERA_IRQ_ENABLE=0x0c, CAMERA_GPIO=0x10, CAMERA_INPUT=0x14,
    CAMERA_FRAME_ID=0x18, CAMERA_ERROR=0x1c, CAMERA_INDEX=0x20,
    CAMERA_DATA=0x24, CAMERA_INPUT_SIZE=0x28, CAMERA_OUTPUT_SIZE=0x2c,
    CAMERA_PIXEL_COUNT=0x30
};
enum { CAMERA_BUSY=1, CAMERA_FRAME_READY=2, CAMERA_FAILED=4 };
enum { CAMERA_OK=0, CAMERA_EIO=-1, CAMERA_ETIMEOUT=-2, CAMERA_EBUSY=-3, CAMERA_EFRAME=-4, CAMERA_EINVAL=-5 };

#ifdef ARRAY128_CAMERA_DIAGNOSTIC
/* Opt-in bringup instrumentation. The production driver keeps its 5 us delay. */
extern uint32_t array128_camera_diagnostic_delay_us;
extern uint32_t array128_camera_diagnostic_ignore_nack;
void array128_camera_trace(unsigned phase,uint16_t reg,int status,uint8_t value);
#endif

/* Single software owner for SCCB and snapshot/index registers. No ISR/main race. */
uint32_t array128_camera_read(uint32_t offset);
void array128_camera_write(uint32_t offset, uint32_t value);
int array128_camera_read_sensor(uint16_t reg, uint8_t *value);
int array128_camera_write_sensor(uint16_t reg, uint8_t value);
int array128_camera_initialize(void);
/* Last boot attempt; observed is meaningful for ID/readback stages 4/5/6/8.
 * Stages: MMIO=1, idle=2, reset=3, ID high/low=4/5, ID match=6,
 * table write=7, configuration readback=8, complete=9. */
typedef struct {
    uint32_t stage, reg, expected, observed, table_writes;
} camera_init_diagnostics;
const camera_init_diagnostics *array128_camera_init_diagnostics(void);
int array128_camera_trigger(void);
/* timeout_cycles uses the SoC monotonic cycle counter, and must be 1..0x7fffffff. */
int array128_camera_wait(uint32_t timeout_cycles);
/* Copy a coherent snapshot; capacity is uint16_t pixels, not bytes. */
int array128_camera_copy(uint16_t *destination, uint32_t capacity, uint32_t *frame_id);
#endif
