# RelicScope V2：Scout + DGX Spark 本地 AI 基础设施

> 决策日期：2026-09-01
> 适用对象：客户负责人、AI 工程师、Scout 产品团队、Spark 运维人员
> 状态：代码框架已实现；目标 DGX Spark 与目标 Android 设备尚待现场验收

## 1. 直接结论

V2 的首要交付不是一个信息繁多的网站，也不是由 AI 工程师代替客户建设艺术品数据库。V2 应交付一套可运行、可复现、可维护的本地 AI 基础设施：

```text
Scout 安卓手持设备
拍摄引导 · 端侧质检 · 断网保存 · 安全上传
                    │
                    ▼
主 DGX Spark
设备入口 · 本地存储 · 持久任务 · 本地多模态模型 · 结构化结果
                    │
                    ▼ 可选
第二台 DGX Spark
候选模型 · 视频模型 · 微调/评测 · 批处理 · 人工切换备用
```

一个标准任务必须能在一台 Spark 上完整运行。第二台 Spark 的价值是隔离工作负载、开展模型实验、增加处理能力和提供备用选择，不应成为系统启动的前提。

## 2. 当前产品边界

| 层级 | V2 必须完成 | 后续扩展 | 当前明确不承担 |
|---|---|---|---|
| Scout | CameraX 拍摄、视角标签、端侧质检、离线队列、上传与结果查看 | 轻量陶瓷/视角模型、RAW、色卡、MDM | 在手机上给出真伪或断代 |
| Spark 网关 | 设备认证、上传校验、内容哈希、任务状态、失败恢复、结果 API | 机构用户与审批流、多租户 | 对公网开放模型端口 |
| 本地 AI | 多模态可见特征观察、模型运行证明、补拍建议 | 受控 RAG、参考库、视频 Agent、仪器适配器 | 用生成模型代替科学检测 |
| 数据 | 保存本次任务所需媒体、元数据、结果和审计记录 | 客户授权的数据包与索引 | 由本项目团队先建设完整艺术品数据库 |
| 双 Spark | 主机完整运行；副机做独立模型、评测、微调或备用 | ConnectX‑7 分布式推理 | 宣传两台机器自动合并成一台 256GB GPU |
| 结论 | 图像质量、可见观察、限制和下一步 | 经专家批准的领域任务 | 真伪、年代、窑口、作者、价值和法律结论 |

现有 50 件参考库、假货交叉验证、Scientific Evidence Graph、RAG 和仪器能力仍有长期价值，但它们是可插拔能力。没有参考库时，Scout→Spark 的基础流程仍必须正常工作。

## 3. DGX Spark 的真实意义

### 3.1 适合本项目的能力

