#ifndef ARRAY128_AUDIO_REGS_H
#define ARRAY128_AUDIO_REGS_H

#include <stdint.h>

/* 该地址是 CPU 本地移植草案，不是已验收的板级外设地址。 */
#ifndef ARRAY128_AUDIO_BASE
#define ARRAY128_AUDIO_BASE UINT32_C(0x20006000)
#endif
#define ARRAY128_AUDIO_ID          UINT32_C(0x00)
#define ARRAY128_AUDIO_CONTROL     UINT32_C(0x04)
#define ARRAY128_AUDIO_STATUS      UINT32_C(0x08)
#define ARRAY128_AUDIO_IRQ_ENABLE  UINT32_C(0x0c)
#define ARRAY128_AUDIO_EVENTS      UINT32_C(0x10)
#define ARRAY128_AUDIO_SAMPLES     UINT32_C(0x14)
#define ARRAY128_AUDIO_FRAMES      UINT32_C(0x18)
#define ARRAY128_AUDIO_RESULT_SEQ  UINT32_C(0x20)
#define ARRAY128_AUDIO_AZIMUTH     UINT32_C(0x24)
#define ARRAY128_AUDIO_ELEVATION   UINT32_C(0x28)
#define ARRAY128_AUDIO_CONFIDENCE  UINT32_C(0x2c)
#define ARRAY128_AUDIO_RESULT_FRAME UINT32_C(0x30)
#define ARRAY128_AUDIO_RESULT_ACK  UINT32_C(0x34)
#define ARRAY128_AUDIO_ID_VALUE    UINT32_C(0xa1280001)

#define ARRAY128_AUDIO_RUN         UINT32_C(1)
#define ARRAY128_AUDIO_LOCALIZE    UINT32_C(2)
#define ARRAY128_AUDIO_DENOISE     UINT32_C(4)
#define ARRAY128_AUDIO_CONTROL_MASK UINT32_C(0x0000ff07)
#define ARRAY128_AUDIO_RESULT_PENDING UINT32_C(1)
#define ARRAY128_AUDIO_CONFIG_PENDING UINT32_C(2)
#define ARRAY128_AUDIO_EVENT_CAPTURE_OVERFLOW UINT32_C(1)
/* 协议正常的生产者可持有 valid 等待；此位只报告背压，不宣称数据已丢失。 */
#define ARRAY128_AUDIO_EVENT_RESULT_BLOCKED UINT32_C(2)
#define ARRAY128_AUDIO_EVENT_CONFIG_APPLIED UINT32_C(4)
#define ARRAY128_AUDIO_IRQ_RESULT UINT32_C(1)
#define ARRAY128_AUDIO_IRQ_CAPTURE_OVERFLOW UINT32_C(2)
#define ARRAY128_AUDIO_IRQ_RESULT_BLOCKED UINT32_C(4)
#define ARRAY128_AUDIO_IRQ_CONFIG_APPLIED UINT32_C(8)

typedef struct
{
    uint32_t sequence;
    /* 有符号角度乘 256，32 位存储；覆盖 +/-180 度，不使用窄 16 位 Q8.8。 */
    int32_t azimuth_deg_x256;
    int32_t elevation_deg_x256;
    uint32_t confidence_q0_16;
    uint32_t frame;
} array128_audio_result;

uint32_t array128_audio_read(uint32_t offset);
void array128_audio_write(uint32_t offset, uint32_t value);
int array128_audio_present(void);
int array128_audio_try_configure(uint32_t control);
void array128_audio_enable_irqs(uint32_t mask);
void array128_audio_clear_events(uint32_t mask);
/* 单消费者；成功后 ACK 释放整组邮箱。不可由中断和主循环同时调用。 */
int array128_audio_take_result(array128_audio_result *result);

#endif
