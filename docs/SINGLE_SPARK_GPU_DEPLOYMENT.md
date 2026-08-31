# 单台 DGX Spark 真实 GPU 部署与现场验收

> 适用版本：RelicScope Spark Suite 1.2 单机 GPU 配置
> 核对日期：2026-08-30
> 适用对象：项目负责人、现场演示人员、机构 IT 与运维人员

## 1. 最终效果

完成本指南后，一台 DGX Spark 会运行：

- 一个真实使用 GB10 GPU 的多模态模型服务；
- 一个使用同一 GB10、仅供容器内部访问的 Qwen3-VL-Embedding-2B 参考图向量服务；
- 一个 RelicScope 网页应用；
- 同一模型端点依次读取瓷器图片、服务端校验通过的原生视频，再依据结构化证据生成报告摘要；
- 本地知识、会话、图片、视频、证据和报告留在本机；
- 50 件 × 至少 5 视角真实参考库、负向案例与独立校准齐备后，提供库内实例候选、库外相关参考和开集拒绝；
- 联网阶段只负责下载并冻结软件与模型，正式运行可断开公网。

当前默认基线为：

```text
配置：qwen3-vl
权重源：Qwen/Qwen3-VL-30B-A3B-Instruct
服务名：qwen3_vl_30b_a3b
```

