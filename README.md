# RelicScope Spark Suite v1.1.0｜图片 / 视频科学观察与双 DGX Spark Demo

本仓库交付一个可真实运行、可审计、可离线演示的古陶瓷科学鉴证工作流原型。v1.1.0 的主入口是：瓷器图片 / 视频 → 原始哈希登记 → 质量门控、去重与代表帧 → 可见观察和对象科学指纹 → 本地知识引用 → Scientific Evidence Graph → Next Best Observation → JSON / HTML 结构化报告。既有 P01 主动检测回放继续展示从可见观察走向多模态科学检测的安全闭环。

> **演示边界**：用户上传图片和视频属于真实输入；内置知识、检测值和仪器结果包含 `DEMO/SYNTHETIC` 数据。RGB 媒体不能产生 Raman、XRF、HSI、X-ray、CT 或 TL 测量值。输出用于验证产品架构与科学工作流，不构成文物真伪、年代、窑口、作者、价值、文物定级或法律鉴定结论。

应用发布版本为 `1.1.0`；报告中的 `P01-ACTIVE-SENSING-DEMO-V1 / 1.0.0` 是独立的演示检测协议版本，不是应用版本残留。

## 从 GitHub 最快复现

仓库公开可见，可直接使用标准 Git 克隆，无需 GitHub 登录或 deploy key。公开可见不等于开源授权；本仓库当前未授予复制、修改或再分发许可，详见 `NOTICE.md`：

```bash
git clone https://github.com/dingyucanada/relicscope-spark-suite.git
cd relicscope-spark-suite
git checkout v1.1.0-r2

# 首次联网安装依赖并启动本地确定性 Demo
./scripts/reproduce-demo.sh --install
```

随后打开 `http://127.0.0.1:8088`。若服务运行在远程 Spark，可从管理工作站建立 SSH 隧道：

```bash
ssh -L 8088:127.0.0.1:8088 <spark-admin>@<spark-private-ip>
```

仓库随附完全合成且带校验清单的图片/视频输入。服务启动后，另开终端运行 `make demo-media-smoke` 可自动验证“图片 → 复拍比较 → 视频多帧 → 证据图 → 报告 → 完整性”；浏览器手工复演文件位于 `demo_media/`。

