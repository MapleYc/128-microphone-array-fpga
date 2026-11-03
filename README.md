# 128通道麦克风阵列采集与定向音频处理

本作品采用FPGA、片内RISC-V与PC协同架构，实现128通道麦克风阵列采集、音视频传输、定向音频增强和可视化控制。

本次参赛方案由FPGA完成采集与传输，RISC-V固件负责板端控制，Windows上位机完成定向增强、显示和交互。仓库提供这套FPGA＋PC方案的工程源码与构建入口。

## 工程目录

| 目录 | 内容 |
| --- | --- |
| `fpga/Prj/` | PDS工程、RTL、约束、IP配置及存储初始化HEX |
| `fpga/tools/` | 厂商IP依赖检查与本地生成工具 |
| `firmware/array/` | 阵列固件、启动与链接脚本、编译配方 |
| `firmware/coremark/` | 独立CoreMark程序与平台移植 |
| `host/` | 上位机Python/C++源码、配置、测试及原生库构建入口 |

## 硬件与开发环境

- FPGA：Logos2 PG2L200H-6FBB676；Pango Design Suite 2022.2-SP6.4。
- CPU：63MHz，RV32I+Zmmul、ilp32，指令ROM与数据RAM各64KiB。
- 固件工具链：xPack RISC-V GCC15.2.0-1。
- 上位机：Windows64位、Python3.12.7，含标准tkinter。
- Python依赖：NumPy、SciPy、Pillow、psutil、threadpoolctl、PyYAML，版本见`host/requirements.txt`；pyserial源码位于`host/host/vendor/`。
- 原生计算库：Qt6 Core/Gui开发包及匹配的C++20工具链。验证环境使用Qt6.11.1、MinGW g++13.1.0、CMake3.30.5、Ninja1.12.1。

## FPGA构建

工程通过IDF和初始化HEX描述厂商IP配置，生成实现由已获授权的PDS环境提供。所需文件见`fpga/external_ip_files.txt`，包含79份厂商实现与2份生成初始化常量。

在仓库之外的新工作目录生成依赖：

```text
cd fpga
python tools/prepare_external_ip.py --project Prj --check
python tools/prepare_external_ip.py --project Prj --generate --out C:/fpga_local/Prj --pds-bin "PDS的bin目录"
```

IP生成前，`--check`会以退出码2列出81个缺失文件。输出目录须尚不存在；许可证按本地PDS环境配置，也可用`--license`指定本地许可证路径。

依赖准备完成后，在新工作目录打开`ArrayAcquisition.pds`，或运行`rebuild_sources.cmd`，依次完成综合、映射、布局布线和时序检查。确认新实现的时序和约束覆盖后，单独运行`generate_new_bit.tcl`生成位流。

## 固件构建

从仓库根目录执行：

```text
python -B firmware/array/build.py --check
python -B firmware/array/build.py --toolchain "RISC-V工具链bin目录"
python -B firmware/coremark/build.py --check
python -B firmware/coremark/build.py --toolchain "RISC-V工具链bin目录" --mode all --iterations 10000
```

产物位于各模块的`build/`目录。`build_commands.csv`保存阵列固件编译配方，`SOURCE_PROVENANCE.csv`用于CoreMark源码来源校验。

FPGA初始化HEX对应阵列固件。采用新的固件HEX后，需要重新生成存储IP并完成FPGA实现。阵列固件启动后进入阵列任务；CoreMark作为独立目标构建和运行。

## 上位机构建与启动

在仓库根目录安装Python依赖：

```text
python -m pip install -r host/requirements.txt
```

在cmd.exe中设置本地工具路径，然后构建、检查并启动：

```bat
set "PATH=<cmake-bin>;<ninja-bin>;<compiler-bin>;%PATH%"
set "CMAKE_PREFIX_PATH=<Qt-kit-root>"
cd host
BuildNative.cmd
RunOfflineChecks.cmd
StartHost.cmd
```

两个计算DLL由所附C++源码本地构建，默认产物位于系统临时目录。自选构建目录时使用`BuildNative.py --build-dir <目录>`，并设置`HOST_NATIVE_BUILD_DIR`。构建前可执行`python -B VerifyPackage.py --source-only`检查源码。

默认串口为COM8、UART波特率921600；PC地址为192.168.10.1，板卡地址为192.168.10.2，UDP端口为5001。可通过启动参数调整，网卡设置与板卡烧录由操作者完成。

相机与阵列对齐需按实际安装位置进行现场校准。测试夹具中的合成标定仅用于离线检查；会话日志和本地校准配置保存在本机。

## 验证情况

- 阵列固件与CoreMark两种模式均可全新编译，ELF与HEX一致性检查通过。CoreMark独立运行成绩以实板串口日志为准，当前未记录本方案63MHz下的实测分数。
- 三项厂商IP在独立目录生成成功，81项生成文件比对一致，三份IDF参数一致，194项必要输入闭包检查通过。
- 两个上位机C++原生库的构建、加载及样本处理通过，323项离线测试通过。
- 两个独立目录的3秒合成DSP测试各发送、接受并处理48,000包，溢出与旧代丢弃为0，动态参考接受18次。

合成测试用于检查数据链路与算法调用。声学效果、音频尾部完整性和长时间播放稳定性需结合实机条件评估。源码重建后，应重新检查该实现的时序、外部IO、CDC与复位约束。

## 第三方组件与许可

项目使用CoreMark、TensorFlow/micro_speech、KissFFT、pyserial等第三方组件，各组件的版权声明与许可证保留在对应目录，汇总见`THIRD_PARTY_NOTICES.md`。

Pango IP按厂商授权使用，生成实现通过本地PDS工具准备；Python、Qt及C++工具链依照各自许可安装和使用。
