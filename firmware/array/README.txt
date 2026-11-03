阵列 CPU 固件源码工程

本目录保留阵列程序、启动代码、链接脚本及全部编译依赖。目标为 RV32I_ZMMUL / ilp32，指令存储与数据存储各 64 KiB。

本次验证环境：Python 3.12.7；xPack RISC-V GCC 15.2.0-1（bin 目录须有 riscv-none-elf-gcc.exe、riscv-none-elf-g++.exe）。构建脚本仅使用 Python 标准库。

检查源码和构建输入（不调用编译器）：
  python build.py --check
从源码构建：
  python build.py --toolchain "工具链的 bin 目录"
也可将工具链 bin 目录加入 PATH，或设置 RISCV_TOOLCHAIN。

构建入口为 build.py。build_commands.csv 按原顺序保存 34 步命令；每个参数按 UTF-8 Base64url 编码，保留空格、引号和换行。启动入口是 source/base/firmware/meeting_test_start.S；应用由 source/main/main.c 引入 fitter.c 和 source/entry/meeting_av_main.c；内存布局由 source/base/firmware/meeting_test.ld 指定。编译选项、宏、目标指令集和链接参数保持不变。

首次构建自动创建 build 目录。输出为 build/firmware.elf、build/firmware.map、build/images/imem.hex、build/images/dmem.hex，并保存各步日志和 BUILD_REPORT.txt。HEX 按每行一个 32 位字、小端存储生成，每份 16384 行。构建后检查 ELF 地址范围、镜像长度及 HEX 反向编码一致性。该检查不执行板卡程序，不生成 FPGA 位流，也不写 Flash。

SHA256SUMS 校验当前源码包；build 目录不属于输入清单。第三方来源、许可证与实际分发范围见 source/base/vendor/micro_speech_baseline 下的 SOURCE_NOTICE.md、UPSTREAM_SOURCE.txt、BUNDLE_SCOPE.txt、LICENSE 和 kissfft_v130/COPYING；补丁版 KissFFT 的 COPYING 位于 source/base/work/kws_baseline_20261022/frontend/kissfft_patched。历史完整语料下载记录不属于本源码包。

本目录不包含预生成固件。将新生成 HEX 导入 FPGA 时，须在独立 FPGA 工程中重新生成 CPU 存储 IP 和位流；仅替换 HEX 文本不会改变现有位流。
