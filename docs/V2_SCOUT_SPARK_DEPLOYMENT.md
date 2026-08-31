# RelicScope V2 单台 DGX Spark 部署

> 当前状态：部署代码已准备；尚未在客户 DGX Spark 上完成真机验收。
> V2 只启动 Scout 网关、持久任务、本地 VLM 和 HTTPS 入口。

Codex Desktop 可以帮助工程师完成检出、配置复核和排障，但不属于运行时。完成部署后，
即使 Codex Desktop 未启动，Scout、队列、模型和结果接口也必须独立工作。

## 1. 文件与边界

- Compose：`compose.v2.yml`
- 服务入口：`app.scout_main:app`
- 环境模板：`.env.v2.example`
- 数据：`runtime/v2-data/`，不进入 Git
- 模型缓存：`runtime/hf-cache/`，不进入 Git
- HTTPS：Caddy 内部 CA，仅用于受控 Demo；正式部署改用机构 CA
- vLLM：只在私有 Docker 网络出现，无主机端口

## 2. 唯一支持的安装顺序

```bash
git switch v2-scout-spark-platform
make v2-install
```

`v2-install` 不联网、不启动服务。它创建 `.env.v2`、非 root 容器身份、私有目录和
服务密钥。不要把密钥、`.env.v2`、模型、媒体或数据库加入 Git。

先核对模型许可与官方 Spark/vLLM 或 NIM 支持，把真实 immutable revision 写入
`.env.v2`，并把 `SCOUT_BIND_IP` 从安全的 loopback 默认值改成这台 Spark 已分配的
私有 LAN IPv4 地址。在经批准的联网准备窗口执行：

