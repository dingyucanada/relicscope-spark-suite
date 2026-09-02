# V2 DGX Spark 与 Scout 现场验收表

当前仓库状态为 `DEPLOYMENT_READY`。任何性能与能力数字均在目标机器实测后填写；空白表示尚未验证。

| 状态 | 升级条件 |
|---|---|
| `DEPLOYMENT_READY` | 代码、配置、部署脚本和交接材料已通过软件侧检查，尚未形成目标硬件验收结论 |
| `HARDWARE_VERIFIED` | 本表中的目标 Spark、模型、网络、安全与真实多图闭环项目完成，并保存可复核证据 |
| `PILOT_ACCEPTED` | `HARDWARE_VERIFIED` 已完成，客户对约定业务场景、边界和运维责任完成评审并签署 |

## A. 系统身份

| 项目 | 主 Spark | 可选第二台 Spark |
|---|---|---|
| 设备序列/资产号 |  |  |
| DGX OS |  |  |
| Kernel |  |  |
| NVIDIA driver |  |  |
| CUDA |  |  |
| Docker / Container Toolkit |  |  |
| NVMe 容量与可用空间 |  |  |
| 10GbE/Wi‑Fi 地址 |  |  |
| ConnectX‑7 固件与链路 |  |  |
| Git commit |  |  |

## B. 模型身份

| 项目 | 值 |
|---|---|
| Runtime provider（NVIDIA NIM / vLLM） |  |
| 模型 source |  |
| NIM profile / immutable model revision |  |
| NIM/vLLM 镜像 digest |  |
| 精度/量化 |  |
| 最大上下文 |  |
| GPU memory utilization |  |
| System prompt SHA‑256 |  |
| Exact request payload SHA‑256 |  |
| 容器启动时间 |  |

## C. 工作负载测量

每个条件至少运行 10 次，首轮冷启动与后续热运行分开。

| 输入 | 并发 | TTFT p50/p95 | 总时长 p50/p95 | 峰值统一内存 | 成功率 | OOM/重启 |
|---|---:|---:|---:|---:|---:|---:|
| 1 张 1600px | 1 |  |  |  |  |  |
| 3 张 1600px | 1 |  |  |  |  |  |
| 5 张 1600px | 1 |  |  |  |  |  |
| 3 张 1600px | 2 |  |  |  |  |  |
| 5 张 1600px | 2 |  |  |  |  |  |

验收时记录 `nvidia-smi`、系统内存、容器内存、API 请求 ID 和每个 job ID。DGX Spark 为统一内存架构，不能只抄传统显存字段。

## D. Scout 可靠性

| 场景 | 期望 | 结果/证据 |
|---|---|---|
| 未配对设备 | 401，不创建任务 |  |
| 已撤销设备 | 401 |  |
| 设备 B 读取设备 A 任务 | 不披露 |  |
| 图片 MIME 伪造 | 422 |  |
| 超大图片 | 413 |  |
| 重复图片冒充不同视角 | 拒绝 |  |
| 相同不可变任务重试 | 返回相同 job ID |  |
| 相同 ID、不同内容 | 409 |  |
| 上传前断网 | WorkManager 保留并恢复 |  |
| App 杀进程/手机重启 | 队列恢复 |  |
| 图片全糊/全黑 | NEEDS_RECAPTURE，不调用 VLM |  |
| 模型冷启动/关闭 | 原 job 保持 QUEUED/RETRY_WAIT，不消费图片或伪造观察 |  |
| 模型在线但 completion 连续失败 | 同 job 指数退避；达到上限后 MODEL_UNAVAILABLE |  |
| 入库后原图被替换 | MEDIA_INTEGRITY_FAILURE，不调用 VLM |  |
| EXIF Orientation 6/8 | 原字节哈希不变；质检和模型像素先转正 |  |
| 数据卷低于保留余量 | 507，健康状态显示 storage degraded |  |
| 网关重启 | 非终态任务恢复 |  |
| 默认 V2 smoke（900 秒总等待） | 仅 SUCCEEDED 且本地 NIM/vLLM、容器、请求与观察绑定齐全时 exit 0 |  |

## E. 双 Spark 决策门

只有以下问题有明确答案才进行分布式推理：

- 单机不能满足的具体指标是什么：容量、延迟还是吞吐？
- 目标模型是否有可复现的 ARM64/GB10 多节点配方？
- QSFP 接口、MTU、Ray/NCCL 和模型 tensor parallel 是否分别通过？
- 双机测得收益是否覆盖网络、部署、故障和维护复杂度？
- 副机作为独立模型/微调节点是否已经能实现更高业务价值？

| 方案 | 单机结果 | 双机结果 | 收益 | 复杂度 | 决策 |
|---|---:|---:|---:|---:|---|
| 独立副模型 |  |  |  |  |  |
| 批处理卸载 |  |  |  |  |  |
| Tensor parallel |  |  |  |  |  |
| 人工备用切换 |  |  |  |  |  |

## F. 签署

技术项目全部通过后记录 `HARDWARE_VERIFIED`；四方完成业务场景、科学边界、数据责任与
运维责任评审后记录 `PILOT_ACCEPTED`。任何未通过项须列出责任人、处置计划和复验日期。

| 角色 | 姓名 | 日期 | 结论 |
|---|---|---|---|
| 客户技术负责人 |  |  |  |
| AI 工程负责人 |  |  |  |
| Scout 产品负责人 |  |  |  |
| 安全/数据负责人 |  |  |  |