希望由 Codex 协助时，先按 [OpenAI 官方 Codex CLI 文档](https://learn.chatgpt.com/docs/codex/cli)安装并登录，在仓库根目录运行 `codex`，然后输入：

```text
请先阅读 AGENTS.md 和 docs/GITHUB_SPARK_QUICKSTART.md。只复现本地 deterministic demo，
不要读取 secrets、.env 或 runtime；执行仓库检查和首次安装，确认健康状态后告诉我浏览器地址。
```

Codex 只帮助执行和检查；RelicScope 本身不依赖 Codex 运行。完整克隆、校验、CI、单机和双 Spark 路径见 [GitHub / Spark 快速复现](docs/GITHUB_SPARK_QUICKSTART.md)。

## v1.1.0 核心 Demo

| 能力 | 观众看到什么 | 科学控制 |
|---|---|---|
| 图片 / 视频登记 | 本地选择媒体、原始 SHA-256、来源与区域 | 服务端验证类型、大小和原始字节；失败不进入证据 |
| 多帧观察 | 质量状态、近重复、代表帧、时间戳和覆盖缺口 | 有限抽帧；低质量帧保留记录但不形成有效观察 |
| 对象科学指纹 | 媒体、代表帧和算法版本组成的可复算身份 | 用于关联与复核，不称防伪证书或真伪证明 |
| 同区域复拍比较 | 基线与复拍的可见差异候选、可比性和标准复拍建议 | 必须同区域/同模态且通过质量门；不解释为劣化、修复或材料变化 |
| 本地 AI 与知识 | 可见事实、候选区域、相似参考和精确引用 | 模型失联可见降级；无充分参考时明确弃权 |
| 证据与建议 | 四态证据关系、Scientific Evidence Graph、下一步补拍/检测 | 保留冲突与不确定性；未接仪器只显示建议，不生成虚构测量 |
| 结构化报告 | 同一会话的 HTML 阅读版与 JSON 机器版 | 原始哈希、帧来源、模型、引用、审计和能力边界一并导出 |

完整原理见 [媒体科学观察架构](docs/MEDIA_ARCHITECTURE.md)，现场步骤与七分钟台词见 [媒体 Demo 操作手册](docs/MEDIA_DEMO.md)，软件验收结果见 [v1.1.0 媒体验收清单](docs/MEDIA_ACCEPTANCE_V1.1.md)。

## 运维入口

交付包为两台通过专用 **25/100 GbE 本地链路**连接的 DGX Spark 设计：Spark A 承担感知与模型推理，Spark B 承担知识、证据、会话和报告。两者是同一套本地系统的两个应用层节点，不是跨机统一显存，也不依赖 Ray/NCCL 才能运行。

```bash
make help

# Spark B 生成一次共享服务密钥；通过现有安全通道复制到 Spark A。
make install ROLE=spark-b INSTALL_ARGS="--generate-key"
make install ROLE=spark-a INSTALL_ARGS="--service-key /secure/service_api_key"

# Fresh Spark：核对 .env 后，在批准的联网准备窗口显式获取镜像与模型。
make prefetch ROLE=spark-a
make prefetch ROLE=spark-b

# 恢复离线标志后再预检与启动；完整顺序见第 5、6 节。
make preflight ROLE=spark-a
make start ROLE=spark-a
make health ROLE=spark-a

make preflight ROLE=spark-b
make start ROLE=spark-b
make health ROLE=spark-b
```

`install` 只完成安全的项目初始化，不会擅自安装系统软件、修改网卡/防火墙、下载模型或启动服务。首次联网准备仍由 `make prefetch ROLE=...` 显式执行；运行阶段保持离线和 `--pull never`。详细步骤见 [双 Spark 部署](docs/DUAL_SPARK_DEPLOYMENT.md)、[运维手册](docs/OPERATIONS.md)、[离线目录与打包](docs/OFFLINE_LAYOUT.md)、[安全加固](docs/SECURITY_HARDENING.md)、[正式部署路线](docs/PRODUCTION_ROADMAP.md)及 [Codex 开发运维入口](docs/CODEX_OPERATIONS.md)。

### 当前可运行边界与成熟框架

- `IMPLEMENTED / LOCAL ACCEPTED`：图片/视频登记、有限抽帧、多帧质量分析、同区域复拍比较、证据图和结构化报告；自动化、真实 JPEG/MP4、报告完整性、进程重启持久性与桌面/390 px 浏览器呈现均已通过。路演前仍需人工选择一次本地图片和视频；双 Spark、真实 VLM 与真实传感器另行上机验收。
- `DEPLOYMENT_READY`：NVIDIA vLLM 容器启动路径、OpenAI-compatible 私网接口、FastAPI 控制面、确定性知识检索、主动检测回放、质量门控、证据图、审计链和报告已经具备部署路径；目标 Spark 进程、GPU 和性能状态必须在 9.4 后才标为 `HARDWARE_VERIFIED`。
- `ADAPTER_READY`：RamanSPy、PyMca、Spectral Python、Open3D；当前未随包安装，需在真实仪器、格式、校准和许可证验证后启用。
- `EVALUATION`：anomalib、OpenLIME、NeMo Retriever、NVIDIA NIM。
- `PHASE_2`：Holoscan/Sensor Bridge、DeepStream Service Maker Python、cuVS/Milvus。

没有发现一个许可证清晰、经过广泛验证、可直接负责“古陶瓷真伪鉴定”的成熟开源系统。异常检测热图只表示相对参考分布的偏离，不能证明真伪、年代、窑口或修复事实。VisuCeram 为 Apache-2.0 的卫生陶瓷工业缺陷数据集（3,265 张、7 类），与古陶瓷科学鉴证存在明显域差异，只列为 `EVALUATION`，不随本包分发，也不把其模型输出解释为古陶瓷结论。MVTec AD 仅用于符合其非商业许可的独立评测，不下载或打包进本交付；CE5-DET 因授权边界不清不集成；HyperSpy GPLv3 在专有产品使用前先进行法律审查。完整取舍见 [成熟框架复用计划](docs/FRAMEWORK_REUSE_PLAN.md)和[运行边界](docs/RUNTIME_BOUNDARY.md)。

## 1. 一天版本的部署结论

默认使用两项独立服务：

```text
受控浏览器
    │ 仅访问 Spark B 网关
    ▼
Spark B · Gateway / Knowledge / Evidence / P01 / Report
    │ OpenAI-compatible API + Bearer key · 受控私网
    ▼
Spark A · Qwen2.5-VL-7B-Instruct-NVFP4
             └─ 可选 Qwen3-VL-Embedding-2B

Spark B 可选：Qwen3-14B-NVFP4 报告摘要服务
```

这是**应用层独立服务协同**。每台设备拥有自己的模型进程和显存；本配置未启用 Ray、NCCL、张量并行或跨机统一显存。NVIDIA 官方也提供跨两台 Spark 的 vLLM 张量并行方案，但该方案未用于本 Demo。

| 位置 | 默认职责 | 默认模型 | 故障或资源不足时 |
|---|---|---|---|
| Spark A | 图片 / 视频代表帧观察；可选嵌入 | `nvidia/Qwen2.5-VL-7B-Instruct-NVFP4` | 应用显示视觉服务降级，保留确定性质量、指纹和报告 |
| Spark B | 单一入口、SQLite 会话、本地知识、P01、证据、审计、报告 | 无必需生成模型 | 核心流程继续运行 |
| Spark B 可选 | 受约束报告摘要 | `nvidia/Qwen3-14B-NVFP4` | 使用确定性报告模板，不阻断证据查看与导出 |

选择 7B 视觉模型可以在一天内优先体现多模态特色；14B 推理模型保持可选，避免模型加载影响核心演示。应用通过 OpenAI-compatible 接口解耦模型，后续可切换到经验证的 vLLM、NVIDIA NIM 或其他本地兼容端点。

## 2. 交付结构

```text
.
├── app/                         # 应用、科学工作流与无外部 CDN 控制台
├── data/                        # 版本化演示知识清单
├── deploy/
│   ├── install.sh               # 幂等初始化，不自动改系统或下载
│   ├── network-preflight.sh     # 25/100 GbE 路由、速率与 MTU 检查
│   ├── service-control.sh       # 统一预检、启停、状态与健康入口
│   ├── healthcheck.sh           # 跨节点、认证、模型与拓扑健康检查
│   ├── backup.sh / restore.sh   # 一致性备份及可恢复还原
│   ├── package.sh               # 发布包；--offline含镜像/模型清单，不含第三方权重
│   ├── install-systemd.sh       # 角色化 systemd 模板安装器
│   ├── preflight.sh             # 架构、GPU、缓存、密钥、私网、就绪预检
│   ├── prefetch.sh              # 显式联网窗口中的镜像与模型预缓存
│   ├── spark-a-vision.sh        # Spark A 视觉服务
│   ├── spark-a-embedding.sh     # 可替换嵌入服务
│   ├── spark-b-app.sh           # 双机网关或单机降级入口
│   ├── spark-b-reasoner.sh      # 可选独立推理服务
│   └── rollback.sh              # 保留数据与缓存的回滚
├── docs/                        # 媒体架构/演示/验收、部署、离线、安全与框架边界
├── scripts/
│   ├── check-deployment.sh      # Shell 语法/严格模式/Compose 静态检查
│   └── media-smoke.py           # 真实 JPEG/MP4/抽帧/报告/完整性闭环验收
├── Makefile                     # 一键运维入口
├── Dockerfile                   # ARM64、多架构基础镜像、非 root 应用
├── .dockerignore                # 构建上下文 allowlist；排除密钥、会话和模型缓存
├── .gitignore                   # 排除 .env、secrets、runtime、work、venv 与缓存
├── compose.yml                  # 默认双机中的 Spark B
├── compose.single.yml           # 单台 Spark 降级配置
├── .env.example                 # 无默认密钥的配置模板
└── run_local.sh                 # 无模型的本地确定性冒烟路径
```

持久数据位于 `RELICSCOPE_DATA_HOST_DIR`，模型位于 `HF_CACHE_DIR`，vLLM 编译缓存位于 `VLLM_CACHE_DIR`。容器重建不会删除这些目录。

## 3. 前置条件

两台设备均应满足：

- 当前受支持的 NVIDIA DGX OS；
- ARM64 / Grace Blackwell；
- Docker、Docker Compose 2.30 或更高版本与 NVIDIA Container Toolkit；
- `nvidia-smi` 正常，且容器可见 GPU；
- 足够磁盘空间；默认预检要求至少 40 GiB，启用多个模型时应预留更多；
- 两台 Spark 之间有固定私网 IPv4；
- 仅在准备阶段具备 NGC、Hugging Face 和 PyPI 访问能力，演示运行阶段可断开公网。

`.env` 中的 `PYPI_INDEX_URL` 可在准备阶段切换为机构批准的 HTTPS PyPI 镜像；禁止在 URL 中嵌入账号或 token。正式运行不会访问该地址。

本地开发电脑运行 `run_local.sh` 需要 Python 3.11 或更高版本及可用的 `venv` 模块（Docker 应用固定使用 Python 3.12）。Ubuntu / DGX OS 若提示 `ensurepip` 或 `venv` 不可用，应由管理员安装与所选 Python 匹配的 `python3-venv` 包。脚本优先使用 `RELICSCOPE_PYTHON` 指定的解释器；未指定时依次查找 `python3.12`、`python3.11` 和满足版本要求的 `python3`。例如：

```bash
RELICSCOPE_PYTHON=/opt/homebrew/bin/python3.12 ./run_local.sh --install
```

DGX Spark 的 ConnectX-7 QSFP 端口支持高速以太网连接。当前交付按专用 25/100 GbE 私网设计，可使用 NVIDIA Sync Cluster Assistant 辅助发现与配置。RelicScope 的独立服务拓扑只需要稳定的私网 IP 通信，不依赖 Ray 或 NCCL。`deploy/network-preflight.sh` 只验证路由、地址、链路速率和 MTU，不会更改站点网络设置。

## 4. 配置与密钥

在两台 Spark 的项目目录各执行一次：

```bash
cp .env.example .env
install -d -m 700 secrets runtime/data runtime/hf-cache runtime/vllm-cache
openssl rand -base64 48 | tr -d '\n' > secrets/service_api_key
chmod 600 secrets/service_api_key
```

两台设备必须使用同一个本次环境专用 `service_api_key`。请通过现有安全通道复制密钥文件；不要把密钥写进 `.env`、源码、镜像、聊天记录或命令历史。预检拒绝少于 32 字节、权限过宽及带常见示例标记的服务密钥。

仓库自带 `.gitignore`，用于阻止新的 `.env`、`secrets/`、运行数据库、上传内容、模型缓存、虚拟环境和本地工作目录被误加入版本控制。它不会清除已经提交到历史中的凭据；一旦发生误提交，应立即轮换凭据，并按机构批准的仓库历史清理流程处置。

在承载应用持久数据的 Spark 上运行 `id -u` 和 `id -g`，把结果分别写入 `.env` 的 `APP_UID` 与 `APP_GID`。应用镜像以该非 root 身份构建，预检会核对镜像标签和 `runtime/data` 所有者，避免 SQLite 因绑定目录权限不一致而启动失败。

准备模型时，将已有的只读 Hugging Face token 文件放到：

```text
secrets/hf_token
```

并执行 `chmod 600 secrets/hf_token`。该 token 只挂载到一次性的预缓存容器，正常运行容器不接收 Hugging Face token。

按真实网络修改两台 `.env`，至少确认：

```dotenv
SPARK_A_IP=192.168.100.10
SPARK_A_BIND_IP=192.168.100.10
SPARK_B_IP=192.168.100.11
SPARK_B_BIND_IP=192.168.100.11
VISION_BASE_URL=http://192.168.100.10:8001/v1

# 浏览器仅在 Spark B 本机访问时保留 127.0.0.1；
# 需要受控局域网访问时改为 Spark B 的私网地址。
APP_BIND_IP=127.0.0.1

# 图片与视频资源上限。256 MiB 为原视频上限；默认最多分析 12 帧。
RELICSCOPE_MAX_UPLOAD_BYTES=8388608
RELICSCOPE_MAX_VIDEO_BYTES=268435456
RELICSCOPE_MAX_VIDEO_FRAMES=12
RELICSCOPE_MAX_FRAME_BYTES=2097152
```

`RELICSCOPE_MAX_UPLOAD_BYTES` 用于普通图片/请求；`RELICSCOPE_MAX_VIDEO_BYTES` 只放宽经专用视频登记流程验证的原视频；`RELICSCOPE_MAX_VIDEO_FRAMES` 限制一次视频分析的帧数；单帧有效上限取 `RELICSCOPE_MAX_FRAME_BYTES` 与图片上限中的较小值。反向代理、磁盘容量、备份窗口和机构保留策略也必须允许相应大小，不能只调整应用变量。

当前 vLLM 视觉服务仍配置为接收有限图片输入（`video:0`）：视频在浏览器进行有限时间采样，Spark B 重新验证帧，再将少量代表帧发送给 Spark A。它不表示 vLLM 直接读取整段视频，也避免无界视频占用模型显存。

`.env` 由脚本逐项读取，不会作为 shell 程序执行。端点必须是 loopback、Docker 内部服务名、链路本地地址或 `ALLOWED_PRIVATE_CIDRS` 中的固定 IP；公网地址与带点公网 DNS 名会被拒绝。

### 网络控制

部署脚本将视觉端口绑定到 `SPARK_A_BIND_IP`，应用端口绑定到 `APP_BIND_IP`。还应在主机或上游防火墙实施最小允许规则：

- Spark A `8001/tcp` 仅允许来自 Spark B 固定 IP；
- 启用独立嵌入端点时，`8003/tcp` 仅允许来自 Spark B；
- Spark B `8088/tcp` 仅允许演示终端所在受控网段；
- 可选 Compose reasoner 不映射主机端口；
- 禁止这些端口进入公网；运行阶段阻断公网出站。

若使用 UFW，可由机构管理员按以下形式建立规则，替换尖括号值后再执行：

```bash
sudo ufw allow from <SPARK_B_PRIVATE_IP> to <SPARK_A_PRIVATE_IP> port 8001 proto tcp
sudo ufw allow from <DEMO_CLIENT_CIDR> to <SPARK_B_PRIVATE_IP> port 8088 proto tcp
```

防火墙策略属于站点安全配置，启用前应核对现有 SSH 和管理通道，避免锁定设备。

## 5. 首次预缓存与离线封存

下载行为不会由启动脚本自动触发。先阅读并接受每个所选模型的许可证和地域限制，再在批准的联网窗口暂时设置：

```dotenv
ALLOW_NETWORK_DOWNLOADS=YES
ACCEPT_MODEL_TERMS=YES
OFFLINE_RUNTIME=0
HF_HUB_OFFLINE=0
TRANSFORMERS_OFFLINE=0
```

Spark A：

```bash
./deploy/prefetch.sh --role spark-a
```

Spark B：

```bash
./deploy/prefetch.sh --role spark-b
```

如需可选服务，预缓存前设置：

```dotenv
PREFETCH_EMBEDDING=1
PREFETCH_REASONER=1
```

`prefetch.sh` 是机构管理员在批准联网窗口中自行执行的可选准备工具，本次交付制作没有运行它、也没有下载或打包第三方权重。执行后，它会使用 NVIDIA 官方 Spark playbook 当前对应的固定 vLLM 标签 `nvcr.io/nvidia/vllm:26.05.post1-py3`，构建 ARM64 应用镜像，按已完成许可审查的清单获取所选权重，并在 `runtime/prefetch-manifest-<role>.txt` 记录镜像 ID、模型 ID 和实际缓存 revision。清单不包含 token。生产发布仍应把已验证标签进一步固定为镜像 digest。

### 复现等级

- Git 标签固定源码；`requirements.lock` / `requirements-dev.lock` 固定 Python 的直接和间接依赖，合成媒体由 SHA-256 清单固定，因此本地 deterministic Demo 可以进行功能与证据结构复演。
- 完整 Spark AI 路径在预缓存前仍以镜像 tag 和 Hugging Face 模型 ID 描述。每台设备必须保存 `git rev-parse HEAD`、`runtime/prefetch-manifest-<role>.txt`、实际容器 image ID 和模型 revision；完成组织级 digest/revision 冻结前，不宣称跨设备字节级一致。

准备完成后立即恢复：

```dotenv
ALLOW_NETWORK_DOWNLOADS=NO
ACCEPT_MODEL_TERMS=NO
OFFLINE_RUNTIME=1
HF_HUB_OFFLINE=1
TRANSFORMERS_OFFLINE=1
```

随后断开公网或应用站点出站阻断策略。环境变量能阻止 Hugging Face/Transformers 自动联网；网络层出口控制才是可证明的运行期隔离边界。

全部运行脚本都会拒绝 `OFFLINE_RUNTIME=0`，并使用 `--pull never` 或本地 `docker run`；缺失镜像或权重会在预检阶段明确失败，不会临时转为联网下载。

## 6. 双机部署

### 6.1 Spark A：视觉服务

```bash
./deploy/preflight.sh --role spark-a --require-vision
./deploy/spark-a-vision.sh
```

默认端点为 `http://SPARK_A_BIND_IP:8001/v1`，模型为 `nvidia/Qwen2.5-VL-7B-Instruct-NVFP4`。容器存活由 Docker 状态体现；`/health` 只有在模型完成加载后才通过，因此同时承担模型就绪探针。首次加载可能需要数分钟，脚本给予 15 分钟启动宽限。

可选嵌入服务：

```dotenv
EMBEDDING_ENABLED=1
EMBEDDING_BASE_URL=http://192.168.100.10:8003/v1
```

```bash
./deploy/spark-a-embedding.sh
```

嵌入端点是可替换加速项。其不可用时，本地知识服务使用确定性特征和全文检索降级；一天演示应先完成视觉主链路，再决定是否启用。

同时启用视觉和嵌入后，运行检查应显式要求两者：

```bash
./deploy/preflight.sh --role spark-a --require-vision --require-embedding --check-running
```

预检会分别核对两个容器的 Docker health、无密钥拒绝行为以及 `/v1/models` 返回的实际模型标识。应用的 `/api/health` 当前把嵌入状态汇总进 `local-knowledge` 组件，不单列一个 embedding 组件。

### 6.2 Spark B：应用单入口

确认 Spark B 的 `.env` 指向 Spark A 私网地址后执行：

```bash
./deploy/preflight.sh --role spark-b
./deploy/spark-b-app.sh
```

浏览器只访问：

```text
http://APP_BIND_IP:8088
```

如已预缓存 Qwen3 14B，并希望启用报告摘要：

```dotenv
REASONER_ENABLED=1
```

```bash
./deploy/spark-b-app.sh --with-reasoner
```

该 Compose profile 将 reasoner 与应用置于同一私有容器网络，不映射 reasoner 主机端口。`spark-b-reasoner.sh` 只用于把该逻辑角色改配为单独私网服务的场景。

### 6.3 运行检查

Spark A：

```bash
./deploy/preflight.sh --role spark-a --require-vision --check-running
```

该检查确认视觉容器为 healthy，验证无 API key 的 `/v1/models` 请求收到 `401/403`，再用只在进程内读取的服务 key 核对端点报告的实际模型 ID。`/health` 未受 vLLM API key 保护，不能承载业务数据或敏感详情。

Spark B：

```bash
./deploy/preflight.sh --role spark-b --check-running
curl -fsS http://127.0.0.1:8088/health/ready | python3 -m json.tool
curl -fsS http://127.0.0.1:8088/api/health | python3 -m json.tool
```

应用提供三个健康接口：`/health/live` 只验证进程存活；`/health/ready` 用于容器 readiness，并在 `dual-node` 模式缺少必需视觉服务时返回 HTTP 503；`/api/health` 返回诊断用的节点、组件、模型配置与拓扑载荷。Dockerfile 与两份 Compose 均以 `/health/ready` 作为应用容器健康检查，部署检查不得只探测始终用于诊断展示的 `/api/health`。`preflight --check-running` 还会核对期望模式、节点 ID、服务版本、必需组件状态、端点认证和模型标识。

健康响应应明确显示：

- `mode: dual-node`；
- `topology.type: APPLICATION_LEVEL_INDEPENDENT_SERVICES`；
- `topology.tensor_parallel: false`；
- 实际 gateway / compute 节点；
- 各组件的名称、节点、角色与状态；视觉和推理组件同时给出配置模型，网关与知识组件给出各自版本；
- 顶层 `checked_at` 最近检查时间；
- 本地知识版本；
- `DEMO/SYNTHETIC` 免责声明。

`status: degraded` 可以表示某项可选模型未启用；必须查看 `components`，确认一天演示所需的视觉服务为 `online`。模型进程存活但权重未加载时，vLLM `/health` 不会通过。

## 7. 角色交换与单机降级

逻辑角色由端点和节点 ID 配置决定。要把视觉角色迁移到另一台 Spark：

1. 在原节点执行 `./deploy/rollback.sh --role spark-a`；
2. 在目标节点准备同版本镜像、模型缓存和服务 key；
3. 把 `SPARK_A_BIND_IP`、`VISION_NODE_ID` 与 `RELICSCOPE_COMPUTE_NODE_ID` 改成新的实际映射；
4. 在目标节点运行 `spark-a-vision.sh`；
5. 更新网关的 `VISION_BASE_URL` 并复跑预检。

应用、知识和证据存储是唯一持久写入角色。迁移该角色前应停止旧实例并完整迁移 `RELICSCOPE_DATA_HOST_DIR`，不得让两个没有一致性机制的实例同时写入同一会话集。

只有一台 Spark 时，可先预缓存：

```bash
./deploy/prefetch.sh --role single
```

核心应用加确定性降级：

```bash
./deploy/spark-b-app.sh --single
```

单机加视觉模型：

```bash
./deploy/spark-b-app.sh --single --with-vision
```

单机同时启用视觉和 reasoner：

```bash
./deploy/spark-b-app.sh --single --with-vision --with-reasoner
```

最后一种配置需要现场验证显存、统一内存、延迟和并发，未列为一天演示默认路径。单机健康状态始终显示 `single-degraded`，不会显示成双机 AI。

## 8. 本机确定性冒烟

这条路径用于没有 Spark 的开发电脑，不下载模型、不调用公网模型：

```bash
./run_local.sh --install
```

以后运行：

```bash
./run_local.sh
```

打开 `http://127.0.0.1:8088`。模型组件会显式显示 disabled/degraded，图片/视频登记、有限抽帧、质量门控、对象指纹、知识检索、风险状态机、回放仪器、证据图、审计链和报告仍可运行。`--install` 是唯一会安装 Python 依赖的本地选项；普通启动不会隐式联网。

如需让本地应用调用已配置的私网模型端点：

```bash
./run_local.sh --configured-models
```

应用自身会再次拒绝公网模型 URL。

## 9. 一天执行时间表

| 时间 | 目标 | 退出条件 |
|---|---|---|
| 09:00–09:30 | 冻结模型、IP、演示样本和许可选择 | 两台 `.env` 经双人核对 |
| 09:30–11:00 | 并行完成 Spark 网络、密钥、镜像和权重预缓存 | 两台均生成 prefetch manifest |
| 11:00–12:00 | Spark A 视觉就绪；Spark B 应用就绪 | 双侧预检通过，无 key 请求被拒绝 |
| 13:00–14:00 | 跑通图片、视频、P01、报告和下载 | 两类媒体会话可复演，审计链有效 |
| 14:00–15:00 | 故障演练 | 视觉停机后可见降级；恢复后状态更新 |
| 15:00–16:00 | 连续三次 5 分钟演示 | 无遗留容器冲突，无静默部分写入 |
| 16:00–17:00 | 离线封存、记录版本与备份 | 公网断开后完整流程仍可运行 |
| 17:00–18:00 | 最终彩排与交付 | 台词、样本、报告、回滚路径全部确认 |

NVIDIA 的 vLLM Spark playbook把容器路径估计为约 30 分钟；模型下载时间受链路影响，首次启动还需加载权重。时间表为计划值，现场必须记录实测。

## 10. 演示台词

对投资人和收藏界观众，优先使用 [七分钟图片 / 视频主 Demo](docs/MEDIA_DEMO.md#7-七分钟演示脚本)。下面的五分钟 P01 回放适合作为第二幕，用于解释可见观察如何继续连接到风险受控的真实多模态检测。

### 五分钟 P01 科学检测回放

### 0:00–0:35｜科学边界与双节点

**操作**：打开控制台，指向顶部 `DEMO/SYNTHETIC`，刷新运行状态。

**讲解**：“今天展示的是可审计的科学鉴证工作流。系统不会输出真伪和价格。Spark A 负责多模态计算，Spark B 负责知识、检测编排、证据和报告；两台设备通过私网独立服务协作，当前没有使用张量并行。”

### 0:35–1:15｜器物建档与本地图像

**操作**：创建“疑似清代青花瓷”会话，上传本地 RGB 图像。

**讲解**：“原始文件先计算 SHA-256，再生成确定性质量指标和科学指纹。模型只能描述可见观察，无法把单张照片升级成鉴定结论。图像与查询都留在本地环境。”

### 1:15–1:55｜可引用知识

**操作**：执行默认本地检索，展开一条结果。

**讲解**：“每个结果都返回知识版本、来源标识、片段和适用范围。演示知识与正式知识空间隔离；没有来源的模型文本不会被当作事实依据。”

### 1:55–3:35｜主动科学检测

**操作**：点击“一键运行 P01 演示”，指向风险账本和时间线。

**讲解**：“系统先过滤不安全动作，再原子预留风险预算。Raman 回放因信噪比不足未通过质量门控，但实际光照负荷仍先结算。相同参数重试受到抑制，XRF 因预算约束被拦截，系统改选 HSI。HSI 通过门控后才进入命题更新，不确定度从 0.85 降至 0.48。”

### 3:35–4:25｜证据图与审计链

**操作**：展示证据图，点击“验证审计链”。

**讲解**：“图中区分原始输入、派生测量、模型观察、参考来源和回放证据。每次状态变化进入哈希链，可检查记录是否被修改。哈希完整性仍需在生产版结合机构签名与可信时间戳。”

### 4:25–5:00｜保守报告与收束

**操作**：生成报告，展示 JSON/HTML 下载与限制说明。

**讲解**：“最终报告保留知识版本、模型运行状态、检测实耗、质量失败、证据关系和包哈希。输出是声明一致性与下一步建议，真实结论仍需真实仪器、专家复核和机构程序。”

## 11. 真实仪器适配契约

一天版本使用 `ReplayInstrumentAdapter`，后续 Raman、HSI、XRF 或其他设备均实现同一适配器边界。真实 SDK、串口、文件落地或厂商服务被封装在适配器内部，P01 风险账本、质量门控、证据图和报告代码保持不变。

### 请求最小字段

| 字段 | 含义 |
|---|---|
| `request_id` / `idempotency_key` | 全局唯一；重复提交返回同一结果，不重复曝光 |
| `session_id`, `artifact_id` | 会话与器物身份 |
| `action_id`, `action_run_id` | 已规划动作与本次执行身份 |
| `modality` | Raman、HSI、XRF、UV 等 |
| `region` | `region_id`、坐标系、ROI 几何与配准版本 |
| `parameters` | 激光功率、积分时间、波段、帧数等；每项带单位 |
| `risk_reservation` | 预留通道、预计负荷、预算上限和账本版本 |
| `protocol` | 检测协议、阈值、质量门控与适配器版本 |
| `deadline` | 最迟开始/完成时间与超时策略 |

### 响应最小字段

| 字段 | 含义 |
|---|---|
| `request_id`, `action_run_id`, `status` | 与请求严格关联；状态为 completed / failed / unknown |
| `raw_result_ref`, `raw_sha256` | 原始文件在本地对象存储或文件区的引用及哈希；普通 API 不传大文件字节 |
| `started_at`, `acquired_at`, `completed_at` | UTC 时间戳与设备时钟来源 |
| `device` | 厂商、型号、序列号、固件、驱动和适配器版本 |
| `calibration` | 校准 ID、时间、参考物、有效期与校准文件哈希 |
| `actual_load` | 各风险通道实测值与单位 |
| `telemetry_valid` | 每个通道的遥测有效性及缺失原因 |
| `quality_metrics` | SNR、饱和、覆盖、配准、完整性、参考适用性等原始指标 |
| `finding`, `evidence_status` | 受限测量描述与支持/冲突/不确定状态 |
| `error` | 稳定错误码、可重试性与安全处置；不得含 token 或整段原始数据 |
| `demo_data` | 真实设备必须为 `false`；回放保持 `true` |

执行顺序必须固定：记录设备结果与遥测 → 将实际负荷结算到风险账本 → 运行质量门控 → 质量通过后更新命题。设备超时或遥测无效时，按预留值或协议保守值结算并锁定相应风险通道。质量失败仍保留原始结果、风险实耗和审计事件，且不得用失败结果降低不确定度。

生产适配器还应具备：进程隔离、设备互斥锁、幂等恢复、超时取消、校准过期拒绝、单位换算测试、文件完整性校验、设备时钟同步和厂商错误码映射。

## 12. 安全说明

- 应用容器使用 `.env` 中与持久目录所有者一致的非 root `APP_UID/APP_GID`（Dockerfile 独立构建默认值为 10001:10001；模板按常见 DGX 用户预填 1000:1000）、只读根文件系统、`cap_drop: ALL` 与 `no-new-privileges`。官方 vLLM NGC 镜像保持其受支持的运行用户模型；部署前应纳入机构容器基线审核。
- 服务 key 通过只读 Docker secret 文件注入，未进入镜像或 Compose 环境。没有有效 key 时应用和模型容器默认拒绝启动或拒绝 `/v1` 请求。
- vLLM 的 `VLLM_API_KEY` 只保护 `/v1`、`/v2` 和部分 `/inference` 路径；同一服务器仍存在未受该 key 保护的端点。官方建议同时使用网络隔离、防火墙与反向代理 allowlist。一天 Demo 至少实施固定私网 IP、源地址防火墙和无公网暴露。
- vLLM 开发模式被关闭，请求日志未启用，日志级别为 warning；仍应在彩排后执行凭据与器物内容抽查。
- 浏览器网关当前没有机构 SSO、用户级权限和 TLS 终止。只允许受控演示网段访问。生产部署应增加 HTTPS/mTLS、反向代理、速率限制、机构身份、审计主体和密钥轮换。
- `OFFLINE_RUNTIME=1` 使权重目录只读，并禁止启动时 pull/build；持久化 vLLM 编译缓存可写。运行期不挂载 HF token。
- 固定标签配合本地镜像 ID 和 `--pull never` 保持一天演示可复现。生产发布应把镜像升级为经过签名与扫描的 digest 固定引用。

## 13. 回滚

停止 Spark A 模型容器：

```bash
./deploy/rollback.sh --role spark-a
```

停止 Spark B 或单机 Compose：

```bash
./deploy/rollback.sh --role spark-b
./deploy/rollback.sh --role single
```

恢复已缓存的上一应用镜像：

```dotenv
PREVIOUS_APP_IMAGE=relicscope-ai-demo:<previous-fixed-tag>-arm64
```

```bash
./deploy/rollback.sh --role spark-b --restore-app
```

脚本只停止并移除明确命名的 RelicScope 容器，不删除镜像、会话数据库、上传文件、知识文件或模型缓存，也不执行递归删除。迁移或升级持久层前，应在停止应用后另行制作 `runtime/data` 的一致性快照。

## 14. 已知限制与模型许可

1. 当前仪器数据为回放，阈值为演示协议假设；真实设备接入前必须由文保专家、设备工程师和安全负责人联合校准。
2. 内置知识库规模有限，部分条目标记为合成演示数据，未完成正式机构审核。
3. 模型输出存在随机性、领域外风险和视觉误读；模型观察不直接进入真实性结论。
4. 共享环境级 API key 适合一天私网演示，无法替代生产身份、授权和轮换体系。
5. 本配置未实现跨机张量并行，也未把两台 Spark 内存合并。
6. 可选 embedding/reasoner 会增加预缓存、显存和启动时间，应先在目标镜像与模型版本上完成冒烟测试。
7. 当前交付环境无法代替两台真实 Spark 的网络、GPU、断网和性能验收；9.4 的双节点实测须在取得设备权限后执行。

### Qwen2.5-VL 欧盟地域提醒

默认视觉权重 `nvidia/Qwen2.5-VL-7B-Instruct-NVFP4` 的 NVIDIA 模型卡当前标注：**Deployment Geography: Global, except in European Union**，并适用 NVIDIA Open Model License 及模型卡列示的附加信息。部署地点或服务对象涉及欧盟时，不得沿用该默认权重；应先完成法务/合规审查并切换为获准模型，或清空 `VISION_BASE_URL` 使用可见的确定性降级路径。模型卡可能更新，每次发布前应重新核对。

`nvidia/Qwen3-14B-NVFP4` 模型卡当前标注 Apache 2.0、Deployment Geography: Global；可选嵌入模型 `Qwen/Qwen3-VL-Embedding-2B` 当前标注 Apache 2.0。任何模型仍需按实际使用地区、用途、再分发方式与机构政策独立复核。

## 15. 发布验收边界

下表区分当前工作站能够确证的静态交付事实与必须在 DGX Spark 上取得的运行证据。静态通过不代表对应 8.x 任务已完成实机验收。

| OpenSpec 项 | 当前工作站可确证 | 必须在 Spark 实机确证 |
|---|---|---|
| 8.1 ARM64 应用容器 | ARM64 平台声明、固定基础版本、非 root 用户、持久卷及 health 路径的静态一致性 | ARM64 构建与启动；容器重启后同一会话、上传和知识版本仍可读取 |
| 8.2 Spark A 计算服务 | 默认模型/端点配置、私网与密钥拒绝逻辑、模型 ID 核对脚本 | NGC ARM64 镜像与 GPU 透传；NVFP4 权重真实加载；视觉/嵌入 health、认证、节点和模型身份 |
| 8.3 Spark B 单一入口 | 只映射应用端口、reasoner 不映射主机端口、私有 Compose 网络与持久路径 | 浏览器经单入口完成跨节点流程；可选 reasoner 与持久存储重启验证 |
| 8.4 通信安全 | `.gitignore`/`.dockerignore`、Docker secret、无密钥默认拒绝和私网地址校验的配置与脚本 | 无密钥请求实际返回 401/403；非允许源被防火墙拒绝；日志抽查和运行期出站抓取不含凭据/业务外泄 |
| 8.5 角色/单机降级 | 环境驱动的节点角色、`dual-node`/`single-degraded` 配置与单机 Compose 静态解析 | 实际角色迁移；单机资源占用、模型取舍、页面模式和降级标签 |
| 8.6 预缓存/离线/回滚 | 脚本语法、固定标签、启动期 `--pull never`/`--no-build`、缺失依赖报错与保留数据的回滚逻辑 | 官方镜像/权重预缓存；断公网启动与完整演示；缺缓存失败；回滚后会话恢复和出站流量证据 |

## 16. 干净环境复演记录

以下表格用于两台 Spark 现场验收。计划值不得写成实测值；每次变更镜像、模型、网络或知识版本后重新填写。

| 项目 | 计划/上限 | 实测 | 证据 |
|---|---:|---:|---|
| Spark A preflight | ≤ 2 分钟 | 待填 | 终端记录 |
| Spark A 视觉首次就绪 | ≤ 15 分钟 | 待填 | Docker health + 模型 ID |
| Spark B app 启动 | ≤ 3 分钟 | 待填 | `/health/ready` + `/api/health` |
| 首次页面可交互 | ≤ 30 秒 | 待填 | 浏览器时间戳 |
| 完整 P01 + 报告 | ≤ 5 分钟 | 待填 | session ID + report hash |
| 视觉故障降级 | ≤ 60 秒 | 待填 | health + UI 截图 |
| 恢复视觉服务 | ≤ 15 分钟 | 待填 | health 时间线 |
| 断公网完整演示 | 通过 | 待填 | 出站流量记录 |
| 容器重启后会话存在 | 通过 | 待填 | 同一 session ID |

本部署交付已在当前工作站完成脚本语法、Compose 静态解析与 OpenSpec 静态验证。两台真实 DGX Spark 上的模型下载、GPU 启动、私网防火墙、断网流量审计和端到端性能数据仍须按上表现场填写；本文件不把待执行项目呈现为已完成实测。

## 17. 官方资料

- [NVIDIA DGX Spark 硬件概览](https://docs.nvidia.com/dgx/dgx-spark/hardware.html)
- [NVIDIA DGX Spark ConnectX-7 网络与集群连接](https://docs.nvidia.com/dgx/dgx-spark/spark-clustering.html)
- [NVIDIA Sync Cluster Assistant](https://docs.nvidia.com/sync/latest/cluster-assistant.html)
- [NVIDIA DGX Spark vLLM 官方 playbook](https://github.com/NVIDIA/dgx-spark-playbooks/blob/main/nvidia/vllm/README.md)
- [NVIDIA NIM on Spark 官方 playbook](https://build.nvidia.com/spark/nim-llm)
- [vLLM OpenAI-compatible Server](https://docs.vllm.ai/en/stable/serving/online_serving/openai_compatible_server/)
- [vLLM 多模态输入](https://docs.vllm.ai/en/stable/features/multimodal_inputs/)
- [vLLM 安全与 API key 边界](https://docs.vllm.ai/en/stable/usage/security/)
- [NVIDIA Qwen2.5-VL-7B-Instruct-NVFP4 模型卡](https://huggingface.co/nvidia/Qwen2.5-VL-7B-Instruct-NVFP4)
- [NVIDIA Qwen3-14B-NVFP4 模型卡](https://huggingface.co/nvidia/Qwen3-14B-NVFP4)
- [Qwen3-VL-Embedding-2B 模型卡](https://huggingface.co/Qwen/Qwen3-VL-Embedding-2B)
