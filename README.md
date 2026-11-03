# 128通道麦克风阵列采集与定向音频处理

FPGA负责音频/视频采集和传输，片内RISC-V固件负责板端控制，Windows上位机完成定向音频增强、显示与交互。

## 目录

| 目录 | 内容 |
| --- | --- |
| `fpga/Prj/` | PDS工程、自主RTL、约束、IP配置与必要初始化HEX |
| `fpga/tools/` | 外部厂商IP检查及独立工作目录生成工具 |
| `firmware/array/` | 阵列固件、启动/链接脚本与编译配方 |
| `firmware/coremark/` | 独立CoreMark及平台移植 |
| `host/` | 上位机Python/C++源码、配置、测试及原生库构建入口 |

## 硬件与依赖

- FPGA：Logos2 PG2L200H-6FBB676，Pango Design Suite 2022.2-SP6.4。CPU63MHz，RV32I+Zmmul/ilp32，ROM和RAM各64KiB。
- 固件：xPack RISC-V GCC15.2.0-1。本次验证Windows64位、Python3.12.7。
- 上位机：Python3.12.7（含tkinter），NumPy、SciPy、Pillow、psutil、threadpoolctl、PyYAML，版本见`host/requirements.txt`；pyserial源码已随包并保留许可。
- 原生计算库：Qt6 Core/Gui开发包及匹配的C++20工具链、CMake、Ninja。实际验证Qt6.11.1、MinGW g++13.1.0、CMake3.30.5、Ninja1.12.1。
- 工具、许可证、Python及Qt运行环境由使用者准备；源码目录不附安装包或预编译DLL。

## FPGA构建

厂商生成实现作为外部依赖：公开目录保留IDF和HEX，省略79份受限实现及2份生成初始化常量。先在已获授权的PDS环境恢复到仓库之外的新目录：

```text
cd fpga
python tools/prepare_external_ip.py --project Prj --check
python tools/prepare_external_ip.py --project Prj --generate --out C:/fpga_local/Prj --pds-bin "PDS的bin目录"
```

生成前`--check`退出2并列出81个缺失文件是预期结果；输出目录须尚不存在。按本机工具要求配置许可证，或用生成工具的`--license`指定本地许可证路径。工具不覆盖仓库源码，精确依赖名单见`fpga/external_ip_files.txt`。

恢复后打开新工作目录的`ArrayAcquisition.pds`，或在那里运行`rebuild_sources.cmd`，完成综合、映射、布局布线和时序检查。审查新实现通过后再单独运行`generate_new_bit.tcl`生成位流。项目不附旧ADF或位流，全部阶段为初始状态。

## 固件构建

从仓库根目录执行：

```text
python -B firmware/array/build.py --check
python -B firmware/array/build.py --toolchain "RISC-V工具链bin目录"
python -B firmware/coremark/build.py --check
python -B firmware/coremark/build.py --toolchain "RISC-V工具链bin目录" --mode all --iterations 10000
```

产物写入各模块`build/`。`build_commands.csv`是阵列编译配方；`SOURCE_PROVENANCE.csv`是CoreMark来源校验输入，均由构建脚本读取。非必要历史清单已移出。

FPGA中的初始化HEX对应阵列固件。采用新固件须重新生成存储IP并完成新实现；仅替换HEX不会更新旧实现。CoreMark是独立目标，阵列固件开机直接进入阵列功能，没有自动跑分后切换流程。

## 上位机构建与启动

先在仓库根安装Python依赖：`python -m pip install -r host/requirements.txt`。随后在cmd.exe中执行，替换本地工具路径：

```bat
set "PATH=<cmake-bin>;<ninja-bin>;<compiler-bin>;%PATH%"
set "CMAKE_PREFIX_PATH=<Qt-kit-root>"
cd host
BuildNative.cmd
RunOfflineChecks.cmd
StartHost.cmd
```

两个计算DLL从所附C++源码本地构建，默认写系统临时目录。自选构建目录时用`BuildNative.py --build-dir <目录>`，并设置`HOST_NATIVE_BUILD_DIR`；源码和产物绑定校验保留。未构建原生库前可用`python -B VerifyPackage.py --source-only`检查源码。

默认COM8、UART921600、PC地址192.168.10.1、板卡192.168.10.2、UDP5001，可通过启动参数调整。不会自动修改网卡或烧录板卡。实机相机与阵列对齐需现场校准，合成夹具仅供测试，不作默认投影；会话日志及本地校准不应提交。

## 已验证范围

- 阵列固件及CoreMark两种模式均全新编译，ELF/HEX等产物逐字节复现原文件；源码检查通过。独立跑分尚未在CPU或实板执行，无本次运行CRC或分数。
- 三项官方IP生成在独立目录完成，81份恢复文件与比赛源码参考逐字节一致，三份IDF参数一致；194项必要输入闭包通过。
- 上位机两个C++原生库重编译及样本处理通过；323项离线测试通过。原位和英文新目录的两次3秒合成DSP测试，各发送、接受并处理48,000包，溢出与旧代丢弃为0，动态参考接受18次。这些不代表完整PCM尾部或实机长期播放验收。

本轮未重新运行完整FPGA综合、布局布线或STA，没有新增GUI、声卡、串口、板卡及声学验收。外部IO、CDC/复位、时钟约束和长期稳定性仍需按实际新实现核查。

## 版权

第三方原版权与许可保留，见`THIRD_PARTY_NOTICES.md`。厂商受限IP未取得明确公开再分发依据，故仅提供本地生成方式。项目原创部分的公开许可由权利人决定，本次整理未擅自添加统一开源许可证。

目录组织参考[指定FPGA竞赛仓库](https://github.com/change1010/PG2L100H_RK3568-2026)的分类与根README方式，未复制其代码或文字。
