# epapers

Home Assistant 墨水屏工作台：在本机或群晖 Docker 编辑 2.13 寸黑白屏画布，通过 ESP32-S3 蓝牙网关下发至 DA14585。HA 提供实体和历史记录，字体与渲染在工作台运行。

## 功能

- 画布编辑、中文字体与字重、多种内置模板、用户模板复用。
- HA 实体按房间、设备、领域和类别筛选；温湿度、功率、状态及数值属性显示。
- HA Recorder 趋势图、自定义历史时长、15 分钟网格和数值标注。
- 每块屏幕独立设置内容同步间隔，30～86400 秒；趋势图定时读取历史，显示包相同则跳过发送。
- MCU 本地时钟、日期与倒计时；断线重连和至少每 24 小时校时。
- 整页轮播和组件轮播，停留时间按整数分钟设置；已下发内容可离线轮换。
- 自动发现网关和屏幕、名称与房间配置、删除与恢复、任务和刷新状态观察。
- 屏幕内部温度、供电电压和电量上报；配套 HA 集成同步设备和实体。
- 网关网页 OTA、墨水屏 BLE SUOTA 批量升级，重启后核对固件标识。
- ESP32-S3 手机热点配网：扫描 Wi-Fi、填写服务器地址和网关 Token，保存后重连；密码与 Token 不回显。
- 小变化快速刷新，大变化与整点清残影使用全刷；A41 每次仍写完整控制器 RAM，避免断电丢失 RAM 后花屏。

当前硬件实测为 HINK-E0213A41，70 字节 LUT、引脚组 6。HINK-E0213A55 和 OPM021B1 已通过主机协议与驱动模拟测试，对应实物尚待验证。只支持黑白屏。200mAh 电池续航尚未实测。

## 群晖安装：使用现成镜像

