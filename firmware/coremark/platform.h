#ifndef PLATFORM_H
#define PLATFORM_H
/* 平台契约：本工程目标 CPU 周期计数器为 63 MHz；UART 使用板级 921600 baud。
 * PLATFORM_HZ 用于把实际周期换算为秒，不改变 CPU 的运行频率。 */
#include <stdint.h>
#include <stddef.h>
#ifndef PLATFORM_HZ
#define PLATFORM_HZ 63000000u
#endif
#ifndef PLATFORM_LABEL
#define PLATFORM_LABEL "ARRAY128_CPU"
#endif
#ifndef PLATFORM_ISA
#define PLATFORM_ISA "RV32I_ZMMUL"
#endif
uint64_t platform_cycles(void);
void platform_mark(uint32_t marker);
void platform_exit(int status) __attribute__((noreturn));
int ee_printf(const char *fmt, ...);
#endif
