# RelicScope Spark Suite v1.2.0｜单台 DGX Spark 真实多模态分析与可追溯报告

本仓库交付一个可真实运行、可审计、可离线演示的古陶瓷科学鉴证工作流原型。v1.2.0 的默认部署是一台 DGX Spark：Qwen3-VL 作为中文陶瓷图像主基线，并在同一视频上提供 A/B 对照；Nemotron 3 Nano Omni 作为原生视频候选，用冻结输入顺序 A/B。同一私有 GPU 模型端点完成可见观察和受约束报告摘要。主闭环为：瓷器图片 / 视频 → 原始哈希登记 → 质量门控与原生视频观察 → 本地知识引用 → Scientific Evidence Graph → Next Best Observation → JSON / HTML 结构化报告。

> **演示边界**：用户上传图片和视频属于真实输入；内置知识、检测值和仪器结果包含 `DEMO/SYNTHETIC` 数据。RGB 媒体不能产生 Raman、XRF、HSI、X-ray、CT 或 TL 测量值。输出用于验证产品架构与科学工作流，不构成文物真伪、年代、窑口、作者、价值、文物定级或法律鉴定结论。

应用发布版本为 `1.2.0`；报告中的 `P01-ACTIVE-SENSING-DEMO-V1 / 1.0.0` 是独立的演示检测协议版本。

## 从 GitHub 最快复现

仓库公开可见，可直接使用标准 Git 克隆，无需 GitHub 登录或 deploy key。公开可见不等于开源授权；本仓库当前未授予复制、修改或再分发许可，详见 `NOTICE.md`：

```bash
git clone https://github.com/dingyucanada/relicscope-spark-suite.git
cd relicscope-spark-suite
git checkout main

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
请先阅读 AGENTS.md、docs/GITHUB_SPARK_QUICKSTART.md 和单台 Spark 部署指南。
默认产品目标是一台 DGX Spark；若当前没有目标硬件，只运行 deterministic demo 并明确边界。
不要读取 secrets、.env、runtime 或真实器物数据；只使用仓库已有 make 入口。
```

Codex 只帮助执行和检查；RelicScope 本身不依赖 Codex 运行。完整克隆、校验、默认单机路径和可选双机扩展见 [GitHub / Spark 快速复现](docs/GITHUB_SPARK_QUICKSTART.md)。

## v1.2.0 核心 Demo

| 能力 | 观众看到什么 | 科学控制 |
|---|---|---|
| 图片 / 视频登记 | 本地选择媒体、原始 SHA-256、来源与区域 | 服务端验证类型、大小和原始字节；失败不进入证据 |
| 多帧观察 | 质量状态、近重复、代表帧、时间戳和覆盖缺口 | 有限抽帧；低质量帧保留记录但不形成有效观察 |
| 对象科学指纹 | 媒体、代表帧和算法版本组成的可复算身份 | 用于关联与复核，不称防伪证书或真伪证明 |
| 同区域复拍比较 | 基线与复拍的可见差异候选、可比性和标准复拍建议 | 必须同区域/同模态且通过质量门；不解释为劣化、修复或材料变化 |
| 本地 AI 与知识 | 可见事实、候选区域、相似参考和精确引用 | 模型失联可见降级；无充分参考时明确弃权 |
| 证据与建议 | 四态证据关系、Scientific Evidence Graph、下一步补拍/检测 | 保留冲突与不确定性；未接仪器只显示建议，不生成虚构测量 |
| 结构化报告 | 同一会话的 HTML 阅读版与 JSON 机器版 | 原始哈希、帧来源、模型、引用、审计和能力边界一并导出 |

单机实装首先阅读 [单台 DGX Spark 真实 GPU 部署与现场验收](docs/SINGLE_SPARK_GPU_DEPLOYMENT.md)。媒体原理见 [媒体科学观察架构](docs/MEDIA_ARCHITECTURE.md)，软件层历史验收见 [v1.1.0 媒体验收清单](docs/MEDIA_ACCEPTANCE_V1.1.md)。

## 运维入口

当前推荐路径是一台 DGX Spark、一个常驻多模态模型和一个应用入口。图片、原生视频和报告摘要复用同一私有模型端点；知识、证据、会话和报告均留在本机。双 Spark 独立服务拓扑仍保留为后续扩展路径。

```bash
make help

# 一台 Fresh Spark：初始化 → 联网预缓存 → 恢复离线 → 启动 → 实测。
make install ROLE=single INSTALL_ARGS="--generate-key"
make prefetch ROLE=single
make preflight ROLE=single
make start ROLE=single
make health ROLE=single
make accept-single-spark

# 冻结同一视频，顺序运行 Qwen3-VL 与 Nemotron Omni；成功后恢复 Qwen。
make ab-single-spark
```

