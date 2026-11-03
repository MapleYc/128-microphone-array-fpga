本源码包不包含实机相机校准，也不默认启用任何合成投影。

首次启动时处于未校准状态。请在实际安装位置完成界面中的十点声画校准；结果默认写入config/local_alignment.yaml，该文件已列入.gitignore，不应提交个人现场观测。
也可用StartHost.cmd --alignment-file <本地校准文件>指定已有且适用于当前安装位置的校准。
host/fixtures/synthetic_camera_alignment只供单元测试，不作为实机配置。

串口与网络可通过StartHost.cmd --port COM8 --bind 192.168.10.1 --board-ip 192.168.10.2 --udp-port 5001配置。这里是板卡默认示例，并非自动网卡配置。
