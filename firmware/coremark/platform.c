/* 裸机平台层：以 MMIO 访问周期计数器、计时标记、UART 和退出寄存器。
 * 地址必须与 rtl/soc/rv32_coremark_soc.v 保持一致。 */
#include "platform.h"
#include <stdarg.h>

uint64_t platform_cycles(void)
{
    /* 32 位 CPU 读取 64 位自由运行计数器：高/低/高重读，避免低位翻转时拼出错误值。 */
    volatile uint32_t *const timer = (volatile uint32_t *)0x20002000u;
    uint32_t hi, lo, again;
    do {
        hi = timer[1];
        lo = timer[0];
        again = timer[1];
    } while (hi != again);
    return ((uint64_t)hi << 32) | lo;
}

void platform_mark(uint32_t marker)
{
    /* 标记计时区间边界，供 testbench 独立观察；不会清零硬件计数器。 */
    *(volatile uint32_t *)0x20002008u = marker;
}

void platform_exit(int status)
{
    /* 退出码写给 testbench/板级调试逻辑；裸机没有操作系统可返回。 */
    *(volatile uint32_t *)0x2000f000u = (uint32_t)status;
    for (;;) __asm__ volatile ("j .");
}

static void putch(char c)
{
    /* UART MMIO 写入单字节；串口发送耗时不属于 CoreMark 被标记的工作负载。 */
    *(volatile unsigned char *)0x20000000u = (unsigned char)c;
}

static int print_unsigned(uint32_t value, unsigned base, unsigned width, char pad)
{
    /* 无 libc 的最小整数格式化器，先倒序取位，再按原顺序输出。 */
    char digits[32];
    unsigned used = 0;
    int count = 0;
    do {
        unsigned digit = value % base;
        digits[used++] = (char)(digit < 10 ? '0' + digit : 'a' + digit - 10);
        value /= base;
    } while (value);
    while (width > used) { putch(pad); --width; ++count; }
    while (used) { putch(digits[--used]); ++count; }
    return count;
}

/* 只实现原版 CoreMark 所需的整数/字符串格式，不引入完整 libc。 */
int ee_printf(const char *fmt, ...)
{
    va_list args;
    int count = 0;
    va_start(args, fmt);
    while (*fmt) {
        if (*fmt != '%') { putch(*fmt++); ++count; continue; }
        ++fmt;
        char pad = ' ';
        unsigned width = 0, is_long = 0;
        if (*fmt == '0') { pad = '0'; ++fmt; }
        while (*fmt >= '0' && *fmt <= '9') width = width * 10 + (unsigned)(*fmt++ - '0');
        if (*fmt == 'l') { is_long = 1; ++fmt; }
        char spec = *fmt++;
        if (spec == 's') {
            const char *s = va_arg(args, const char *);
            while (*s) { putch(*s++); ++count; }
        } else if (spec == 'c') {
            putch((char)va_arg(args, int)); ++count;
        } else if (spec == '%') {
            putch('%'); ++count;
        } else if (spec == 'd' || spec == 'i') {
            int32_t value = is_long ? (int32_t)va_arg(args, long) : va_arg(args, int);
            uint32_t magnitude = (uint32_t)value;
            if (value < 0) { putch('-'); ++count; magnitude = 0u - magnitude; if (width) --width; }
            count += print_unsigned(magnitude, 10, width, pad);
        } else if (spec == 'u' || spec == 'x' || spec == 'p') {
            uint32_t value;
            if (spec == 'p') value = (uint32_t)(uintptr_t)va_arg(args, void *);
            else value = is_long ? (uint32_t)va_arg(args, unsigned long) : va_arg(args, unsigned int);
            count += print_unsigned(value, spec == 'u' ? 10 : 16, width, pad);
        } else {
            putch('?'); ++count;
        }
    }
    va_end(args);
    return count;
}

void *memcpy(void *dest, const void *src, size_t n)
{
    /* 原版算法和编译器可能调用这两个内存例程；按字节实现避免额外库依赖。 */
    unsigned char *d = dest;
    const unsigned char *s = src;
    while (n--) *d++ = *s++;
    return dest;
}

void *memset(void *dest, int value, size_t n)
{
    unsigned char *d = dest;
    while (n--) *d++ = (unsigned char)value;
    return dest;
}