`install` 只完成安全的项目初始化，不会擅自安装系统软件、修改网卡/防火墙、下载模型或启动服务。首次联网准备由 `make prefetch ROLE=single` 显式执行；运行阶段保持离线和 `--pull never`。详细步骤见 [单台 Spark 部署](docs/SINGLE_SPARK_GPU_DEPLOYMENT.md)、[运维手册](docs/OPERATIONS.md)、[离线目录与打包](docs/OFFLINE_LAYOUT.md)与[安全加固](docs/SECURITY_HARDENING.md)。

### 当前可运行边界与成熟框架

- `IMPLEMENTED / LOCAL ACCEPTED`：图片/视频登记、有限抽帧、多帧质量分析、同区域复拍比较、证据图和结构化报告；自动化、真实 JPEG/MP4、报告完整性、进程重启持久性与桌面/390 px 浏览器呈现均已通过。该层不调用 GPU 模型。
- `DEPLOYMENT_READY`：单台 Spark 共享 vLLM 端点、OpenAI-compatible 私网接口、Qwen 基线、Nemotron 顺序 A/B、FastAPI 控制面、确定性知识检索、主动检测回放、质量门控、证据图、审计链和报告均具备固定部署入口；只有目标 Spark 上的 `make accept-single-spark` 证据通过后才可标为 `HARDWARE_VERIFIED`。
- `OPTIONAL DUAL-SPARK`：双节点独立服务拓扑继续保留，但需要单独完成私网、认证、故障和性能验收，不能由单机结果推断。
- `ADAPTER_READY`：RamanSPy、PyMca、Spectral Python、Open3D；当前未随包安装，需在真实仪器、格式、校准和许可证验证后启用。
- `EVALUATION`：anomalib、OpenLIME、NeMo Retriever、NVIDIA NIM。
- `PHASE_2`：Holoscan/Sensor Bridge、DeepStream Service Maker Python、cuVS/Milvus。

没有发现一个许可证清晰、经过广泛验证、可直接负责“古陶瓷真伪鉴定”的成熟开源系统。异常检测热图只表示相对参考分布的偏离，不能证明真伪、年代、窑口或修复事实。VisuCeram 为 Apache-2.0 的卫生陶瓷工业缺陷数据集（3,265 张、7 类），与古陶瓷科学鉴证存在明显域差异，只列为 `EVALUATION`，不随本包分发，也不把其模型输出解释为古陶瓷结论。MVTec AD 仅用于符合其非商业许可的独立评测，不下载或打包进本交付；CE5-DET 因授权边界不清不集成；HyperSpy GPLv3 在专有产品使用前先进行法律审查。完整取舍见 [成熟框架复用计划](docs/FRAMEWORK_REUSE_PLAN.md)和[运行边界](docs/RUNTIME_BOUNDARY.md)。

## 1. 单 Spark 部署结论

默认使用一个共享 GPU 模型端点：

```text
受控浏览器 · 仅访问 127.0.0.1:8088
    │
    ▼
一台 DGX Spark
    ├─ RelicScope Gateway / Knowledge / Evidence / P01 / Report
    └─ 一个私有 vLLM 端点
          └─ 默认 Qwen3-VL-30B-A3B-Instruct
             候选 Nemotron-3-Nano-Omni（顺序 A/B，不同时加载）
```

应用与模型是同机容器服务；本配置未启用 Ray、NCCL 或张量并行。图像观察和报告摘要使用不同提示词与校验器，但共享同一个模型进程，避免在 128 GB 统一内存中重复加载权重。

| 逻辑角色 | 默认职责 | 当前选择 | 故障或资源不足时 |
|---|---|---|---|
| 中文图像主基线 / 视频对照 | 图片与代表帧观察、报告摘要；同视频 A/B 对照 | `Qwen/Qwen3-VL-30B-A3B-Instruct` | 验收失败即显示不可用，不把规则回退冒充 GPU 推理 |
| 原生视频候选 | 同一视频输入的跨视角/时序观察 | `nvidia/Nemotron-3-Nano-Omni-30B-A3B-Reasoning-NVFP4` | 保留 Qwen 为主模型；候选不自动晋级 |
| 科学证据引擎 | SQLite 会话、本地知识、P01、证据图、审计、结构化报告 | 确定性应用逻辑 | 模型离线时仍可查看既有证据与完整性记录 |

