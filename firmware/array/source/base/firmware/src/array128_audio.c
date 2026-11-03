#include "../include/array128_audio_regs.h"

static void audio_io_fence(void)
{
#if defined(__riscv)
    __asm__ volatile ("fence iorw, iorw" ::: "memory");
#else
    /* 主机语法检查用；不把主机版本当成真实 MMIO 执行程序。 */
    __asm__ volatile ("" ::: "memory");
#endif
}

uint32_t array128_audio_read(uint32_t offset)
{
    uint32_t value;
    audio_io_fence();
    value = *(volatile const uint32_t *)(uintptr_t)(ARRAY128_AUDIO_BASE + offset);
    audio_io_fence();
    return value;
}

void array128_audio_write(uint32_t offset, uint32_t value)
{
    audio_io_fence();
    *(volatile uint32_t *)(uintptr_t)(ARRAY128_AUDIO_BASE + offset) = value;
    audio_io_fence();
}

int array128_audio_present(void)
{
    return array128_audio_read(ARRAY128_AUDIO_ID) == ARRAY128_AUDIO_ID_VALUE;
}

int array128_audio_try_configure(uint32_t control)
{
    if ((control & ~ARRAY128_AUDIO_CONTROL_MASK) != 0)
        return -1;
    if ((array128_audio_read(ARRAY128_AUDIO_STATUS) & ARRAY128_AUDIO_CONFIG_PENDING) != 0)
        return 0;
    array128_audio_write(ARRAY128_AUDIO_CONTROL, control);
    return 1;
}

void array128_audio_enable_irqs(uint32_t mask)
{
    array128_audio_write(ARRAY128_AUDIO_IRQ_ENABLE, mask & UINT32_C(15));
}

void array128_audio_clear_events(uint32_t mask)
{
    array128_audio_write(ARRAY128_AUDIO_EVENTS, mask & UINT32_C(7));
}

int array128_audio_take_result(array128_audio_result *result)
{
    if (result == 0)
        return -1;
    if ((array128_audio_read(ARRAY128_AUDIO_STATUS) & ARRAY128_AUDIO_RESULT_PENDING) == 0)
        return 0;
    result->sequence = array128_audio_read(ARRAY128_AUDIO_RESULT_SEQ);
    result->azimuth_deg_x256 = (int32_t)array128_audio_read(ARRAY128_AUDIO_AZIMUTH);
    result->elevation_deg_x256 = (int32_t)array128_audio_read(ARRAY128_AUDIO_ELEVATION);
    result->confidence_q0_16 = array128_audio_read(ARRAY128_AUDIO_CONFIDENCE);
    result->frame = array128_audio_read(ARRAY128_AUDIO_RESULT_FRAME);
    array128_audio_write(ARRAY128_AUDIO_RESULT_ACK, UINT32_C(1));
    return 1;
}
