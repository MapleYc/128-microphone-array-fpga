    .section .text.start
    .globl _start
_start:
    li sp, 0x10010000
    la t0, __bss_start
    la t1, __bss_end
1:  bgeu t0, t1, 2f
    sw zero, 0(t0)
    addi t0, t0, 4
    j 1b
2:  call main
    li t0, 0x2000f000
    bnez a0, 3f
    li a0, 0x128c0001
    j 4f
3:  li t1, 0xdead0000
    or a0, a0, t1
4:  sw a0, 0(t0)
5:  j 5b
