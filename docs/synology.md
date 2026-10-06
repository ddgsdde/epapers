# 群晖部署

工作台运行在群晖 Container Manager 中。群晖负责 HA API 订阅、字体、渲染、页面与日志存储；ESP32-S3 通过 WebSocket 连接群晖；HA 只运行轻量原生集成。

1. 从 [GitHub Release](https://github.com/ddgsdde/epapers/releases/tag/v0.3.1) 下载 `epapers-synology-0.3.1-amd64.zip`（私有仓库需登录），解压到 `/volume1/docker/epapers`（具体路径按群晖共享文件夹调整）。
2. 将 `epapers-0.3.1-amd64.image.tar.gz` 导入 Container Manager。SSH 可执行 `docker load -i epapers-0.3.1-amd64.image.tar.gz`。从 `.env.example` 创建 `.env`，填写两个不同的随机 API/网关密钥。HA 地址和长期访问令牌可稍后在网页设置中保存。私有 `.env` 不包含在 Docker 镜像中。
3. Container Manager → 项目 → 新增，选择该目录的 `compose.yaml`。也可通过 SSH 在项目目录执行 `docker compose up -d`。从源码构建则使用 `docker compose -f compose.yaml -f compose.build.yaml up -d --build`。
4. 打开 `http://群晖地址:8099`。第一次访问输入 `.env` 中的 `EPAPERS_API_TOKEN`，再到“设置”配置 HA Base URL 和 API Key。Key 保存后不能在页面中查看，留空则保留现有 Key。
5. 把 ESP32-S3 的服务器配置为 `ws://群晖地址:8099/ws/gateway`，并填入 `EPAPERS_GATEWAY_TOKEN`。USB 桥接只用于本机联调。
6. 工作台“设置”→“下载 HA 集成”，解压后将 `epapers` 文件夹复制到 HA 的 `/config/custom_components/epapers`，重启 HA，然后添加 epapers 集成。地址填写 `http://群晖地址:8099`，密钥使用 `EPAPERS_API_TOKEN`。
7. HA 设置 → 仪表盘 → 添加仪表盘 → 网页，填写工作台地址，命名“墨水屏”，显示在侧边栏。HA 现行网页仪表盘取代旧 iframe panel。若 HA 使用 HTTPS，工作台也通过群晖反向代理提供 HTTPS，以便浏览器正常嵌入。

持久化数据位于 `./data`，包含 SQLite、页面、图片、日志和私有 HA 连接凭据。备份 `data` 和私有 `.env`；镜像与程序可重新构建。默认容器内存上限 512MB，后续按实测峰值调整。HA 不加载字体和渲染进程。

原生集成提供每屏内部温度、电量、供电/电池电压、RSSI、全刷/快速刷新次数、最近出现/刷新/对时、错误、任务状态、连接传感器、型号选择及操作按钮。工作台设置屏幕的 HA 名称、实体 ID 前缀和房间后，插件约 15 秒内同步。`epapers.set_text` 和 `epapers.set_entity` 可从 HA 自动化修改指定组件。型号改变导致组件超出新尺寸时，请先在工作台调整页面。

整页轮播与组件轮播都在工作台保存后统一传到 MCU，断开网关仍按整数分钟轮换。希望降低大幅画面切换时，关闭整页轮播，只使用组件轮播。编辑好的画布可保存为“我的模板”。“固件”页面可上传网关应用 `.bin` 和墨水屏 `.img`，并逐台确认重启后的固件标识；墨水屏无线升级需要先安装支持 SUOTA 的基础固件。当前实机已安装 Flash 基础固件，见 [验证记录](implementation.md)。

镜像为 `epapers:0.3.1`。x86 群晖使用 `linux/amd64` 镜像，ARM 群晖应从源码构建对应 `linux/arm64` 镜像。项目中不包含本机凭据、用户数据、SDK 或原始硬件备份。

参考：[HA 网页仪表盘](https://www.home-assistant.io/dashboards/dashboards/#webpage-dashboard)、[HA 原生协调器](https://developers.home-assistant.io/docs/integration_fetching_data/)。