Qwen3-VL 是中文陶瓷场景的工程基线；Nemotron Omni 具备 NVIDIA 官方单台 Spark playbook，用于原生视频候选评测。A/B 必须保持相同输入哈希，机器门槛通过后仍需文物/材料专家和模型工程负责人复核，才可修改默认模型。

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
│   ├── spark-b-app.sh           # 双机网关或单机真实 GPU 入口
│   ├── single-spark-ab.sh       # 单机顺序 Qwen/Nemotron A/B
│   ├── single-spark-accept.sh   # 单机真实模型验收
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
├── compose.single.yml           # v1.2.0 默认：单台 Spark 共享 GPU 模型配置
├── compose.yml                  # 次级扩展：双机中的 Spark B 应用配置
├── .env.example                 # 无默认密钥的配置模板
└── run_local.sh                 # 无模型的本地确定性冒烟路径
```

持久数据位于 `RELICSCOPE_DATA_HOST_DIR`，模型位于 `HF_CACHE_DIR`，vLLM 编译缓存位于 `VLLM_CACHE_DIR`。容器重建不会删除这些目录。

## 3. 前置条件

默认产品路径只需要一台目标 DGX Spark，并满足：

- 当前受支持的 NVIDIA DGX OS；
- 主机架构为 `aarch64` / `arm64`，`/proc/device-tree/model` 明确识别 `DGX Spark`，且 `nvidia-smi` 的 GPU 名称包含 `GB10`；三项同时满足才算严格 DGX Spark 身份通过；
- Docker、Docker Compose 2.30 或更高版本与 NVIDIA Container Toolkit；
- `nvidia-smi` 正常，且容器可见 GPU；
- 权重、镜像、编译缓存和验收输出使用的本地磁盘有足够余量；当前模板在预缓存 Qwen 与 Nemotron 两套候选前要求至少 `MIN_FREE_GB=160` GiB，最终容量以所选 revision 和现场实测为准；
- 仅在批准的准备窗口访问容器源、Hugging Face 和 PyPI；运行和验收阶段可阻断公网出站。

`.env` 中的 `PYPI_INDEX_URL` 可在准备阶段切换为机构批准的 HTTPS PyPI 镜像；禁止在 URL 中嵌入账号或 token。正式运行不会访问该地址。

本地开发电脑运行 `run_local.sh` 需要 Python 3.11 或更高版本及可用的 `venv` 模块（Docker 应用固定使用 Python 3.12）。Ubuntu / DGX OS 若提示 `ensurepip` 或 `venv` 不可用，应由管理员安装与所选 Python 匹配的 `python3-venv` 包。脚本优先使用 `RELICSCOPE_PYTHON` 指定的解释器；未指定时依次查找 `python3.12`、`python3.11` 和满足版本要求的 `python3`。例如：

```bash
RELICSCOPE_PYTHON=/opt/homebrew/bin/python3.12 ./run_local.sh --install
```

DGX Spark 的 128 GB 统一系统内存由 CPU 与 GPU 共享，不能把全部容量当作模型可用显存。模型权重、KV cache、视频解码、应用、操作系统和文件缓存会共同占用它；因此单机默认只常驻一套大模型，A/B 通过停止、切换、验收、恢复来顺序执行。双机高速互连属于次级扩展，见第 7 节；单机默认路径不需要配置 Spark 间私网。

## 4. 配置与密钥

在目标 Spark 的项目目录初始化单机配置和随机服务密钥：

```bash
make install ROLE=single INSTALL_ARGS="--generate-key"
```

初始化脚本创建受控目录、设置当前非 root 用户的 `APP_UID/APP_GID`、把 `DEPLOYMENT_ROLE` 设为 `single`，并生成本环境专用密钥。不要把密钥写进 `.env`、源码、镜像、聊天记录或命令历史。预检拒绝少于 32 字节、权限过宽及带常见示例标记的服务密钥。

仓库自带 `.gitignore`，用于阻止新的 `.env`、`secrets/`、运行数据库、上传内容、模型缓存、虚拟环境和本地工作目录被误加入版本控制。它不会清除已经提交到历史中的凭据；一旦发生误提交，应立即轮换凭据，并按机构批准的仓库历史清理流程处置。

准备模型时，将已有的只读 Hugging Face token 文件放到：

```text
secrets/hf_token
```

并执行 `chmod 600 secrets/hf_token`。该 token 只挂载到一次性的预缓存容器；正常运行容器不接收 Hugging Face token。负责人还必须分别审核两套模型的实际模型卡、许可和地区/用途限制。

至少确认以下单机配置：

```dotenv
MODEL_PROFILE=qwen3-vl
VISION_MODEL_SOURCE=Qwen/Qwen3-VL-30B-A3B-Instruct
VISION_MODEL=qwen3_vl_30b_a3b
SINGLE_VISION_BASE_URL=http://vision:8000/v1

