/* 原版 EEMBC CoreMark 的平台移植层：算法文件不修改，只提供 seed、计时和收尾接口。 */
#include "coremark.h"
#include "platform.h"

/* 官方 validation/performance 模式使用不同 seed，必须与选定镜像及 CRC 期望匹配。 */
#if VALIDATION_RUN
volatile ee_s32 seed1_volatile = 0x3415;
volatile ee_s32 seed2_volatile = 0x3415;
#else
volatile ee_s32 seed1_volatile = 0;
volatile ee_s32 seed2_volatile = 0;
#endif
volatile ee_s32 seed3_volatile = 0x66;
/* seed4 是迭代次数，不是固定 CPU 周期数；实际周期由 platform_cycles 测量。 */
volatile ee_s32 seed4_volatile = ITERATIONS;
volatile ee_s32 seed5_volatile = 0;
ee_u32 default_num_contexts = 1;
static CORE_TICKS started, stopped;

void start_time(void)
{
    /* 软件读计时器，并写边界标记供 testbench 交叉核对；标记不复位计时器。 */
    started = platform_cycles();
    platform_mark(1);
}

void stop_time(void)
{
    platform_mark(2);
    stopped = platform_cycles();
}

CORE_TICKS get_time(void) { return stopped - started; }
/* CoreMark 的秒数接口只保留整数秒；精确周期间隔另由 SIM_CYCLES_HEX 打印。 */
secs_ret time_in_secs(CORE_TICKS ticks) { return (secs_ret)(ticks / PLATFORM_HZ); }

void portable_init(core_portable *p, int *argc, char *argv[])
{
    /* 启动时核对 ILP32 数据类型宽度，避免交叉编译 ABI 不匹配。 */
    (void)argc; (void)argv;
    _Static_assert(sizeof(ee_ptr_int) == sizeof(void *), "pointer width");
    _Static_assert(sizeof(ee_u32) == 4 && sizeof(ee_s32) == 4, "32-bit types");
    _Static_assert(sizeof(ee_u16) == 2 && sizeof(ee_s16) == 2, "16-bit types");
    _Static_assert(sizeof(ee_u8) == 1, "8-bit type");
    p->portable_id = 1;
    ee_printf(PLATFORM_LABEL " CoreMark " PLATFORM_ISA "/ILP32; timer_hz=%u; iterations=%u\n",
              PLATFORM_HZ, ITERATIONS);
}

void portable_fini(core_portable *p)
{
    /* 官方 core_main 的 CRC 与 >=10 秒检查不变；这里额外校验结果并输出诊断标记。
       短程运行即使 CRC 正确，也只表示功能通过，不能当作正式 CoreMark 分数。 */
    core_results *r = (core_results *)((unsigned char *)p - offsetof(core_results, port));
#if VALIDATION_RUN
    const ee_u16 list = 0xe3c1, matrix = 0x0747, state = 0x8d84;
    const ee_s16 seed = 0x3415;
#else
    const ee_u16 list = 0xe714, matrix = 0x1fd7, state = 0x8e3a;
    const ee_s16 seed = 0;
#endif
    int ok = r->crclist == list && r->crcmatrix == matrix && r->crcstate == state
        && r->seed1 == seed && r->seed2 == seed && r->seed3 == 0x66
        && r->size == 666 && r->iterations == ITERATIONS && ITERATIONS > 0
        && r->execs == ALL_ALGORITHMS_MASK && r->err == 0 && check_data_types() == 0;
    CORE_TICKS elapsed = get_time();
    ee_printf("SIM_CYCLES_HEX=%08x%08x TIMER_HZ=%u\n", (ee_u32)(elapsed >> 32), (ee_u32)elapsed, PLATFORM_HZ);
    if (time_in_secs(elapsed) < 10)
        ee_printf("SIM_SHORT_RUN: functional CRC validation only; cycle-derived estimate, not a reportable CoreMark score.\n");
    ee_printf(ok ? "SIM_CRC_PASS\n" : "SIM_CRC_FAIL\n");
    p->portable_id = 0;
    platform_exit(ok ? 0 : 1);
}
