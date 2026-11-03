/* 裸机链接布局：指令放 0x0 的 64 KiB IMEM，静态数据/只读数据/栈放
 * 0x1000_0000 的 64 KiB DMEM；地址必须与 SoC 译码和镜像导出脚本一致。 */
OUTPUT_ARCH(riscv)
ENTRY(_start)
MEMORY
{
    IMEM (rx) : ORIGIN = 0x00000000, LENGTH = 64K
    DMEM (rw) : ORIGIN = 0x10000000, LENGTH = 64K
}
SECTIONS
{
    .text : ALIGN(4) {
        KEEP(*(.text.start))
        *(.text .text.*)
        . = ALIGN(4);
    } > IMEM
    .rodata : ALIGN(4) {
        *(.rodata .rodata.* .srodata .srodata.*)
        . = ALIGN(4);
    } > DMEM
    .data : ALIGN(4) {
        __data_start = .;
        /* gp 指向小数据区附近，供编译器生成的 gp 相对寻址使用。 */
        __global_pointer$ = . + 0x800;
        *(.data .data.* .sdata .sdata.*)
        . = ALIGN(4);
        __data_end = .;
    } > DMEM
    .bss (NOLOAD) : ALIGN(4) {
        /* 不占 ELF 文件镜像空间；start.S 在调用 main 前逐字清零。 */
        __bss_start = .;
        *(.bss .bss.* .sbss .sbss.* COMMON)
        . = ALIGN(4);
        __bss_end = .;
    } > DMEM
    __stack_top = ORIGIN(DMEM) + LENGTH(DMEM);
    /* 顶部预留 8 KiB 向下增长的栈；静态区越界时由 ASSERT 拒绝链接。 */
    __stack_bottom = __stack_top - 8192;
    ASSERT(__bss_end <= __stack_bottom, "DMEM overlaps reserved 8 KiB stack")
    /DISCARD/ : { *(.comment .note .eh_frame .eh_frame.*) }
}