AB_NEMOTRON_MODEL_SOURCE=nvidia/Nemotron-3-Nano-Omni-30B-A3B-Reasoning-NVFP4
AB_NEMOTRON_MODEL=nemotron_3_nano_omni
PREFETCH_AB_MODELS=1

# 默认仅本机访问；远程演示使用 SSH 隧道。
APP_BIND_IP=127.0.0.1

# 上传、原生视频模型请求与代表帧的独立上限。
RELICSCOPE_MAX_UPLOAD_BYTES=8388608
RELICSCOPE_MAX_VIDEO_BYTES=268435456
RELICSCOPE_MAX_NATIVE_VIDEO_BYTES=33554432
RELICSCOPE_MAX_NATIVE_VIDEO_DURATION_MS=15000
RELICSCOPE_MAX_VIDEO_FRAMES=12
RELICSCOPE_MAX_FRAME_BYTES=2097152
```

`RELICSCOPE_MAX_VIDEO_BYTES` 控制登记和保存的原视频；原生模型路径还受更严格的大小与时长闸门约束。服务端会直接解析文件字节，只接受结构明确的单视频轨 H.264 MP4，并以容器内时长为准；`15000` 毫秒是当前实测 Demo 的默认安全门槛，不是模型能力上限，只有完成当前 profile 的解码、延迟、统一内存与结构化输出复验后才可提高。其他容器、超过原生大小/时长的输入继续使用服务端复核后的有限代表帧路径。当前私有 vLLM 端点限制每个提示中图片最多一份、视频最多一份且音频为零（`image:1, video:1, audio:0`）；RelicScope 标准调用每次只发送所需的一种视觉媒体，并关闭视频音频处理。

`.env` 由脚本逐项读取，不会作为 shell 程序执行。端点必须是 loopback、Docker 内部服务名、链路本地地址或 `ALLOWED_PRIVATE_CIDRS` 中的固定 IP；公网地址与带点公网 DNS 名会被拒绝。

### 网络控制

单机 Compose 只映射 `${APP_BIND_IP:-127.0.0.1}:${RELICSCOPE_PORT:-8088}`；vLLM 端点只存在于私有容器网络，不映射主机端口。远程管理工作站通过 SSH 隧道访问：

```bash
ssh -L 8088:127.0.0.1:8088 <spark-admin>@<spark-private-ip>
```

如机构确需局域网直连，必须另行配置 HTTPS/mTLS、反向代理、身份、速率限制和最小来源 allowlist。防火墙变更前先核对现有 SSH 与管理通道，避免锁定设备；任何运行模式都不得把应用或模型端点暴露到公网。

## 5. 首次预缓存与离线封存

下载行为不会由启动脚本自动触发。先审核并接受 Qwen 基线与 Nemotron 候选各自的许可和地域/用途限制，再在批准的联网窗口暂时设置：

```dotenv
ALLOW_NETWORK_DOWNLOADS=YES
ACCEPT_MODEL_TERMS=YES
OFFLINE_RUNTIME=0
HF_HUB_OFFLINE=0
TRANSFORMERS_OFFLINE=0
```

```bash
make prefetch ROLE=single
```

默认预缓存包含：

```dotenv
VLLM_BASE_IMAGE=vllm/vllm-openai:v0.20.0
VLLM_IMAGE=relicscope-multimodal-vllm:0.20.0-arm64
PREFETCH_AB_MODELS=1
```

脚本拉取固定上游 vLLM 基础镜像，构建 RelicScope ARM64 多模态运行镜像与应用镜像，下载已批准的两套权重，并在 `runtime/prefetch-manifest-single.txt` 记录容器 image ID、模型 ID 与实际缓存 revision。清单不包含 token。生产发布应进一步固定并签名实际通过验收的镜像 digest；Qwen 路径属于 RelicScope 工程配置，不描述为 NVIDIA 官方验证。

### 复现等级

- `main` 上的实际 commit 固定源码；`requirements.lock` / `requirements-dev.lock` 固定 Python 依赖，合成媒体由 SHA-256 清单固定，因此本地 deterministic Demo 可以进行功能与证据结构复演。
- 完整 Spark AI 路径还必须保存 `git rev-parse HEAD`、`runtime/prefetch-manifest-single.txt`、实际容器 image ID、模型 revision、GPU telemetry 与验收结果；完成组织级 digest/revision 冻结前，不宣称跨安装字节级一致。

准备完成后立即恢复：

```dotenv
ALLOW_NETWORK_DOWNLOADS=NO
ACCEPT_MODEL_TERMS=NO
OFFLINE_RUNTIME=1
HF_HUB_OFFLINE=1
TRANSFORMERS_OFFLINE=1
```

随后断开公网或启用站点出站阻断策略。环境变量能阻止 Hugging Face/Transformers 自动联网；网络层出口控制才是可证明的运行期隔离边界。

全部运行脚本都会拒绝 `OFFLINE_RUNTIME=0`，并使用 `--pull never` 或本地 `docker run`；缺失镜像或权重会在预检阶段明确失败，不会临时转为联网下载。

## 6. 默认单 Spark 运行、验收与 A/B

### 6.1 启动 Qwen3-VL 基线

离线标志已恢复、网络出口已锁定后运行：

```bash
make preflight ROLE=single
make start ROLE=single
make health ROLE=single
```

浏览器只访问 `http://127.0.0.1:8088`。健康响应应显示 `mode: single-spark`，并列出当前 `MODEL_PROFILE`、模型源、served name、知识版本、组件状态和 `DEMO/SYNTHETIC` 边界。`endpoint_identity_ready=true` 只表示端点在线且 configured model 与 served model 身份一致；它不证明任何图片、视频或报告请求已经完成，也不等于 GPU/DGX 身份通过。

