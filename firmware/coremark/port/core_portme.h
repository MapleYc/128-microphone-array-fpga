#ifndef CORE_PORTME_H
#define CORE_PORTME_H
/* CoreMark 平台配置：关闭浮点/标准 I/O，使用本地 MMIO 计时、串口和静态内存。
 * BUILD_FLAGS 来自 build.py 自动记录的实际编译参数，不应手写为固定字符串。 */
#include <stddef.h>
#include <stdint.h>
#include "build_flags.h"
#define HAS_FLOAT 0
#define HAS_TIME_H 0
#define USE_CLOCK 0
#define HAS_STDIO 0
#define HAS_PRINTF 0
#define COMPILER_VERSION "xPack GCC " __VERSION__
#define COMPILER_FLAGS BUILD_FLAGS
#define MEM_LOCATION "IMEM code, DMEM static data/rodata/stack; memory:core 1:1"
typedef int16_t ee_s16;
typedef uint16_t ee_u16;
typedef int32_t ee_s32;
typedef uint32_t ee_u32;
typedef uint8_t ee_u8;
typedef double ee_f32;
typedef uintptr_t ee_ptr_int;
typedef size_t ee_size_t;
typedef uint64_t CORE_TICKS;
/* CoreMark 的 align_mem 约定按 4 字节对齐；寄存器和指针大小由 ILP32 保证。 */
#define align_mem(x) (void *)(4 + (((ee_ptr_int)(x) - 1) & ~(ee_ptr_int)3))
#define SEED_METHOD SEED_VOLATILE
#define MEM_METHOD MEM_STATIC
#define MULTITHREAD 1
#define MAIN_HAS_NOARGC 1
#define MAIN_HAS_NORETURN 0
extern ee_u32 default_num_contexts;
typedef struct CORE_PORTABLE_S { ee_u8 portable_id; } core_portable;
void portable_init(core_portable *p, int *argc, char *argv[]);
void portable_fini(core_portable *p);
int ee_printf(const char *fmt, ...);
#endif