NVIDIA 官方规格显示，DGX Spark 使用 GB10 Grace Blackwell、20 核 Arm CPU、128GB CPU/GPU 一致性统一内存、4TB NVMe、10GbE、Wi‑Fi 7，以及 ConnectX‑7 200Gb/s 网络；FP4 稀疏理论峰值最高 1 PFLOP。其优势不是便携相机控制，而是以较小体积提供本地模型运行、数据不出现场、容器化复现和模型开发环境。[NVIDIA DGX Spark 产品规格](https://www.nvidia.com/en-us/products/workstations/dgx-spark/) · [Hardware Overview](https://docs.nvidia.com/dgx/dgx-spark/hardware.html)

这使 Spark 适合承担：

- 本地 VLM/LLM 推理服务；
- 图片、短视频和结果的本地存储；
- OpenAI 兼容的内网 API；
- 模型 A/B、量化、LoRA/PEFT 和评测；
- 受控知识包、向量检索和后续 Agent 工具；
- 离线或弱网环境下的 AI appliance。

### 3.2 不应误解的地方

128GB 是 CPU、GPU、操作系统、模型权重、KV Cache 和其他服务共同使用的统一内存，不是 128GB 独占显存。官方的“最高 200B 推理”“双机更大模型”属于容量上限口径，实际上下文、并发、首字延迟和吞吐都需要在目标工作负载上测量。FP4 理论峰值也不能直接换算为五张图片生成报告所需时间。

Spark 为 ARM64。每个 Python wheel、Docker 镜像和 NVIDIA 组件都必须确认 ARM64/GB10 支持；不能默认复用 x86 GPU 服务器的镜像。[DGX Spark Porting Guide](https://docs.nvidia.com/dgx/dgx-spark-porting-guide/dgx-spark-porting-guide.pdf)

### 3.3 两台 Spark 的正确使用方式

官方支持通过 QSFP 将 Spark 连接为 200GbE 集群，并可使用 vLLM、Ray、NCCL 或 TensorRT‑LLM 运行分布式工作负载；这不是 NVLink 共享内存，也不是自动把两台主机合并。[ConnectX‑7 Networking](https://docs.nvidia.com/dgx/dgx-spark/spark-clustering.html) · [NVIDIA Connect Two Sparks](https://github.com/NVIDIA/dgx-spark-playbooks/tree/main/nvidia/connect-two-sparks)

V2 推荐按以下顺序使用两台机器：

1. **先验证单机完整闭环。** 主机独立完成 Scout 上传、任务、模型和结果。
2. **副机独立承载工作负载。** 运行视频候选模型、A/B、批处理或微调，不干扰现场服务。
3. **再验证故障与切换。** 相同模型和容器可在副机人工启动，明确恢复时间和数据恢复步骤。
4. **最后评估分布式模型。** 仅当目标模型确实无法在单机满足容量或性能门槛时，才增加 QSFP、Ray/NCCL 和 tensor parallel。

## 4. V2 功能架构

### 4.1 Scout 端

当前 Scout 端只做低风险、快速、可解释的工作：

- 五视角拍摄引导：正面、背面、侧面、口沿/内部、底足；
- 取景框引导，以及清晰度、曝光、高光/暗部剪切和分辨率提示；
- 为任务和每张照片生成稳定 UUID、时间、视角和 SHA‑256，服务器再次计算并核对；
- 用 Room 保存待传任务，用 WorkManager 在断网或 App 重启后重试；
- Wi‑Fi 与 USB 网络共享使用同一 HTTPS API；
- 展示任务状态、可见观察、失败原因和补拍建议。

端侧轻量模型可以在获得目标手机和样本后增加，例如“陶瓷/非陶瓷”“视角分类”“主体是否完整入框”。它不应承担正式鉴定。

### 4.2 Spark 网关

V2 使用独立的 `app.scout_main`，只公开 `/api/v2/scout`，旧版演示接口不会随手机入口一起暴露。网关已经实现：

- 每台设备独立、可撤销的凭据；服务器只保存 scrypt 加盐哈希；
- 配发文件以 `0600` 权限原子创建；数据库中的新设备在凭据落盘前保持禁用，避免
  断电留下已启用但无人取得的令牌；
- multipart 图片传输，不再用 base64 JSON 增加约三分之一体积；
- MIME、大小、图像解码、像素数和重复媒体检查；
- 原始文件按 SHA‑256 存在 Spark 本地，模型只接收移除 EXIF 后的重编码图片；
- 内容寻址文件采用无覆盖原子发布；媒体发布、SQLite 引用创建与拒绝清理共用独占锁，
  因而两个并发任务上传相同字节时，失败任务不能删除成功任务已经引用的文件；
- `client_job_id + 不可变输入哈希` 幂等；
- SQLite 持久任务、阶段事件、启动恢复和设备数据隔离；
- 模型冷启动不消费任务；暂态 completion 失败在同一 job 上进入有界指数退避；
- 低质量图像直接要求重拍；冷启动/离线时保留原任务，模型端点在线但 completion
  连续达到重试上限后才返回 `MODEL_UNAVAILABLE`；
- 修复模型配置后可对 `MODEL_UNAVAILABLE` 原 job 发起受控原地重试，无需重拍或更换
  `client_job_id`；媒体完整性失败不允许绕过；
- 将全部合格视角放入一次多图推理请求，同时把每条观察绑定到原始 capture ID；
- 结果绑定模型来源、revision、请求 ID、系统提示词哈希、实际请求载荷哈希、
  容器 digest、输入/输出哈希、节点和延迟；实际请求哈希覆盖有序多视角内容，
  但不保存图像副本；推理前再次核对原始文件哈希并按采集 ordinal 固定顺序。
- 每次失败和成功的模型调用都追加为独立的运行证明；同一任务重试不会覆盖此前
  尝试。每次外部调用先原子预留 attempt，完成后把经过结构校验的输出与证明持久化；
  进程若在结果装配前中断，重启会复用已记录的成功输出。结果未知的已发起调用仍计入
  上限，失败证明与 `RETRY_WAIT` 在同一事务写入，重启不会绕过退避或重复消费预算。
- 按数据卷最低余量和每设备未完成任务数拒绝过量写入，不自动删除客户数据。

### 4.3 确定性编排优先于 Agent

当前流程只有四个确定阶段：

```text
INGEST_VALIDATION
  → QUALITY_CHECK
  → MULTIMODAL_OBSERVATION
  → RESULT_ASSEMBLY
```

这里不需要自主 Agent。设备认证、质量门控、模型选择、超时、失败重试和结论边界都应由代码决定，才能做运维验收。

V2 手机入口目前只接受 `standard` 分析模式。这样可避免客户端选择低质量捷径，所有
正式演示均经过同一套多视角质量门控、模型调用和结果边界。

Agent 适合后续作为受限工具调用层，例如：根据观察建议补拍、检索客户批准的资料、选择仪器适配器。Agent 只能调用允许的工具，不能自行改变结论状态。

Codex Desktop 可作为工程师在 Spark 上检出代码、审查配置和协助排障的开发工具；它
不参与现场任务处理，也不是服务开机、Scout 上传或本地推理的运行依赖。正式运行只
依赖已冻结的源码提交、容器、模型、配置和系统服务。

## 5. 模型与微调决策

### 5.1 先把模型服务跑稳

网关采用 OpenAI 兼容接口。目标模型若有经过 GB10 验证的 NVIDIA NIM profile，可优先评估 NIM；需要快速切换开源模型、A/B 或自定义量化时，可使用 NVIDIA 官方 Spark vLLM 路径。NIM 的支持是逐模型、逐版本和逐 profile 的，不能宣传所有 NIM 均可直接在 Spark 运行。[NIM Support Matrix](https://docs.nvidia.com/nim/large-language-models/latest/reference/support-matrix.html) · [vLLM on DGX Spark](https://github.com/NVIDIA/dgx-spark-playbooks/tree/main/nvidia/vllm)

建议形成三个槽位：

| 槽位 | 当前用途 | 晋级条件 |
|---|---|---|
| 中文图像基线 | Qwen 系 VLM；输出受限 JSON | 在目标 Spark 上通过延迟、内存、中文观察和边界测试 |
| NVIDIA 候选 | Nemotron 3 Nano Omni；图像/视频影子 A/B | 解决官方 English-only 边界，并在中文任务上胜出 |
| 正式模型 | 基线或定制模型 | 冻结评测集上有可重复收益并通过专家评审 |

Nemotron 3 Nano Omni 官方模型卡支持图像、视频、音频和文本，也有 Spark 配方，但语言支持标注为 English only。因此它适合视频/多模态候选，不宜未经验证直接取代中文主模型。[Nemotron 3 Nano Omni 模型卡](https://huggingface.co/nvidia/Nemotron-3-Nano-Omni-30B-A3B-Reasoning-NVFP4)

### 5.2 何时微调

先完成 prompt + schema + evaluation baseline。只有同时满足以下条件才进入 LoRA/PEFT：

1. 已冻结需要改进的任务，例如器型字段抽取、视角识别或专业术语输出；
2. 已有获得授权、定义一致、专家复核的训练与独立测试示例；
3. 基线模型的错误能够归因于能力缺口，而非拍摄质量或提示设计；
4. 微调后在独立集上有可量化收益，且拒答和结论边界没有下降。

副 Spark 最适合承担这一工作。NVIDIA 官方 NeMo 路径支持在 Spark 上进行 PEFT/SFT；仍需针对具体模型、精度和数据量实测。[Fine-tune with NeMo](https://build.nvidia.com/spark/nemo-fine-tune)

## 6. 当前 Demo 应展示什么

Demo 是一条真实运行链，而不是网页讲解：

1. Android Scout 选择“正面”，拍摄一张清晰图片；端侧显示质量通过。
2. 依次完成背面、侧面、口沿/内部与底足；关闭网络后点击提交，任务保留在手机。
3. 恢复 Wi‑Fi 或 USB 网络共享，WorkManager 自动上传。
4. Spark 接口返回同一任务 ID，任务进入持久队列。
5. 本地 GPU 模型处理通过质检的图片；运维页显示真实模型身份和服务状态。
6. Scout 收到结构化可见观察、每个观察对应的视角、限制和补拍建议。
7. 重复提交不重复推理；撤销设备后请求立即返回 401。
8. 关闭第二台 Spark，主机仍完成任务；再在副机运行候选模型做独立 A/B。

参考库、假货库、RAG 或 Evidence Graph 可以作为“扩展能力已留接口”说明，不应占据当前演示主流程。

当前代码不会自动删除原始照片或任务。客户的数据保留期限、批准人、备份介质与
销毁流程必须在正式上线前确定；在此之前仅使用授权的演示媒体，并由操作员监控
`runtime/v2-data` 容量。

## 7. 分阶段计划

### 阶段 A：当前代码基线

- 独立 V2 API、持久任务和本地模型适配；
- Android 参考客户端；
- 单机 ARM64 Compose、HTTPS、设备配对和烟雾测试；
- 非 root、只读容器、私有模型网络、固定镜像/模型版本与一致性备份恢复；
- 范围、部署与硬件验收文档。

### 阶段 B：第一台 Spark 现场验收

- 冻结 DGX OS、驱动、CUDA、容器和模型 revision；
- 测量 1/3/5 张图的 TTFT、总时长、峰值统一内存和失败率；
- 连续运行 10 个任务，验证无 OOM、无服务重启；
- 测试手机断网、杀进程、恢复上传和设备撤销；
- 冻结客户可接受的模型和参数。

### 阶段 C：第二台 Spark

- 先部署独立的候选/视频模型或微调环境；
- 验证主服务与实验负载互不干扰；
- 制作人工切换/恢复手册；
- 只有业务收益明确后才配置 ConnectX‑7 分布式推理。

### 阶段 D：领域能力

- 定义客户负责、项目协助的数据与权属流程；
- 选择一个任务做受控评测或 LoRA；
- 按插件启用参考检索、RAG、Agent 或仪器接口；
- 任何“准确识别”“鉴定”指标都由独立测试集支持。

## 8. 验收口径

代码仓库通过测试只代表软件逻辑可复现。以下事实必须在目标机器形成记录后才能对客户承诺：

- 目标模型能够在指定 Spark、容器、量化与上下文配置下稳定启动；
- 1/3/5 图任务的 p50/p95 延迟、吞吐和峰值内存；
- 并发 1/2 的排队行为和无 OOM 稳定性；
- 断网/重启恢复与本地离线边界；
- Android 目标机型的 CameraX 组合、后台任务和本地 CA；
- 第二台 Spark 的独立模型收益或分布式收益。

完整测量表见 [`V2_SPARK_ACCEPTANCE.md`](V2_SPARK_ACCEPTANCE.md)。

## 9. 主要官方资料

- [NVIDIA DGX Spark 产品页](https://www.nvidia.com/en-us/products/workstations/dgx-spark/)
- [DGX Spark User Guide](https://docs.nvidia.com/dgx/dgx-spark/index.html)
- [DGX Spark Hardware Overview](https://docs.nvidia.com/dgx/dgx-spark/hardware.html)
- [DGX Spark Release Notes](https://docs.nvidia.com/dgx/dgx-spark/release-notes.html)
- [DGX Spark Known Issues](https://docs.nvidia.com/dgx/dgx-spark/known-issues.html)
- [ConnectX‑7 Networking](https://docs.nvidia.com/dgx/dgx-spark/spark-clustering.html)
- [DGX Spark Porting Guide](https://docs.nvidia.com/dgx/dgx-spark-porting-guide/dgx-spark-porting-guide.pdf)
- [NVIDIA DGX Spark Playbooks](https://github.com/NVIDIA/dgx-spark-playbooks)
- [NIM LLM Support Matrix](https://docs.nvidia.com/nim/large-language-models/latest/reference/support-matrix.html)
- [Android CameraX Architecture](https://developer.android.com/media/camera/camerax/architecture)
- [Android secure networking](https://developer.android.com/develop/connectivity/network-ops/connecting)