严格硬件身份还必须同时满足 device-tree 主机型号含 `DGX Spark`、GPU 名称含 `GB10`、架构为 ARM64。真正的业务完成证据来自验收 JSON 中独立的 `model_runs`：对应角色必须为 `SUCCESS` / `local_vllm`，并保留 provider request ID、finish reason、模型 revision、应用 commit、输入/输出哈希和完成时间。

Qwen3-VL 是默认中文陶瓷图像主基线，并在冻结视频上形成 A/B 对照。图片观察、Qwen 视频对照与报告摘要使用同一端点，但由不同提示词、结构校验和证据角色约束。任何调用失败都必须显示为不可用或降级，不能由规则输出冒充真实 GPU 推理。

### 6.2 单机真实模型验收

```bash
make accept-single-spark
```

验收入口登记固定图片与服务端解析通过的 H.264 MP4，验证原始哈希，并依次证明图片观察、15 秒默认闸门内的原生视频观察和报告摘要均经过当前本地 vLLM 模型。默认结果写入：

```text
runtime/acceptance/single-spark-live.json
```

只有该记录、健康响应、预缓存 manifest、容器 image ID、模型 revision 和 GPU 证据来自目标 Spark，才可写“单台 DGX Spark 真实 GPU 推理已验证”。开发电脑的确定性 Demo 不满足这项声明。

### 6.3 冻结输入顺序 A/B

```bash
make ab-single-spark
```

包装器固定同一视频及其 SHA-256，按以下顺序执行：

1. `qwen3-vl`：Qwen3-VL 中文基线；
2. `nemotron-omni`：Nemotron 3 Nano Omni 原生视频候选；
3. 恢复 `qwen3-vl`，生成最终报告证据。

两套大模型不会同时常驻。结果位于 `runtime/model-ab/`：

```text
qwen3-vl-baseline.json
nemotron-omni-candidate.json
qwen3-vl-final-report.json
model-ab-scorecard.json
SHA256SUMS
```

Nemotron 按 English-first 候选评审：保留其原始输出，专家评分覆盖中文陶瓷术语或忠实翻译、跨视角/时间完整性、事实与推测分离、禁限结论与幻觉风险、候选区域及下一步建议的可操作性。机器 scorecard 只判断候选是否具备进入人工评审的资格；任何默认模型晋升都必须由文物/材料专家与模型工程负责人签字。无论候选结果如何，成功路径最后恢复 Qwen；演示前还要再次确认 served model 为 `qwen3_vl_30b_a3b`。

## 7. 次级扩展：双 Spark

双 Spark 仅用于在单机主闭环稳定后，把多模态计算与应用/证据服务拆到两个独立节点。它不是 v1.2.0 默认安装，不会合并两台设备的统一内存，也未启用 Ray、NCCL、张量并行或跨机模型切分。单机模式下，原视频由本机应用保存、校验并送往同机私有模型端点，没有 Spark 间媒体传输；双机模式下，Spark B 保存权威原件，代表帧路径只发送受限派生帧，而显式启用的原生路径会把通过服务端 H.264 MP4、大小与时长校验的整段请求字节经私有模型平面送往 Spark A。报告和普通日志均不得嵌入原视频字节。

