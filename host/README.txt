128通道阵列上位机源码包

本包包含PC端显示、板端状态与音视频接收、交互控制、三维定向增强、校准和离线测试源码。关键词识别及板端功能由相应固件提供；本包不表示全部增强算法已在FPGA独立运行。

1. 环境与外部依赖
最低Python版本：3.12.7（验证使用Windows 64位Python 3.12.7，需标准tkinter）。先执行 python -m pip install -r requirements.txt。
原生库构建需要CMake 3.19以上、Ninja、Qt6 Core/Gui开发包及其兼容的C++20工具链。实际验证：CMake 3.30.5、Ninja 1.12.1、MinGW g++ 13.1.0、Qt 6.11.1。
本包不包含DLL、编译器或Qt SDK。frame_kernel与stream_post均提供完整编译源码；Qt及GCC运行库由外部安装的开发环境提供。相关许可与归属保存在licenses和THIRD_PARTY_NOTICES.txt。

2. 构建两个计算库
把CMake、Ninja和Qt兼容编译器的bin目录加入PATH，把CMAKE_PREFIX_PATH设为Qt套件根目录。示例使用占位符，须替换为实际安装位置：
set "PATH=<cmake-bin>;<ninja-bin>;<compiler-bin>;%PATH%"
set "CMAKE_PREFIX_PATH=<Qt-kit-root>"
BuildNative.cmd
也可执行 python -B BuildNative.py --build-dir <源码目录外的构建目录>。
默认产物位于系统临时目录的array_host_native下，不写入源码包。如使用自选目录，后续设置HOST_NATIVE_BUILD_DIR为同一位置。HOST_DLL_DIRS可补充外部运行库目录。
构建生成两个DLL和native_manifest.yaml；运行时同时检查当前原生源文件摘要、产物摘要及构建来源绑定。该机制替代预编译DLL的固定摘要，不取消完整性检查。

3. 校验与启动
未安装原生库时可执行 python -B VerifyPackage.py --source-only，检查源码摘要、语法和入口配置。
构建完成后执行 RunOfflineChecks.cmd 或 python -B VerifyPackage.py --tests。
启动：StartHost.cmd。可用 --port COM8 --bind 192.168.10.1 --board-ip 192.168.10.2 --udp-port 5001覆盖板卡默认通信参数；不会自动改网卡或烧写板卡。HOST_PYTHON可指定Python解释器。
本包不含实机相机校准。启动明确报告未校准；请在实际安装位置完成界面十点校准，或用 --alignment-file <本地配置>指定适用的校准。默认本地结果写到config/local_alignment.yaml，已列入.gitignore。
host/fixtures/synthetic_camera_alignment仅为测试生成的数据，绝不自动当作实机投影。阵列geometry_source.yaml保留一般硬件坐标，非个人现场位置。
会话数值日志写入runs；主入口保留不保存音视频媒体的行为。用户本地配置、日志、编译产物不应加入Git仓库。

4. 源码与检查维护
SHA256SUMS、host/MANIFEST.yaml、MONITOR_HASHES.yaml、LOCAL_SUPPORT_BINDING.yaml及DSP/FREEZE.yaml均为当前源码完整性数据，不是历史验证报告。维护者有意修改源码后执行 python -B RefreshIntegrity.py，再重新构建和测试；启动不会自动重写摘要。
帧计算及BP/LogMMSE/AGC的C++算法和DLL内存JSON接口保持不变；配置/状态使用YAML，事件使用YAML多文档.log。
完整DSP空输出测试：python -B host/smoke_3d_paced.py --seconds 3 --out <源码外的结果.yaml>，输入是合成信号，不访问声卡、GUI、串口或网络。
dsp/scripts中部分历史批量评估入口需要未附语料和探针，未作为本包主运行或离线验证入口。回归用旧实现仍保留，维护工具不得直接覆盖正在运行的源码。

5. 验证边界
源码检查、离线单测和短时合成DSP测试不能代替实机联合验收。既有现场欠供、RAW缺包及长期稳定性问题不因源码打包而视为解决；声学质量、声卡连续性和FPGA实时性仍须分别验证。