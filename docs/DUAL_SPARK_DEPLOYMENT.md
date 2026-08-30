# 双 DGX Spark 部署手册

## 1. 部署拓扑

```text
受控演示终端
      │ HTTPS/HTTP（当前Demo仅受控私网HTTP）
      ▼
Spark B · 192.168.100.11
Gateway / Session / Knowledge / P01 / Evidence / Audit / Report
      │ OpenAI-compatible API + Bearer key
      │ 专用25/100 GbE本地链路
      ▼
Spark A · 192.168.100.10
vLLM Vision（有限代表帧）/ optional Embedding / future scientific adapters
```

这是一套应用层双节点系统。Spark A和Spark B各自运行独立进程与模型；当前不启用跨机张量并行，也不合并两台设备的统一内存。原始图片/视频、派生帧、证据图和报告保存在 Spark B；Spark A 只接收当前任务需要的有限代表帧，并返回受限的模型观察或嵌入结果。

## 2. 站点前置条件

两台设备均需由机构管理员准备：

- 受支持的DGX OS、驱动和固件；
- Docker、Docker Compose 2.30 或更高版本、NVIDIA Container Toolkit；
- `nvidia-smi`和GPU容器透传正常；
- 固定的专用私网IPv4；
- 25 GbE或100 GbE链路，建议使用独立直连或隔离VLAN；
- 两端一致的MTU。1500可用；启用9000前必须确认网卡、线缆、交换机和两端配置完全一致；
- 独立管理网络或经批准的SSH入口；
- 加密磁盘及备份目标。

部署脚本不会安装驱动、改网卡、改防火墙或开启SSH，以避免破坏机构现有管理通道。

## 3. 网络配置与验证

两台`.env`使用同一组地址：

```dotenv
SPARK_A_IP=192.168.100.10
SPARK_A_BIND_IP=192.168.100.10
SPARK_B_IP=192.168.100.11
SPARK_B_BIND_IP=192.168.100.11
VISION_BASE_URL=http://192.168.100.10:8001/v1

# 留空时按到对端的路由自动识别网卡。
INTERCONNECT_INTERFACE=
INTERCONNECT_MIN_MBPS=25000
INTERCONNECT_MTU_MIN=1500
ALLOW_UNKNOWN_LINK_SPEED=NO

# 媒体资源上限
RELICSCOPE_MAX_UPLOAD_BYTES=8388608
RELICSCOPE_MAX_VIDEO_BYTES=268435456
RELICSCOPE_MAX_VIDEO_FRAMES=12
RELICSCOPE_MAX_FRAME_BYTES=2097152
```

默认原视频上限为 256 MiB，一次最多分析 12 帧，单帧上限为图片上限与 2 MiB 中较小者。调整这些值前同时核对 Spark B 数据盘、备份窗口、反向代理请求限制、浏览器内存和机构数据保留政策。普通图片请求不会因视频上限而被无条件放宽。

若站点要求100 GbE，把`INTERCONNECT_MIN_MBPS`设为`100000`。完成第 4 节安装初始化并核对 `.env` 后，可在两台设备分别运行只检查链路的命令：

```bash
./deploy/network-preflight.sh --role spark-a
./deploy/network-preflight.sh --role spark-b
```

网络预检会确认：对端路由、预期本地IP、接口状态、协商速率和MTU。它不会自动配置网络，也不要求镜像与模型已经缓存。完整的 `make preflight` 必须在第 5 节预缓存并恢复离线标志后运行。若驱动无法通过sysfs报告速率，管理员确认物理链路后可显式设置`ALLOW_UNKNOWN_LINK_SPEED=YES`；这项例外应进入部署记录。

推荐最小防火墙关系：

| 目标 | 端口 | 允许来源 |
|---|---:|---|
| Spark A视觉 | 8001/tcp | 仅Spark B固定IP |
| Spark A可选嵌入 | 8003/tcp | 仅Spark B固定IP |
| Spark B网关 | 8088/tcp | 仅受控演示/业务网段 |
| Spark B本地reasoner | 不映射 | 仅Compose私有网络 |

先核对SSH和管理网，再由站点管理员实施防火墙；本包不自动执行UFW规则。

## 4. 安装初始化

只在Spark B生成一次服务密钥：

```bash
make install ROLE=spark-b INSTALL_ARGS="--generate-key"
```

通过机构现有安全通道把同一密钥复制到临时受控路径，再在Spark A安装：

```bash
make install ROLE=spark-a INSTALL_ARGS="--service-key /secure-transfer/service_api_key"
```

`install.sh`会：

- 首次复制`.env.example`为权限`600`的`.env`；
- 自动写入当前非root用户的UID/GID；
- 创建权限`700`的数据、模型、编译缓存、备份和包目录；
- 验证共享key长度和权限；
- 检查ARM64、Docker、Compose和`nvidia-smi`。

它不会下载镜像或模型。初始化后必须人工核对两台`.env`，尤其是IP、绑定地址、模型许可、绝对目录和角色。

## 5. 联网准备与离线锁定

准备窗口开始前还需完成两项受控凭据配置：

- 使用机构批准的凭据助手登录 `nvcr.io`，并确认 Docker Hub 或机构批准的 Python 基础镜像源可访问；脚本不会代替管理员登录镜像仓库，也不要把 registry token 写入脚本、`.env` 或命令参数；
- 将有权访问所选模型的只读 Hugging Face token 保存为 `secrets/hf_token`，权限设为 `600`。默认 Spark A 视觉模型预缓存会要求该文件；它只挂载给一次性预缓存容器，不进入正常运行容器或发布包。

