ENTRY(_start)
MEMORY {
    ROM (rx) : ORIGIN = 0x00000000, LENGTH = 64K
    RAM (rw) : ORIGIN = 0x10000000, LENGTH = 64K
}
SECTIONS {
    .text : { KEEP(*(.text.start)) *(.text*) . = ALIGN(4); } > ROM
    /* CPU data routing only maps 0x1000xxxx. Initialize this section in data RAM. */
    .rodata : { *(.rodata*) . = ALIGN(4); } > RAM
    .data : { *(.data*) *(.sdata*) . = ALIGN(4); } > RAM
    .bss (NOLOAD) : { . = ALIGN(4); __bss_start = .; *(.bss*) *(.sbss*) *(COMMON) . = ALIGN(4); __bss_end = .; } > RAM
    ASSERT(__bss_end <= 0x1000C000, "Reserve at least 16KB stack")
    /DISCARD/ : { *(.comment) *(.eh_frame*) }
}