它是 RelicScope 当前的中文陶瓷图像基线。Qwen 官方模型卡提供 vLLM 调用方法并将许可标为 Apache-2.0；本项目仍需在目标 Spark 上自行完成性能、稳定性与安全验收，**不把它表述成 NVIDIA 官方 Spark 验证配置**。[Qwen3-VL 官方模型卡](https://huggingface.co/Qwen/Qwen3-VL-30B-A3B-Instruct)

同时预缓存、但不同时常驻的 A/B 候选为：

```text
配置：nemotron-omni
权重源：nvidia/Nemotron-3-Nano-Omni-30B-A3B-Reasoning-NVFP4
服务名：nemotron_3_nano_omni
```

该候选用于评测原生视频、长上下文和 NVIDIA 官方 Spark 路径。它按 English-first 候选管理：NVIDIA 模型卡将语言列为 English only，原始输出必须保留并进入专家 rubric，不能先润色成中文再评分。官方 playbook 使用 vLLM 0.20.0 并给出 Spark 启动与内存调优方法。[NVIDIA 模型卡](https://huggingface.co/nvidia/Nemotron-3-Nano-Omni-30B-A3B-Reasoning-NVFP4)；[NVIDIA DGX Spark Nemotron playbook](https://github.com/NVIDIA/dgx-spark-playbooks/blob/main/nvidia/nemotron/README.md)

两种配置共享同一套 vLLM 0.20.0 容器栈，但每次只加载一个模型。Qwen 始终是默认恢复目标；完成或中断 A/B 后，只有重新确认 served model 为 `qwen3_vl_30b_a3b` 才能继续演示。候选只有通过原生视频、语言、JSON、证据边界、延迟、内存与专家验收后才可进入另行批准的升级流程。

安全版本边界：vLLM 官方公告 [GHSA-94f4-hr76-p5j6](https://github.com/vllm-project/vllm/security/advisories/GHSA-94f4-hr76-p5j6) 将 `>=0.3.0,<0.22.0` 标为受影响范围，包含当前 Nemotron Spark 路径要求的 0.20.0。因此本版本只用于受控本机演示：模型端口不发布、运行网络必须为 `internal: true`、应用默认绑定 loopback，API key 只作纵深控制。待 Nemotron 对 0.22+ 完成兼容与质量复验后升级，不在部署现场盲目改版本。

```text
浏览器（127.0.0.1:8088）
            │
            ▼
RelicScope 应用与科学证据链
      │                 │                         │
      │ 图片观察        │ 报告摘要                │ 参考实例检索
      └────────┬────────┘                         ▼
               ▼                       私有 reference-embedding:8010
同一个私有 OpenAI 兼容端点               Qwen3-VL-Embedding-2B
http://vision:8000/v1                              │
               │                                  │
               └──────────────► DGX Spark GB10 GPU ◄┘
当前选中的 Qwen3-VL 或 Nemotron Omni
```

这里的“同一个端点”指观察与报告复用一个大模型进程，无需在 128 GB 统一内存里重复加载两份大模型。参考实例检索由独立 2B embedding sidecar 常驻，职责是生成稳定向量，不承担报告生成。三者在同一 Spark 共享 GPU，因此必须以现场统一内存、延迟和并发实测决定最终参数。A/B 切换发生在两次独立启动之间，不在同一时刻加载 Qwen 与 Nemotron 两套大模型。

## 2. 五条边界先说清楚

1. **这是科学观察和证据组织系统。** 图片模型可以描述可见特征、指出关注区域、表达局限并建议下一步检测；它不凭一张照片给出真伪、年代、价格或法律结论。
2. **两套配置的依据不同。** Nemotron 候选有 NVIDIA 单机 Spark playbook；Qwen3-VL 是 RelicScope 为中文陶瓷场景选择的工程基线。RelicScope 的现场验收证明本机确实调用 GPU、模型身份一致且闭环可运行；这不等于 NVIDIA 对 RelicScope 产品或鉴定结论作认证。
3. **语言能力必须分开验收。** Qwen 基线承担中文场景评测；NVIDIA 模型卡把 Nemotron Omni 的语言支持列为 `English only`，因此它的中文输出只能作为实验结果，不能预先承诺。
4. **端点身份不等于实际完成。** `/api/health` 中的 `endpoint_identity_ready=true` 只表示端点在线且 configured/served model 一致。图片、原生视频和报告是否真实完成，必须分别查看验收包中成功的 `model_runs`；健康响应不能替代 completion 证据。
5. **参考模型在线不等于参考库可信。** 只有 50 件受控真实样本、每件至少 5 个有效视角、至少 10 件经审核负向案例，以及按实物隔离的独立复拍/开集校准全部通过，才允许接受“同一实物候选”。缺失时显示 `CALIBRATION_REQUIRED` 或未就绪；负向命中不等于假货结论。

## 3. 一页式操作顺序

不要在活动当天第一次下载模型或升级系统。完整流程分为一次联网准备和每次离线启动。

| 阶段 | 操作 | 通过标志 |
|---|---|---|
| 设备准备 | 检查电源、系统、磁盘、Docker 与 GPU | NVIDIA CUDA 容器能看到 GB10 |
| 安装 | `make install ROLE=single INSTALL_ARGS="--generate-key"` | 生成 `.env`、目录和本机服务密钥 |
| 配置 | 核对本指南给出的单机参数 | 当前运行配置只有一个模型源和一个服务名 |
| 联网预缓存 | `make prefetch ROLE=single` | 生成 `runtime/prefetch-manifest-single.txt` |
| 锁定离线 | 关闭下载开关并阻断公网出站 | 运行期不挂载 Hugging Face token |
| 参考库准备 | `make reference-verify/import/build/evaluate/seal/status` | 真实数据、向量、独立评测和冻结校准全部绑定 |
| 启动 | `make start ROLE=single` | 应用、共享 VLM 与私有参考 embedding 均就绪 |
| 健康检查 | `make health ROLE=single` | `endpoint_identity_ready` 与单机运行模式正确；尚不代表完成过推理 |
| 现场验收 | `make accept-single-spark` | 三类 completion 与严格 DGX 身份共同写入验收 JSON |
| 顺序 A/B | `make ab-single-spark` | 同输入比较两模型并自动恢复 Qwen |

任一步失败都先停止，不要用“模拟结果”替代该步骤。

## 4. 设备与系统准备

### 4.1 现场硬件

- 使用 DGX Spark 随机附带的 240 W 电源适配器。NVIDIA 明确指出其他或低功率电源可能导致降速、无法启动或意外关机。
- 保持通风，避免把设备放在软布、密闭展柜或热源旁。
- 演示显示器、采集设备和外置硬盘提前完成插拔测试。
- 代码仓库、模型缓存和报告数据建议放在本机加密盘；不要把真实藏品数据放入公开仓库。

DGX Spark 使用 Grace Blackwell 架构、Arm CPU 和 128 GB 统一内存，官方硬件信息见 [NVIDIA DGX Spark Hardware Overview](https://docs.nvidia.com/dgx/dgx-spark/hardware.html)（页面更新于 2026-08-25）。

### 4.2 系统基线

在正式活动前的维护窗口，通过 DGX Dashboard 完成获准的系统更新并重启；更新后至少完整演练一次。NVIDIA 推荐用 Dashboard 管理系统、驱动与固件更新，并要求更新前保存工作、准备恢复方案和保持稳定供电。[NVIDIA OS and Component Update Guide](https://docs.nvidia.com/dgx/dgx-spark/os-and-component-update.html)

截至 2026-08-30，NVIDIA 发布说明中 Founders Edition 的当前基线是 DGX OS 7.5.0、驱动 580.159.03、CUDA 13.0.2；合作厂商的 GB10 设备更新节奏可能不同，因此验收记录应保存**本机实际版本**，不能只抄这组数字。[NVIDIA DGX Spark Release Notes](https://docs.nvidia.com/dgx/dgx-spark/release-notes.html)

记录实际环境：

```bash
uname -m
uname -r
nvidia-smi
docker version
docker compose version
nvidia-ctk --version
df -h
```

同时记录主机 device-tree 身份：

```bash
tr -d '\000' </proc/device-tree/model
```

通过条件：

- 架构显示 `aarch64` 或 `arm64`；
- `/proc/device-tree/model` 明确包含 `DGX Spark`；
- `nvidia-smi` 的 GPU 名称明确包含 `GB10`；
- Docker Compose 不低于仓库预检要求的 2.30；
- 如果同时预缓存 Qwen、Nemotron 候选和 2B 参考 embedding，建议在容器、三套权重、编译缓存、受控数据和一次回退版本之外仍预留至少 180 GB 空间；这也是当前仓库模板的最低预检线，仍应根据锁定 revision 与真实图片的实测体积追加余量。

只有 device-tree 主机型号、GB10 和 ARM64 三项同时满足，`dgx_spark_hardware_verified` 才可为真。仅能看到 NVIDIA GPU、仅识别到 GB10，或仅运行于 ARM64，都不能写成“DGX Spark 身份已核验”。

### 4.3 验证容器能真实访问 GPU

联网准备窗口先拉取 NVIDIA CUDA 验证镜像，再运行：

```bash
docker run --rm --gpus all \
  nvcr.io/nvidia/cuda:13.0.1-devel-ubuntu24.04 \
  nvidia-smi
```

容器内必须能看到 GPU、驱动和 CUDA 信息。DGX Spark 已预装并配置 NVIDIA Container Toolkit；`--gpus all` 是把 GPU 暴露给容器的关键开关。[NVIDIA Container Runtime for Docker](https://docs.nvidia.com/dgx/dgx-spark/nvidia-container-runtime-for-docker.html)（页面更新于 2026-08-25）

## 5. 模型许可与数据许可

下载前由项目负责人或机构管理员分别完成两套模型的确认：

- Qwen3-VL 模型卡仍标注 Apache-2.0，且仓库中的许可文件与机构用途一致；
- Nemotron 模型卡仍指向 `NVIDIA Open Model Agreement`；
- 机构用途、使用地区、再分发方式、出口管制和内部 AI 政策允许使用；
- 保存审核日期、审核人、模型 ID、下载 revision 和当时适用的许可文本；
- 不把模型权重、Hugging Face token、真实藏品图片或未获许可的数据提交到 GitHub；
- 上传到系统的图片、视频和参考资料已经取得采集、使用与保存权利。

[Qwen3-VL 官方模型卡](https://huggingface.co/Qwen/Qwen3-VL-30B-A3B-Instruct)标注 Apache-2.0，并提供 vLLM 服务示例。[NVIDIA Open Model Agreement](https://www.nvidia.com/en-us/agreements/enterprise-software/nvidia-open-model-agreement/)说明其覆盖作品可在遵守条款的前提下作商业使用，并规定了再分发、通知、贸易合规等义务。仓库中的 `ACCEPT_MODEL_TERMS=YES` 只是“操作人员已完成两套模型审核”的技术闸门，不代替合同或法律审查。本段不构成法律意见。

## 6. 一次性联网安装与预缓存

以下操作在 DGX Spark 上、项目仓库根目录执行。

### 6.1 初始化本机目录和服务密钥

```bash
make install ROLE=single INSTALL_ARGS="--generate-key"
```

该操作会创建 `.env`、受限目录和长度足够的本机服务密钥；不会下载模型，也不会启动服务。保留生成的密钥，不要粘贴到聊天、截图或演示文档。

### 6.2 核对单机配置

用文本编辑器打开 `.env`，确认至少包含以下值：

```dotenv
DEPLOYMENT_ROLE=single
SINGLE_NODE_ID=spark-single

APP_BIND_IP=127.0.0.1
RELICSCOPE_PORT=8088
MODEL_TIMEOUT_SECONDS=180
MODEL_MAX_CONCURRENCY=2
RELICSCOPE_MAX_VIDEO_BYTES=268435456
RELICSCOPE_MAX_NATIVE_VIDEO_BYTES=33554432
RELICSCOPE_MAX_NATIVE_VIDEO_DURATION_MS=15000

VLLM_BASE_IMAGE=vllm/vllm-openai:v0.20.0
VLLM_IMAGE=relicscope-multimodal-vllm:0.20.0-arm64
REFERENCE_EMBEDDING_IMAGE=relicscope-reference-embedding:1.0.0-arm64

MODEL_PROFILE=qwen3-vl
VISION_MODEL_SOURCE=Qwen/Qwen3-VL-30B-A3B-Instruct
VISION_MODEL=qwen3_vl_30b_a3b
AB_NEMOTRON_MODEL_SOURCE=nvidia/Nemotron-3-Nano-Omni-30B-A3B-Reasoning-NVFP4
AB_NEMOTRON_MODEL=nemotron_3_nano_omni
AB_NEMOTRON_MAX_MODEL_LEN=32768
AB_NEMOTRON_GPU_MEMORY_UTILIZATION=0.70
PREFETCH_AB_MODELS=1

RELICSCOPE_REFERENCE_LIBRARY_ENABLED=true
RELICSCOPE_REFERENCE_LIBRARY_MIN_ARTIFACTS=50
RELICSCOPE_REFERENCE_LIBRARY_MIN_VIEWS=5
RELICSCOPE_COUNTERFEIT_LIBRARY_MIN_RECORDS=10
REFERENCE_EMBEDDING_MODEL_SOURCE=Qwen/Qwen3-VL-Embedding-2B
REFERENCE_EMBEDDING_MODEL=qwen3_vl_embedding_2b
# 首次预缓存前留空；prefetch 写回并由启动脚本复核真实不可变 revision。
REFERENCE_EMBEDDING_MODEL_REVISION=
REFERENCE_EMBEDDING_DIMENSION=2048
REFERENCE_EMBEDDING_GPU_MEMORY_FRACTION=0.12
PREFETCH_REFERENCE_EMBEDDING=1

SINGLE_VISION_BASE_URL=http://vision:8000/v1
SINGLE_REASONER_BASE_URL=http://vision:8000/v1
VISION_GPU_MEMORY_UTILIZATION=0.75
VISION_MAX_MODEL_LEN=8192

REASONER_ENABLED=0
PREFETCH_REASONER=0
EMBEDDING_ENABLED=0
PREFETCH_EMBEDDING=0

MIN_FREE_GB=180
```

`VISION_MODEL_SOURCE` 是本次启动实际加载的权重；`VISION_MODEL` 是 OpenAI 兼容接口向应用公布的服务名。二者用途不同，不能互换。单机启动代码会自动让报告摘要复用 `http://vision:8000/v1` 和当前 `VISION_MODEL`，不会再启动第二份 reasoner 权重。

`RELICSCOPE_MAX_VIDEO_BYTES` 是登记/留存上限；原生模型请求还必须同时通过 32 MiB 与 15 秒门槛。15 秒来自当前目标配置的实测 Demo 设计，不是 Qwen 或 Nemotron 的理论能力声明；只有锁定 profile 后重新测量服务端解析、解码、延迟、统一内存和 JSON 稳定性，才可在变更评审中提高。

`VLLM_BASE_IMAGE` 是上游多架构 vLLM 0.20.0；`VLLM_IMAGE` 是预缓存阶段在本机一次性构建的 RelicScope 运行镜像。它把候选模型所需的可选多媒体依赖固定进镜像，正式运行时不会再执行 `pip install` 或联网下载。

`REFERENCE_EMBEDDING_MODEL_REVISION` 初次预缓存前在 `.env` 留空。预缓存从本机缓存解析 40/64 位 commit revision 并原子写回；预检和启动再同缓存复核。sidecar、客户端和向量索引还会比对模型 source、served name、revision、维度和 embedding 指令哈希。[Qwen3-VL-Embedding 官方模型卡](https://huggingface.co/Qwen/Qwen3-VL-Embedding-2B)

Qwen 基线以 `0.75 / 8192 / 2 并发` 作为本项目的保守起点，这属于 RelicScope 配置，不是 NVIDIA 官方性能结论。切换 Nemotron 候选时使用 `0.70 / 32768 / 2 并发`；其中 0.70 与 32768 来自 NVIDIA Spark playbook 的内存调优建议。[NVIDIA DGX Spark Nemotron playbook：Memory Tuning](https://github.com/NVIDIA/dgx-spark-playbooks/blob/main/nvidia/nemotron/README.md#run-nemotron-nano)

### 6.3 放入短期下载 token

`make prefetch` 需要 `HF_TOKEN_FILE` 指向的 Hugging Face token 文件。输入时不要回显：

```bash
mkdir -p secrets
chmod 700 secrets
read -rsp "Hugging Face token: " HF_TOKEN
printf '%s' "$HF_TOKEN" > secrets/hf_token
unset HF_TOKEN
chmod 600 secrets/hf_token
```

确认 `.env` 中：

```dotenv
HF_TOKEN_FILE=./secrets/hf_token
ALLOW_NETWORK_DOWNLOADS=YES
ACCEPT_MODEL_TERMS=YES
OFFLINE_RUNTIME=0
HF_HUB_OFFLINE=0
TRANSFORMERS_OFFLINE=0
```

### 6.4 下载并冻结

```bash
make prefetch ROLE=single
```

这一步应完成：

- 拉取固定版本的 ARM64 vLLM 0.20.0 基础容器，并构建固定的 RelicScope 多媒体运行镜像；
- 下载 Qwen 基线、Nemotron A/B 候选和 Qwen3-VL-Embedding-2B 到本地缓存；
- 构建 RelicScope ARM64 应用镜像；
- 构建固定依赖的私有参考 embedding 镜像；
- 记录容器镜像 ID、模型 ID 和实际下载 revision；
- 写出 `runtime/prefetch-manifest-single.txt`，其中不包含密钥。

NVIDIA 的单机 playbook 使用同一个上游多架构 vLLM 0.20.0 镜像，Docker 会在 Spark 上选择 Arm64 版本；Nemotron 模型卡也明确要求 vLLM 0.20.0。两套权重保存在磁盘上不等于同时占用内存；启动时只加载当前配置。权重体积也不等于总运行内存，模型激活、KV cache、图片/视频编码、应用和桌面还会共同占用统一内存。

预缓存成功后，保存以下材料到受控验收目录：

```bash
mkdir -p runtime/acceptance
chmod 700 runtime/acceptance
cp runtime/prefetch-manifest-single.txt runtime/acceptance/
git rev-parse HEAD > runtime/acceptance/application-commit.txt
nvidia-smi > runtime/acceptance/nvidia-smi-before.txt
```

不要在每次启动时重新下载，也不要在模型已验收后无记录地再次运行预缓存。

## 7. 切换为离线运行

预缓存结束后，把 `.env` 改回：

```dotenv
ALLOW_NETWORK_DOWNLOADS=NO
ACCEPT_MODEL_TERMS=NO
OFFLINE_RUNTIME=1
HF_HUB_OFFLINE=1
TRANSFORMERS_OFFLINE=1
HF_HUB_DISABLE_TELEMETRY=1
DO_NOT_TRACK=1
```

这里把 `ACCEPT_MODEL_TERMS` 改回 `NO` 是为了重新锁住下载入口，并不撤销已经完成的许可审核。运行容器不会挂载 Hugging Face token；token 只用于受控预缓存。机构还应在主机或上游网关阻断公网出站，或者在正式验收时物理断开公网。

环境变量可以阻止正常库路径自动下载，但它们不是网络防火墙。要证明离线运行，必须做到：

1. 预缓存完成后断开公网或启用出站阻断；
2. 重新启动整个单机配置；
3. 完成健康检查和端到端验收；
4. 保存模型 revision、镜像 ID、应用 commit、验收时间和验收 JSON；
5. 确认运行容器没有挂载 `secrets/hf_token`。

## 8. 正式启动

在第一次正式启动前，先按 [50 件参考库部署指南](REFERENCE_LIBRARY_DEPLOYMENT.md)完成：

```bash
# 尚未组织数据时先生成不可导入的空白采集工作区；已有批准 manifest 可跳过。
make reference-scaffold
make reference-verify
make reference-import
make reference-build
make reference-evaluate
make reference-seal
make reference-status
```

`reference-status` 只显示受控文件、大小和哈希是否齐备；最终完整性、模型身份与 frozen calibration 绑定仍由应用加载和 `make health` 复验。没有真实库或独立校准时，默认正式配置会保持 `CALIBRATION_REQUIRED`/未就绪，这是预期的失败关闭行为。

### 8.1 启动服务

```bash
make start ROLE=single
```

标准入口会执行预检，并启动：

- `vision`：唯一的当前模型 vLLM GPU 服务（默认 Qwen，A/B 时可切为 Nemotron）；
- `reference-embedding`：Qwen3-VL-Embedding-2B 私有 GPU sidecar，只服务目录检索，不发布主机端口；
- `app`：RelicScope 界面、科学证据链、知识检索和报告服务。

模型端点只在内部容器网络中使用；主机只暴露 `127.0.0.1:8088`。这既避免将无意配置的模型 API 暴露给现场网络，也保证图像观察与报告摘要使用同一受保护服务密钥。

首次启动可能需要加载权重和建立推理缓存。不要在日志仍显示加载过程中反复重启。

### 8.2 健康检查

```bash
make health ROLE=single
make status ROLE=single
```

通过时应确认：

- 运行模式为 `single-spark` / `SINGLE_SPARK_LOCAL_AI`；
- 物理节点数为 1；
- 模型端点在线；
- 默认配置下 configured model 与 served model 均为 `qwen3_vl_30b_a3b`；切换候选后两者均应为 `nemotron_3_nano_omni`；
- `model_identity_verified=true`；
- `endpoint_identity_ready=true`；
- 图像观察和报告模型显示为同一个端点、同一个模型，而不是两个权重实例。
- `reference-image-embedding` 为 `online`，模型 revision 与冻结向量索引一致；
- `/api/reference-library/summary` 的 `readiness` 为 `READY`，且记录 50 件真实参考、至少 10 件负向案例和 calibration hash。

`endpoint_identity_ready` 是端点级身份状态：它只证明端点在线、configured model 与 served model 一致。它不声称某个图片、视频或报告 completion 已发生，也不证明模型计算使用了已严格核验的 DGX Spark。

严格硬件身份另看 `dgx_spark_hardware_verified=true`：device-tree 主机型号必须含 `DGX Spark`、GPU 名称必须含 `GB10`、架构必须为 ARM64。真正的任务完成另看第 9 节验收 JSON 中每个角色的成功 `model_runs`。只看到网页打开、端点 identity ready 或模型列表返回，都不能标记为“真实 Spark 模型调用已完成”。

### 8.3 打开界面

在 Spark 本机浏览器打开：

```text
http://127.0.0.1:8088
```

从另一台电脑访问时，优先使用 SSH 隧道，不要把应用直接开放到公共 Wi-Fi：

```bash
ssh -L 8088:127.0.0.1:8088 <用户名>@<Spark 地址>
```

然后在这台电脑打开同一个 `http://127.0.0.1:8088`。

## 9. 真实 GPU 现场验收

### 9.1 自动闭环验收

保持服务运行，在一个终端执行：

```bash
make accept-single-spark
```

默认访问 `http://127.0.0.1:8088`，并把结果写入：

```text
runtime/acceptance/single-spark-live.json
```

需要改变地址或输出位置时使用：

```bash
make accept-single-spark \
  ACCEPT_ARGS="--base-url http://127.0.0.1:8088 --output runtime/acceptance/single-spark-live.json"
```

该验收必须实际完成三类模型调用：

1. 以图片为输入，得到结构化可见观察；
2. 以服务端解析通过、默认不超过 15 秒的 H.264 MP4 合成视频为输入，得到结构化时序观察；
3. 以证据与检索结果为输入，得到结构化报告摘要。

三条记录都应为 `status=SUCCESS`、`mode=local_vllm`，显示当前配置的同一个 served name，并分别保留任务角色、provider request ID、`finish_reason=stop`、模型 revision、应用 commit、开始/完成时间、输入/输出哈希、耗时与 token 使用记录。这些是 completion 证据；健康响应中的 `endpoint_identity_ready` 不能代替它们。任一调用进入本地规则回退、模型身份不一致、缺少不可变 revision/commit、严格 DGX 身份未通过或 GPU 未识别，都应使“真实 GPU 验收”失败。

### 9.2 顺序完成 Qwen / Nemotron A/B

保持 `.env` 为前述默认/候选配置，然后执行：

```bash
make ab-single-spark
```

该入口在成功路径中自动按以下顺序执行，不会让两模型同时常驻：

1. 启动 Qwen，完成图片、原生视频和报告基线，冻结 session、video 和输入 hash；
2. 停止 Qwen，启动 Nemotron，只对同一个原生视频运行候选观察；
3. 停止 Nemotron，恢复 Qwen，并在同一会话上生成最终报告；
4. 生成机器对比表，保留“需要专家评审”的升级闸门，不自动宣布胜者；
5. 计算全部结果文件的 SHA-256。

结果固定保存在：

```text
runtime/model-ab/
├── qwen3-vl-baseline.json
├── nemotron-omni-candidate.json
├── qwen3-vl-final-report.json
├── model-ab-scorecard.json
└── SHA256SUMS
```

完成后必须再运行 `make health ROLE=single`，确认当前模型已经恢复为 `qwen3_vl_30b_a3b`。`model-ab-scorecard.json` 的机器门槛只检查同输入、真实本地 vLLM、模型身份、时序观察、局限和结构化输出；主模型升级仍需文物/材料专家与模型工程负责人签字。

Nemotron 按 English-first 候选评分，保留原始语言输出。专家 rubric 至少覆盖：中文陶瓷术语或忠实翻译、跨视角与时间信息完整性、可见事实与推测分离、禁限结论与幻觉风险、候选区域及下一步建议的可操作性。不得先由另一模型润色候选输出再评分，也不得因为 NVIDIA 提供 Spark playbook 就预设其胜出。

如果命令被中断或以非零状态退出，不要假定已经恢复。保持 `.env` 的默认 `MODEL_PROFILE=qwen3-vl`，然后执行：

```bash
make stop ROLE=single
make start ROLE=single
make health ROLE=single
```

只有健康检查重新确认 `qwen3_vl_30b_a3b` 后，才能继续现场演示。

### 9.3 同时观察 GPU 活动

在第二个终端运行：

```bash
watch -n 1 nvidia-smi
```

然后重新执行验收或在界面上传一张获准使用的瓷器图片。请求期间应看到 vLLM 进程和 GPU 利用率变化；把验收前后输出保存到 `runtime/acceptance/`。

DGX Dashboard 也可以显示实时系统指标，远程访问可使用 NVIDIA Sync 或 SSH 隧道。[NVIDIA DGX Dashboard](https://docs.nvidia.com/dgx/dgx-spark/dgx-dashboard.html)

### 9.4 最终通过表

| 检查项 | 通过条件 | 证据 |
|---|---|---|
| 设备身份 | device-tree 主机型号含 DGX Spark、GPU 名称含 GB10、架构为 ARM64，三项同时通过 | 环境记录、`nvidia-smi`、health runtime snapshot |
| GPU 容器 | CUDA 验证容器成功 | 验证输出 |
| 软件冻结 | vLLM 镜像 ID、模型 revision、应用 commit 已记录 | prefetch manifest |
| 单模型架构 | 当前时刻只有一份 `vision` 模型服务 | `make status ROLE=single` |
| 端点身份 | `endpoint_identity_ready=true`，configured 与 served name 一致；不作为 completion 证据 | `make health ROLE=single` |
| 图片观察 | 真实模型调用成功并给出结构化输出 | live acceptance JSON |
| 原生视频 | 服务端解析的 H.264 MP4 在默认 15 秒/32 MiB 闸门内，由同一模型完成时序观察 | live acceptance JSON |
| 报告摘要 | 同一模型的第二角色调用成功 | live acceptance JSON |
| GPU 活动 | 请求时看到模型进程和利用率变化 | telemetry 记录 |
| 证据边界 | 输出含局限和下一步，不含真伪定论 | 报告与 UI |
| 离线重启 | 阻断公网后可重新启动并复验 | 断网验收记录 |
| 数据留存 | 没有模型 token 或真实数据进入 Git | Git 检查、目录检查 |

只有整表通过，演示材料才可以写“单台 DGX Spark 本地真实 GPU 推理已验证”。

## 10. 统一内存（UMA）怎么理解

DGX Spark 的 128 GB 是 CPU、GPU 和其他计算单元共享的 LPDDR5X 统一内存，不是“128 GB 独立显存 + 128 GB 系统内存”。因此：

- 不把 128 GB 宣传成独立 VRAM；
- 不用任何一个权重文件的磁盘体积推算全部运行占用；
- 同时观察 DGX Dashboard、`/proc/meminfo`、交换空间、应用延迟和是否发生 OOM；
- 每次正式版本都保存实际峰值，而不是引用估算值；
- 现场不要同时启动第二个大模型、训练任务或占用大量内存的开发环境。

在 iGPU/UMA 平台上，`nvidia-smi` 显示 `Memory-Usage: Not Supported` 是 NVIDIA 已知且预期的现象；它不等于 GPU 没有工作。NVIDIA 也提醒不要只依赖 `cudaMemGetInfo` 判断可分配内存，因为系统可能通过交换空间回收 DRAM。[NVIDIA DGX Spark Known Issues](https://docs.nvidia.com/dgx/dgx-spark/known-issues.html)（页面更新于 2026-08-25）

## 11. 两条视频路径与取舍

RelicScope 同时保留两条可追溯的视频观察路径：

1. **代表帧路径**：应用从视频中抽取带时间戳的帧，逐帧哈希、观察、聚合；稳定、可回看，适合作为当前报告主证据。
2. **原生视频路径**：应用从受控存储重新读取原始字节，验证原始 SHA-256，再由服务端解析 ISO-BMFF 结构；只有单一明确视频轨、H.264 编码、容器时长不超过默认 15 秒且文件不超过 32 MiB 的 MP4 才能作为一次多模态输入。客户端文件名、MIME 和声明时长均不能替代该检查。

默认 15 秒是当前 Qwen/Nemotron 实测 Demo 的资源与稳定性闸门，不是模型理论时长上限。提高前必须按 profile 重新测试服务端解析、解码、延迟、统一内存、JSON 严格遵循率与失败恢复；不合格、较长或其他容器的视频保留登记和代表帧路径，不强行送入原生模型。

Qwen3-VL 是当前中文陶瓷图像主基线，并在同一视频上提供对照；Nemotron Omni 是 English-first 原生视频候选。A/B 时必须对同一段视频保存原始文件 hash、代表帧结果、原生视频结果、模型 revision 和运行指标，不能只比较主观上“哪段文字更好看”。

自定义 `relicscope-multimodal-vllm:0.20.0-arm64` 镜像会在获准联网预缓存阶段把候选需要的可选多媒体依赖一次性构建进去。正式运行不再执行 `pip install`，也不会静默联网。

本指南的单机模式中，原视频由同一 Spark 的应用保存和校验，经 `internal: true` 容器网络传给同机模型端点，不发生 Spark 间传输。可选双机模式的边界不同：Spark B 保存权威原件；代表帧路径只发送受限派生帧；显式原生路径会把上述校验通过的受限 MP4 请求字节经私有模型平面发送到 Spark A。报告和普通日志不得嵌入原视频字节。

## 12. A/B 评测与主模型升级条件

Qwen 基线与 English-first Nemotron 候选至少比较以下维度：

- 中文陶瓷术语和可见事实的准确性；
- 对器形、纹饰、釉面、口沿、足底、裂隙和修复迹象的观察覆盖；
- 原生视频中的视角连续性和时间定位；
- JSON 严格遵循率与重试/回退率；
- 是否越过真伪、年代、归属和价格边界；
- 单次图片、完整视频和报告摘要的延迟；
- 统一内存压力、吞吐和长时间运行稳定性；
- 断网重启、模型身份和证据 hash 可复现性。

Qwen 的“中文基线”是产品工程选择，不是 NVIDIA 官方 Spark 认证。Nemotron 有 NVIDIA 官方 Spark 路径，但其模型卡将语言列为 `English only`。专家评分必须使用未润色的候选原始输出，并记录中文术语或忠实翻译、时序完整性、事实/推测分离、边界安全和建议可操作性。候选只有在预先定义的样本集和评分表上胜出、通过专家复核并重新完成离线验收后，才可以另行修改默认配置；在此之前，每次 A/B 都恢复 `MODEL_PROFILE=qwen3-vl`。

## 13. 故障处理

| 现象 | 判断 | 处理 |
|---|---|---|
| `make prefetch` 提示下载被锁 | 正常安全闸门 | 仅在获准联网窗口把下载和条款开关设为 `YES` |
| GPU 容器看不到设备 | NVIDIA runtime 或 Docker 配置异常 | 停止部署，先让官方 CUDA 验证命令通过 |
| 模型长时间加载 | 首次权重加载或缓存建立 | 查看状态和日志，保持供电，不要连续重启 |
| OOM / 系统明显换页 | 统一内存余量不足 | 关闭其他大任务；核对当前 profile 的已批准内存参数；仍失败则回退，不在现场随意调参 |
| `Memory-Usage: Not Supported` | DGX Spark iGPU 的已知显示方式 | 结合 GPU 进程、利用率、Dashboard 和端到端请求判断 |
| 模型 API 401/403 | 服务密钥不一致 | 检查同一 `SERVICE_API_KEY_FILE` 是否被 app 与 vision 使用；不要打印 key |
| `model_identity_verified=false` | 服务名或缓存版本不一致 | 检查当前 profile 的 `VISION_MODEL` 与 prefetch manifest，停止对外演示 |
| `endpoint_identity_ready=true` 但验收失败 | 端点身份正确，具体 completion 未通过 | 以失败的 `model_runs` 为准排查；不能把 health 当作任务完成证据 |
| `dgx_spark_hardware_verified=false` | device-tree、GB10 或 ARM64 至少一项未通过 | 保存三项原始记录，停止使用“DGX Spark 已核验”表述 |
| 原生视频被拒绝 | 非服务端确认的 H.264 MP4，或超过 15 秒/32 MiB 默认门槛 | 保留原件与拒绝记录，改走代表帧；提高门槛需专项实测和审批 |
| 图片成功、报告回退 | 第二角色调用失败或 JSON 不合格 | 保留失败证据，检查 live acceptance JSON；不把规则回退说成模型生成 |
| Qwen 中文结果不稳定 | 提示词、领域样本或模型能力不足 | 保留失败样本并进入中文专项评测，不能用润色掩盖原始输出 |
| Nemotron 中文结果不稳定 | 官方模型卡仅列 English | 按候选边界记录；不能因其有 Spark playbook 就推定中文合格 |
| 断网后无法启动 | 缺少镜像、权重或运行期仍在下载 | 恢复到联网准备阶段补齐，重新冻结并断网复验 |

## 14. 停止与回退

正常停止：

```bash
make stop ROLE=single
```

建议在通过验收后保存一个受限权限的配置副本：

```bash
cp .env runtime/acceptance/approved-single-spark.env
chmod 600 runtime/acceptance/approved-single-spark.env
```

升级失败时：

1. `make stop ROLE=single`；
2. 恢复上一个已批准的 `.env`、应用版本和 prefetch manifest；
3. `make start ROLE=single`；
4. 重新运行健康检查和完整验收；
5. 在新验收通过前，不对外声称已经升级。

停止和回退默认不删除模型缓存、会话、报告和原始媒体。不要在故障现场运行 `docker system prune`、清空 Hugging Face 缓存或删除 `runtime/data`。如果需要释放空间，先做备份、列出精确目标并在维护窗口单独审批。

如果 GPU 模型无法恢复，只能使用**事先安装并验收过**的确定性演示模式作为降级展示，并在界面和讲解中明确标注“模型不可用 / 非真实 GPU 推理”；不能用降级结果冒充本地大模型输出。

## 15. 一手依据与适用日期

1. [Qwen3-VL-30B-A3B-Instruct 官方模型卡](https://huggingface.co/Qwen/Qwen3-VL-30B-A3B-Instruct)，给出模型能力、Apache-2.0 标识和 vLLM 服务示例；它不是 NVIDIA Spark 官方验证材料。
2. [DGX Spark Nemotron playbook](https://github.com/NVIDIA/dgx-spark-playbooks/blob/main/nvidia/nemotron/README.md)，最近标注更新 2026-07-01；给出单台 Spark、vLLM 0.20.0、Arm64、模型服务名和内存调优方法。
3. [Nemotron-3-Nano-Omni-30B-A3B-Reasoning-NVFP4 model card](https://huggingface.co/nvidia/Nemotron-3-Nano-Omni-30B-A3B-Reasoning-NVFP4)，模型发布于 2026-04-28；给出模态、JSON、语言、硬件、NVFP4 与 vLLM 要求。
4. [NVIDIA Open Model Agreement](https://www.nvidia.com/en-us/agreements/enterprise-software/nvidia-open-model-agreement/)，发布于 2026-04-02；部署前仍应复核当前版本。
5. [DGX Spark Hardware Overview](https://docs.nvidia.com/dgx/dgx-spark/hardware.html)，更新于 2026-08-25；给出统一内存、电源和硬件规格。
6. [NVIDIA Container Runtime for Docker](https://docs.nvidia.com/dgx/dgx-spark/nvidia-container-runtime-for-docker.html)，更新于 2026-08-25；给出预装运行时和 GPU 容器验证方法。
7. [DGX Spark Known Issues](https://docs.nvidia.com/dgx/dgx-spark/known-issues.html)，更新于 2026-08-25；解释 UMA 与 `nvidia-smi` 内存显示边界。
8. [DGX Spark Release Notes](https://docs.nvidia.com/dgx/dgx-spark/release-notes.html)，更新于 2026-08-25；列出当前 Founders Edition 软件基线及厂商差异提醒。
9. [DGX Dashboard](https://docs.nvidia.com/dgx/dgx-spark/dgx-dashboard.html) 与 [OS and Component Update Guide](https://docs.nvidia.com/dgx/dgx-spark/os-and-component-update.html)，更新于 2026-08-25；用于监控与受控升级。

上游文档、模型权重、许可和容器版本都会更新。任何模型、镜像、DGX OS 或关键参数变化，都应视为新版本：重新预缓存、重新记录 revision，并重新执行离线端到端验收。