模板中的 [`Qwen/Qwen3-VL-30B-A3B-Instruct`](https://huggingface.co/Qwen/Qwen3-VL-30B-A3B-Instruct) 官方模型卡给出了 vLLM 调用方式，但
NVIDIA 当前 Spark vLLM 支持矩阵没有列出这一精确型号。它在 V2 中是待验收的中文
基线候选；必须通过下述目标机预检和多图 smoke 才能冻结为主模型。

```bash
make v2-prepare-online
```

该步骤拉取 ARM64 镜像（包括网关的 Python 基础镜像）、把 registry tag 自动改写为 digest、构建绑定当前 Git commit
的网关、缓存指定模型 revision，并建立轻量烟雾测试环境。它不会启动服务。完成后
关闭获批下载通道，并确认 `RELICSCOPE_OFFLINE_MODE=true`。V2 不自动选择“最新模型”。
主机烟雾测试环境固定安装 `httpx`、`Pillow` 与 `Pydantic`，用于 HTTPS 请求、按网关
同一算法重建净化图片，以及验证不可变任务 schema；它不安装或加载另一份大模型。

## 3. Spark 预检与启动

```bash
make v2-preflight
make v2-start
make v2-health
```

`v2-preflight` 检查 ARM64、GPU、Docker、源码 commit、容器 digest、网关镜像标签、
密钥权限、目录身份、私有监听地址、磁盘余量、离线标志、精确模型缓存和隔离网络。
`v2-health` 再通过本地 CA 访问真实 HTTPS 入口，并要求网关、队列、本地模型身份和
数据卷余量就绪。它是 readiness 检查；只有第 6 节的多图 `v2-smoke` 成功，才能证明
chat template、图像处理器、JSON 契约和本地模型 completion 真正工作。

## 4. HTTPS 与 Android 信任

Caddy 第一次启动后生成本地 CA：

```bash
make v2-export-ca
```

输出位于 `runtime/provisioning/scout-local-ca.crt`。受控 Demo 的 Android debug build 可以信任用户安装的 CA；正式 App 默认只信任系统/机构 CA。

在现场 DNS 或路由器 hosts 中把 `.env.v2` 的 `SCOUT_HOSTNAME` 指向主 Spark 的 10GbE/Wi‑Fi LAN 地址。不要向公网转发 8443，不要暴露 vLLM 8000。

## 5. 配对 Scout

在线准备步骤会建立设备注册与烟雾测试共用的轻量 Python 环境。创建一次性配置：

```bash
make v2-enroll \
  SCOUT_NAME="Scout 01" \
  SCOUT_SERVER_URL="https://scout.spark.local:8443" \
  SCOUT_DEVICE_ARGS="--output runtime/provisioning/scout-01.json"
```

配置文件权限为 `600`，包含只显示一次的设备令牌。导入 Scout 后删除或移入受控密码库。设备丢失时执行：

配发过程先以禁用状态写入设备记录，只有凭据文件完成刷新并同步到磁盘后才启用设备；
若中途失败，残留记录仍不可认证。

```bash
RELICSCOPE_DATA_DIR=runtime/v2-data \
  .venv-v2/bin/python scripts/scout-device.py disable <device-id>
```

## 6. 真实端到端验收

先在主 Spark 上用与网关同版本的图像库模拟 Android；这也确保净化 JPEG 与请求哈希
可以逐字节复算：

```bash
make v2-smoke SCOUT_SMOKE_ARGS="\
  --provisioning runtime/provisioning/scout-01.json \
  --ca-cert runtime/provisioning/scout-local-ca.crt \
  --capture FRONT=front.jpg \
  --capture BACK=back.jpg \
  --capture BASE=base.jpg"
```

每张图必须显式绑定视角，CLI 不根据位置或文件名猜测。标准烟雾测试默认只有
`SUCCEEDED` 才返回成功，并校验本地 vLLM 运行、模型身份、容器 digest、请求/输出
哈希和观察—视角绑定。`PARTIAL`、`NEEDS_RECAPTURE`、`MODEL_UNAVAILABLE` 等故障演练
只有显式传入对应的 `--allow-terminal-status` 时才可作为预期结果接受。

成功结果必须包含：

- 同一个 job ID 从 `QUEUED` 进入终态；
- 每张图片的服务端质量检查；
- 本地模型名称、revision、请求 ID、系统提示词哈希、实际请求载荷哈希和延迟；
- 每一次失败或成功模型调用的追加式运行证明，重试历史不得被覆盖；
- 模型调用先预留 attempt；进程中断后，已持久化成功输出不得触发第二次模型调用，
  未知结果的调用必须被明确记录并计入有界尝试次数；
- 观察所依据的 capture ID 与输出哈希；
- `reference_library_used=false`、`rag_used=false`、`agent_used=false`；
- `authenticity_state=NOT_ASSESSED`。

## 7. 第二台 Spark

第一阶段不要把网关数据库或唯一媒体复制到副机后宣称自动高可用。推荐：

1. 副机安装相同 DGX OS 和容器基线；
2. 运行候选模型、Nemotron 视频 A/B、批量评测或 NeMo PEFT；
3. 用内网 mTLS/OpenAI-compatible endpoint 接受受控任务；
4. 主机故障时按恢复手册人工启动经过验证的备用服务；
5. 只有模型容量确实需要时才执行 NVIDIA ConnectX‑7 + Ray/NCCL/vLLM 多节点流程。

## 8. 停止与回滚

```bash
make v2-stop
```

停止容器不会删除 `runtime/v2-data`、模型缓存或 Caddy CA。删除或清理客户媒体属于数据保留操作，必须取得明确授权并先备份。

一致性备份、校验、恢复与回滚步骤见
[`V2_BACKUP_RESTORE.md`](V2_BACKUP_RESTORE.md)。恢复流程不会把 `.env.v2`、服务密钥、
模型缓存或容器镜像放进数据归档。

V2 当前不执行自动保留期清理。正式上线前必须由客户批准保留期限、加密备份位置、
恢复演练和销毁流程；容量监控与这些决策没有完成前，只处理授权演示数据。