只有用户明确选择该拓扑时，才按 [双 Spark 部署指南](docs/DUAL_SPARK_DEPLOYMENT.md)在 Spark A 与 Spark B 分别完成初始化、许可核对、预缓存、私网预检、离线锁定、启动和健康验证。单机验收不能证明跨节点网络、认证、故障切换或性能；双机验收也不能替代模型科学质量和专家复核。

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
| 09:00–09:30 | 冻结 commit、输入、Qwen/Nemotron revision 与许可选择 | 单机 `.env` 和输入 SHA-256 经双人核对 |
| 09:30–11:00 | 在批准联网窗口完成镜像构建和两套权重预缓存 | `prefetch-manifest-single.txt` 完整，磁盘余量合格 |
| 11:00–12:00 | 恢复离线闸门并启动 Qwen 基线 | 单机 preflight、health 与模型身份通过 |
| 13:00–14:00 | 跑通图片、原生视频、证据图与报告 | `single-spark-live.json` 证明三类真实模型调用 |
| 14:00–15:30 | 对冻结视频做顺序 A/B | 五份 A/B 证据文件和哈希清单完整，Qwen 已恢复 |
| 15:30–16:30 | 专家盲评与故障演练 | 保留基线或形成有签字的候选晋升建议；降级与恢复可见 |
| 16:30–17:15 | 断公网重启、连续演示和备份 | 无下载尝试、无静默回退、会话可恢复 |
| 17:15–18:00 | 最终彩排与交付 | 台词、样本、报告、验收证据和回滚路径全部确认 |

模型下载、镜像构建和首次权重加载时间受链路、缓存与所选 revision 影响。表中只给出执行窗口；现场必须记录实测，不能把计划值当作性能声明。

## 10. 演示台词