在机构批准的准备窗口，阅读所选模型卡并配置：

在任何大体积下载前先执行 `make check`，确认仓库脚本、Compose 文件和 OpenSpec 结构可解析。

```dotenv
ALLOW_NETWORK_DOWNLOADS=YES
ACCEPT_MODEL_TERMS=YES
OFFLINE_RUNTIME=0
HF_HUB_OFFLINE=0
TRANSFORMERS_OFFLINE=0
```

分别执行：

```bash
make prefetch ROLE=spark-a
make prefetch ROLE=spark-b
```

只有启用可选组件时才设置：

```dotenv
PREFETCH_EMBEDDING=1
PREFETCH_REASONER=1
```

准备完成后立即恢复：

```dotenv
ALLOW_NETWORK_DOWNLOADS=NO
ACCEPT_MODEL_TERMS=NO
OFFLINE_RUNTIME=1
HF_HUB_OFFLINE=1
TRANSFORMERS_OFFLINE=1
```

随后实施网络层的运行期出站阻断。环境变量防止常用库自动联网，防火墙/网关策略才是可证明的隔离边界。

离线锁定完成后，在启动前执行完整预检：

```bash
make preflight ROLE=spark-a
make preflight ROLE=spark-b
```

## 6. 启动顺序

先启动感知节点：

```bash
make start ROLE=spark-a
make health ROLE=spark-a
```

视觉模型首次加载可能耗时数分钟。Spark A健康后再启动Spark B：

```bash
make start ROLE=spark-b
make health ROLE=spark-b
```

健康检查会验证：

- 模型容器Docker health；
- 未携带key的`/v1/models`被拒绝；
- 带key请求报告预期模型ID；
- Spark B `/health/ready`；
- 双节点模式、节点身份、离线标志和非张量并行拓扑；
- 必需视觉组件在线；
- 可选reasoner或embedding的实际状态。

### 媒体路径核对

v1.1.0 的视频不是由 vLLM 直接读取整段文件。浏览器使用原生媒体解码进行有限时间采样，Spark B 重新解码和验证每个证据帧，再将少量代表帧发送到 Spark A。因此视觉容器继续保持 `--limit-mm-per-prompt` 中 `video:0`，并以有限 `image` 输入控制显存与延迟。

首次部署后至少完成以下两条复演：

1. 上传一组图片，确认原始 SHA-256、质量、指纹、知识、证据图和报告在重启后仍可读取；
2. 上传一段视频，确认原视频服务端 SHA-256、帧时间戳、质量/重复/代表帧、跨节点运行记录和 JSON/HTML 报告完整。

仓库内置的合成输入可用于可重复的命令行复演。在 Spark B 管理终端（或指向 Spark B `8088` 的 SSH 隧道端）确保本地 `.venv` 已按快速复现指南安装，然后对**已经启动的双机服务**运行：

```bash
.venv/bin/python scripts/media-smoke.py \
  --base-url http://127.0.0.1:8088 \
  --image demo_media/reference.png \
  --comparison-image demo_media/comparison.png \
  --video demo_media/synthetic_orbit.mp4 \
  --frames-dir demo_media/frames \
  --duration-ms 3000 \
  --max-frames 6
```

该命令会写入一条明确标记为 synthetic acceptance 的会话，应保存终端摘要、会话 ID、报告 hash 和跨节点模型运行记录。`make demo-media-smoke` 会另行启动 `127.0.0.1:18088` 的临时 deterministic 服务，只验证软件闭环，不能用作双 Spark 实机证据。

如果 Spark A 离线，第二条仍应完成媒体登记、确定性帧质量、指纹和报告，并显式标记模型观察降级。完整检查表见 `docs/MEDIA_ACCEPTANCE_V1.1.md`。

## 7. 停止、重启和状态

```bash
make status ROLE=spark-a
make status ROLE=spark-b

make stop ROLE=spark-b
make stop ROLE=spark-a

make restart ROLE=spark-a
make restart ROLE=spark-b
```

停止操作只移除明确命名的RelicScope容器，保留会话数据、模型缓存、镜像、知识和源文件。

## 8. 自启动

模板采用systemd，服务以安装用户身份运行，Docker容器本身使用`restart: unless-stopped`。在两台设备分别执行：

```bash
sudo ./deploy/install-systemd.sh --role spark-a --user <spark-admin-user>
sudo ./deploy/install-systemd.sh --role spark-b --user <spark-admin-user>
```

加`--now`可立即启动。默认只启用开机自启，便于先审核生成的`/etc/systemd/system/relicscope-*.service`。删除模板：

```bash
sudo ./deploy/install-systemd.sh --role spark-a --remove
```

systemd只负责启动容器，不把“服务已启动”当作“跨节点健康”。两台设备启动后仍运行`make health ROLE=...`。

## 9. 角色与故障边界

- Spark A失效：Spark B保持证据、知识和报告访问；视觉显示降级，不伪造模型输出。
- Spark B失效：停止新会话和报告写入；Spark A模型端点不对浏览器开放。
- 私网中断：Spark B readiness在双节点模式下失败；恢复链路后重新健康检查。
- 可选reasoner失效：使用确定性报告模板，不影响证据查看。
- 不允许两个Spark B实例同时写入同一套SQLite会话目录。
- 原始视频只进入 Spark B 受控持久目录；未经明确策略不得复制到 Spark A、模型缓存或报告包。
- 备份容量估算必须包含原始视频。改变媒体保留期或清除真实媒体属于机构数据治理操作，不由演示脚本自动执行。

真实网络、GPU、断网和性能验收仍需填写README中的现场复演记录。
