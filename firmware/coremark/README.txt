独立 CoreMark 源码工程

本目录只交付源码、链接脚本、构建入口及必要的官方版权资料，不附带历史 ELF、HEX、map 或构建缓存。它与阵列主固件分开；独立跑分结束会写退出寄存器并停在本地循环，不会自动进入阵列程序。

平台
  CPU：rv32i_zmmul / ilp32，指令和数据存储各64KiB，入口PC=0。
  周期计数：0x20002000低32位、0x20002004高32位，高/低/高重读；实际换算频率63000000Hz。
  计时边界：写0x20002008，1开始、2结束，不复位自由运行计数器。
  UART：0x20000000字节输出，板级921600 baud、8N1；配套RTL在FIFO不ready时回压写握手。
  退出：0x2000f000写功能状态，随后永久循环；退出值本身不是有效跑分证明。

源码与许可证
  官方源固定提交：1f483d5b8316753a742cbf5590caf5bd0a4e4777。
  五份算法C文件及官方header逐字保留。vendor/coremark/LICENSE.md包含原许可、归属及CoreMark使用条款，必须随工程保留。
  SOURCE_PROVENANCE.csv保留10份官方输入与6份本地移植文件的原始来源摘要；platform.h的63MHz默认值为本地适配，当前交付内容另由SHA256SUMS核对。
  官方header的旧MD5清单差异和固定提交校验方式见UPSTREAM_SOURCE.txt。

检查与重新生成
  本次验证环境为Python 3.12.7和xPack RISC-V GCC 15.2.0-1；脚本仅使用Python标准库，编译器及其rv32i/ilp32 libgcc属于外部安装工具。
  仅检查源码、许可证及本地依赖，不编译、不要求产物、不访问硬件：
    python build.py --check
  从本目录重新生成两种模式，默认各10000次：
    python build.py --toolchain "工具链的bin目录" --mode all --iterations 10000
  也可设置RISCV_TOOLCHAIN，或把工具链bin加入PATH。
  输出仅写本目录build/performance和build/validation，包括program.elf、program.map、imem.hex、dmem.hex、BUILD_REPORT.txt、commands.csv及逐条工具日志。
  全部算法与port采用相同编译参数。构建会检查最终链接指令集、ELF加载范围、64KiB镜像格式与ELF/HEX一致性；生成的build_flags.h是本次实际参数记录，不是缺失的外部依赖。
  源码及构建均不需要JSON或TSV。源码校验不依赖已生成的测试产物。

验证范围
  成功构建只证明交叉编译及静态产物检查通过，不能记为CPU已执行、运行期CRC通过、已测得分数或已上板。
  用921600/8N1完整捕获一次新的实际串口日志后，可运行：
    python check_log.py 实测串口.txt --mode performance --iterations 10000
    python check_log.py 实测串口.txt --mode validation --iterations 10000
  默认核对规定CRC、种子CRC、迭代次数、完整64位ticks、63MHz换算及至少10秒真实计时。--crc-only只检查功能字段，短程不会输出分数。整数Total ticks在ILP32下仅为低32位，秒数与分数使用SIM_CYCLES_HEX的完整值。
  port中的SIM_CRC_PASS是沿用的状态文字；即使出现，官方10秒检查失败时也不能记为有效成绩。本目录不附带旧100MHz日志作为新63MHz结果。

独立FPGA使用
  必须在独立FPGA工程副本中装入同一模式的一对imem.hex、dmem.hex，重新生成两份原生存储IP及初始化常量，然后重新映射、布局布线、时序核查和生成位流。
  只换磁盘HEX不会改变现有阵列位流，不能用旧ADF只生成bit来代替完整重建；也不会在现有array_capture.sbit里自动切换。构建脚本不调用EDA、下载、串口或Flash。
