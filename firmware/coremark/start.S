# 裸机入口：设置 gp/sp，清零 .bss，然后调用 main。
# 段地址和符号来自 link.ld；这里不实现异常处理或操作系统启动流程。
.section .text.start,"ax",@progbits
.globl _start
.type _start,@function
_start:
    # gp 初始化禁止链接松弛，以免链接器把绝对地址建立过程错误改写为 gp 相对形式。
    .option push
    .option norelax
    la gp, __global_pointer$
    .option pop
    la sp, __stack_top
    la t0, __bss_start
    la t1, __bss_end
    # .bss 在 ELF 中是 NOLOAD：启动阶段必须手动清零，且链接脚本保证 4 字节对齐。
1:
    bgeu t0, t1, 2f
    sw zero, 0(t0)
    addi t0, t0, 4
    j 1b
2:
    call main
    # main 返回时记录退出码；platform_exit 最终停在本地循环。
    call platform_exit
3:
    j 3b
.size _start, .-_start