对投资人和收藏界观众，优先使用 [七分钟图片 / 视频主 Demo](docs/MEDIA_DEMO.md#7-七分钟演示脚本)。下面的五分钟 P01 回放适合作为第二幕，用于解释可见观察如何继续连接到风险受控的真实多模态检测。

### 五分钟 P01 科学检测回放

### 0:00–0:35｜科学边界与本地 AI

**操作**：打开控制台，指向顶部 `DEMO/SYNTHETIC`，刷新运行状态。

**讲解**：“今天展示的是可审计的科学鉴证工作流。系统不会输出真伪和价格。一台 DGX Spark 在本地完成多模态观察、知识引用、检测编排、证据和报告；当前由 Qwen3-VL 提供中文基线，候选模型只在冻结输入评测中顺序切换。”

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

- 应用容器使用 `.env` 中与持久目录所有者一致的非 root `APP_UID/APP_GID`（Dockerfile 独立构建默认值为 10001:10001；模板按常见 DGX 用户预填 1000:1000）、只读根文件系统、`cap_drop: ALL` 与 `no-new-privileges`。多模态运行镜像由固定的 `vllm/vllm-openai:v0.20.0` 基础镜像构建，不等同于 NVIDIA NGC 镜像；部署前应完成机构容器扫描、SBOM 与运行用户审核。
- 服务 key 通过只读 Docker secret 文件注入，未进入镜像或 Compose 环境。没有有效 key 时应用和模型容器默认拒绝启动或拒绝 `/v1` 请求。
- vLLM 的 `VLLM_API_KEY` 不能单独构成安全边界。vLLM 官方安全公告 [GHSA-94f4-hr76-p5j6](https://github.com/vllm-project/vllm/security/advisories/GHSA-94f4-hr76-p5j6) 将 `>=0.3.0,<0.22.0` 标为受影响范围，而 Nemotron 当前 NVIDIA Spark 路径要求 0.20.0。单机配置把 app 与 vision 放入 `internal: true` 的专用 Docker 网络、不发布模型端口、默认只把应用绑定到 loopback，并由现场验收核对网络 `Internal=true`；API key 仅作为纵深控制。待 Nemotron 在 vLLM 0.22 或更高版本完成兼容性与 A/B 复验后再升级运行时。
- Qwen 基线不启用 `--trust-remote-code`。只有 Nemotron 候选启用该能力，并同时固定模型、tokenizer 与 code revision；自定义代码缓存位于运行期内部网络中的本地可写缓存，不允许联网回退。
- vLLM 开发模式被关闭，当前日志级别为 `INFO`。日志可能包含请求元数据或错误上下文，彩排后必须抽查凭据与器物内容，并按机构政策限制访问和保留；不要假设 API key 会保护日志。
- 浏览器网关当前没有机构 SSO、用户级权限和 TLS 终止。只允许受控演示网段访问。生产部署应增加 HTTPS/mTLS、反向代理、速率限制、机构身份、审计主体和密钥轮换。
- `OFFLINE_RUNTIME=1` 使权重目录只读，并禁止启动时 pull/build；持久化 vLLM 编译缓存可写。运行期不挂载 HF token。
- 固定标签配合本地镜像 ID 和 `--pull never` 保持一天演示可复现。生产发布应把镜像升级为经过签名与扫描的 digest 固定引用。

## 13. 回滚

默认单 Spark 停止或回滚：

```bash
make stop ROLE=single
```

恢复已缓存的上一应用镜像时，先在 `.env` 设置：

```dotenv
PREVIOUS_APP_IMAGE=relicscope-ai-demo:<previous-fixed-tag>-arm64
```

```bash
./deploy/rollback.sh --role single --restore-app
```

脚本只停止并移除明确命名的 RelicScope 容器，不删除镜像、会话数据库、上传文件、知识文件或模型缓存，也不执行递归删除。顺序 A/B 异常中断后，先核对 `.env` 已恢复 `MODEL_PROFILE=qwen3-vl` 和 Qwen 模型身份，再启动并重新运行单机验收。迁移或升级持久层前，应在停止应用后另行制作 `runtime/data` 的一致性快照。

双机扩展的 `spark-a` / `spark-b` 回滚入口保留在 [双 Spark 部署指南](docs/DUAL_SPARK_DEPLOYMENT.md)，不属于默认恢复路径。

## 14. 已知限制与模型许可

1. 当前仪器数据为回放，阈值为演示协议假设；真实设备接入前必须由文保专家、设备工程师和安全负责人联合校准。
2. 内置知识库规模有限，部分条目标记为合成演示数据，未完成正式机构审核。
3. 模型输出存在随机性、领域外风险和视觉误读；模型观察不直接进入真实性结论。
4. 共享环境级 API key 适合一天私网演示，无法替代生产身份、授权和轮换体系。
5. 单机在 Qwen 与 Nemotron 之间顺序切换；没有把两套大模型同时常驻，也没有把两台 Spark 的统一内存合并。
6. Qwen 中文基线与 Nemotron 原生视频候选都必须在目标 GB10、固定模型 revision 和冻结输入上实测；机器 scorecard 不能替代专家复核。
7. 当前开发工作站的静态检查不能代替目标单台 Spark 的 GPU、统一内存、原生视频、断网、稳定性和性能验收；双机扩展还需要独立的跨节点验收。

### 当前模型许可与语言边界

Qwen3-VL 基线模型卡当前标注 Apache-2.0，并提供 vLLM 服务示例；Nemotron 3 Nano Omni 候选适用 NVIDIA Open Model Agreement。每次下载和发布仍需按实际地区、用途、再分发方式、机构政策及最新模型卡独立复核。`ACCEPT_MODEL_TERMS=YES` 只是下载闸门，不等于法律审查。

Nemotron 模型卡当前将语言支持列为 English only；因此中文输出质量属于 A/B 的必测项目，不能因其具备 NVIDIA Spark 官方运行路径就预设其胜出。Qwen3-VL 是中文工程基线，也不能表述为 NVIDIA 官方 Spark 验证模型。

## 15. 发布验收边界

下表区分当前工作站能够确证的静态交付事实与必须在目标 DGX Spark 上取得的运行证据。静态通过、确定性 Demo 通过或文档完备都不能替代实机证据。

| 验收域 | 当前工作站可确证 | 必须在默认单台 Spark 实机确证 |
|---|---|---|
| 代码与确定性闭环 | 依赖锁、合成媒体哈希、测试、图片/抽帧/证据/报告路径 | 不适用；该层不能证明 GPU 模型 |
| ARM64 容器与持久层 | 平台声明、固定基础版本、非 root 应用、持久卷和 health 路径的静态一致性 | ARM64 镜像真实构建与启动；容器重启后同一会话、上传和知识版本仍可读取 |
| 单机共享模型端点 | Qwen 默认 profile、served name、私有 Compose 网络和单一应用端口 | `endpoint_identity_ready=true`；它仅证明端点与模型身份，不证明请求完成 |
| 严格 DGX 身份 | device-tree、GB10 与 ARM64 三项检查逻辑 | 主机型号含 DGX Spark、GPU 名称含 GB10、架构为 ARM64，三项同时通过 |
| 图片/原生视频/报告 | 三类受约束调用、输入哈希和结果校验脚本 | `make accept-single-spark` 生成真实 `single-spark-live.json`；三类 `model_runs` 完成且无静默回退 |
| 顺序 A/B 与晋升 | Qwen/Nemotron profile、冻结输入和五份证据文件结构 | 同输入 hash、两模型真实运行、Qwen 恢复、专家盲评与书面晋升/保留决定 |
| 通信与离线 | Docker secret、无密钥拒绝、私网 URL 校验、`--pull never` / `--no-build` 与保留数据的回滚逻辑 | 只暴露应用端口；无密钥请求被拒绝；断公网重启与完整演示；出站与日志审计无凭据或业务外泄 |
| 次级双机扩展 | 独立服务配置和分角色脚本仍存在 | 仅在选择双机时另验私网、认证、节点身份、故障恢复与性能；不能沿用单机结论 |

## 16. 干净环境复演记录

以下表格用于默认单台 Spark 现场验收。计划值不得写成实测值；每次变更 commit、镜像、模型 revision、输入、运行参数或知识版本后重新填写。

| 项目 | 通过条件 | 实测 | 证据 |
|---|---|---|---|
| 单机 preflight | 通过且无缺失缓存/密钥/容量项 | 待填 | 终端记录 |
| Qwen 基线首次就绪 | 模型 ID、profile 与 revision 一致 | 待填 | health + manifest + 容器记录 |
| GPU / 统一内存基线 | 峰值、余量、功耗和温度已记录，无 OOM | 待填 | `nvidia-smi` / 系统 telemetry |
| 图片 + 原生视频 + 报告 | 三类真实模型调用通过，无规则冒充 | 待填 | `single-spark-live.json` |
| 冻结输入 A/B | 输入哈希一致，两候选记录完整 | 待填 | `runtime/model-ab/` + `SHA256SUMS` |
| Qwen 最终恢复 | 健康状态与 served model 回到基线 | 待填 | final report + health |
| 专家复核 | 有署名、日期、评分与晋升/保留决定 | 待填 | 受控评审记录 |
| 断公网重启与完整演示 | 无拉取或公网调用；端到端流程通过 | 待填 | 出站流量 + 启动与演示记录 |
| 容器重启后会话存在 | 同一 session、输入 hash 和报告可读取 | 待填 | 数据库/页面/报告证据 |

本文件不把待执行项目呈现为已完成实测。双 Spark 如被启用，应在独立记录中追加两台设备、私网和跨节点故障证据，不能改写或覆盖单机基线记录。

## 17. 官方资料

- [NVIDIA DGX Spark 硬件概览](https://docs.nvidia.com/dgx/dgx-spark/hardware.html)
- [NVIDIA DGX Spark ConnectX-7 网络与集群连接](https://docs.nvidia.com/dgx/dgx-spark/spark-clustering.html)
- [NVIDIA Sync Cluster Assistant](https://docs.nvidia.com/sync/latest/cluster-assistant.html)
- [NVIDIA DGX Spark vLLM 官方 playbook](https://github.com/NVIDIA/dgx-spark-playbooks/blob/main/nvidia/vllm/README.md)
- [NVIDIA NIM on Spark 官方 playbook](https://build.nvidia.com/spark/nim-llm)
- [vLLM OpenAI-compatible Server](https://docs.vllm.ai/en/stable/serving/online_serving/openai_compatible_server/)
- [vLLM 多模态输入](https://docs.vllm.ai/en/stable/features/multimodal_inputs/)
- [vLLM 安全与 API key 边界](https://docs.vllm.ai/en/stable/usage/security/)
- [Qwen3-VL-30B-A3B-Instruct 模型卡](https://huggingface.co/Qwen/Qwen3-VL-30B-A3B-Instruct)
- [NVIDIA Nemotron-3-Nano-Omni 模型卡](https://huggingface.co/nvidia/Nemotron-3-Nano-Omni-30B-A3B-Reasoning-NVFP4)
- [NVIDIA DGX Spark Nemotron playbook](https://github.com/NVIDIA/dgx-spark-playbooks/tree/main/nvidia/nemotron)
- [Qwen3-VL-Embedding-2B 模型卡](https://huggingface.co/Qwen/Qwen3-VL-Embedding-2B)