[GitHub 容器镜像](https://github.com/ddgsdde/epapers/pkgs/container/epapers)：`ghcr.io/ddgsdde/epapers:0.3.1`，适用于 x86 / amd64 群晖。源码、Release 与容器包保持私有，使用有权限的 GitHub 账号访问。

1. 从 [v0.3.1 Release](https://github.com/ddgsdde/epapers/releases/tag/v0.3.1) 下载 `compose.yaml` 和 `env.example`，放到共享文件夹，例如 `/volume1/docker/epapers`，将 `env.example` 重命名为 `.env`。也可使用完整部署包。
2. 为有包访问权限的 GitHub 账号创建具有 `read:packages` 权限的 Personal access token (classic)。SSH 登录群晖，在 Docker 命令使用的同一账号下执行下列命令，在 Password 提示输入该令牌（不是 GitHub 密码，也不是 HA Key）：

   ```sh
   docker login ghcr.io -u ddgsdde
   ```

3. 在 `.env` 设置两个不同的随机值：`EPAPERS_API_TOKEN` 和 `EPAPERS_GATEWAY_TOKEN`。HA 地址与 Key 可稍后在网页填写。
4. 在项目目录执行：

   ```sh
   docker compose pull
   docker compose up -d
   ```

5. 打开 `http://群晖地址:8099`，输入 `.env` 中的 `EPAPERS_API_TOKEN`。在“设置”保存 HA Base URL 与长期访问令牌；保存后 Key 不回显，留空则保留已有 Key。
6. 配置网关服务器地址 `ws://群晖地址:8099/ws/gateway` 和 `.env` 中的 `EPAPERS_GATEWAY_TOKEN`。

`compose.yaml` 直接拉取 GHCR 镜像。若用 Container Manager 图形界面创建项目，需要在其映像仓库配置中设置 GHCR 的账号与令牌；SSH 的登录凭据不会自动共享给图形界面。所有用户数据位于 `./data`，升级时保留并备份该目录及 `.env`。

完整部署包中的 `.image.tar.gz` 保留作离线安装：`docker load -i epapers-0.3.1-amd64.image.tar.gz`，使用包内的 `compose.offline.yaml`。在线安装无需下载镜像压缩包。

详细说明见 [群晖部署](docs/synology.md) 和 [轮播、独立同步间隔与升级](docs/firmware-and-carousel.md)。

## 从源码运行

需要 Python 3.13、uv 和 Node.js / npm。在项目根目录执行：

```sh
uv sync --locked
cp .env.example .env
```

编辑 `.env` 中的私有访问密钥，然后构建网页：

```sh
npm --prefix frontend ci
npm --prefix frontend run build
uv run epapers
```

访问 `http://127.0.0.1:8099`，在网页配置 HA。源码构建 Docker 使用：

```sh
docker compose -f compose.yaml -f compose.build.yaml up -d --build
```

GHCR 和 Release 预构建镜像仅包含 amd64，其他架构可从源码构建，尚未做实机验证。GitHub Actions 的 `Publish container to GHCR` 支持手动发布或 `v*` 标签触发；镜像版本必须与 `pyproject.toml` 一致，上传后自动验证拉取和容器运行。

## 网关手机配网

网关 0.3.2 首次上电开启 `epapers-xxxxxx` 开放配网热点，手机连接不需要密码。连接后通过联网检测跳转到配网页面；也可手动打开 `http://192.168.4.1`，填写家中 2.4GHz Wi-Fi、电脑或群晖的工作台地址及 `EPAPERS_GATEWAY_TOKEN`，点击“保存并连接”。持续连接工作台 60 秒后关闭热点；运行时长按 BOOT 3 秒可再次开启。0.3.2 修复了热点可连接但网页拒绝连接的问题。完整流程见 [手机配网](docs/gateway-phone-setup.md)。

## HA 集成

从工作台“设置”下载，或使用 Release 中的 `epapers-ha-0.2.0.zip`。把 `epapers` 放入 HA 的 `/config/custom_components/epapers`，重启并添加 epapers 集成。地址填写工作台地址，密钥填写 `EPAPERS_API_TOKEN`。也可在 HA 网页仪表盘中嵌入工作台。

## 固件与验证

- 网关源码：`firmware/gateway`；构建脚本：`scripts/build_gateway.sh`。需要本机 ESP-IDF 环境。
- DA14585 源码覆盖层：`firmware/tag/src`；构建脚本：`scripts/build_tag.py`。需自行取得官方 SDK6，并用 `--sdk` 和 `--toolchain` 指定 SDK 与 GNU Arm 工具链。
- 首次有线安装后，墨水屏可通过网页 → 网关 → BLE SUOTA 升级。原部署包提供网关 0.3.0、墨水屏 0.3.1 和墨水屏 0.3.0 回退镜像；[网关 0.3.2 Release](https://github.com/ddgsdde/epapers/releases/tag/gateway-v0.3.2) 提供修复版手机配网网关更新 `epapers-gateway-0.3.2.bin`。
- 多块墨水屏初装请使用 [墨水屏 0.3.2 Release](https://github.com/ddgsdde/epapers/releases/tag/tag-v0.3.2)。同一个 BIN 根据每颗芯片的出厂 OTP 数据生成稳定的独立蓝牙地址；旧版 BIN 内置测试板地址，多块烧入会被识别为同一个设备。迁移步骤见 [屏幕身份与烧录](docs/tag-device-identity.md)。
- [墨水屏 0.3.3 Release](https://github.com/ddgsdde/epapers/releases/tag/tag-v0.3.3) 保留独立地址修复，并让“读取状态”主动采样当前电压；普通画布传输仍使用 10 分钟采样缓存。网页与 HA 集成会同步新读数，工作台和网关无需升级。
- 项目不包含官方 SDK、用户提供的参考固件、本机凭据、用户数据或原始硬件备份。字体授权文件与字体一起保存于 `assets/fonts`。

测试与构建：

```sh
uv run pytest -q
uv run ruff check backend tests
npm --prefix frontend run build
```

当前记录：190 项主机测试通过、1 项跳过；已验证真实 HA 历史读取、功率页面自动下发、逐屏同步间隔、双分区正常 OTA 和 A41 显示。0.3.2 的屏幕身份修复已完成 SDK 地址生成测试和固件构建，两块实物同时发现仍待验证。0.3.3 已验证状态命令重新采样、传输回执复用缓存和工作台接收新读数，新的实物电压采样尚待烧录验证。网关手机配网修复增加热点接口、HTTP / DNS 状态和请求计数诊断。参考 [实施记录](docs/implementation.md) 与 [屏幕驱动分析](docs/panel-firmware-analysis.md)。

参考项目：[tpunix/HMCLOCK](https://github.com/tpunix/HMCLOCK)。
