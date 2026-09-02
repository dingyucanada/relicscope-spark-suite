# RelicScope V2 模型选型与 DGX Spark 运行栈

> 决策基线：2026-09-03
>
> 适用范围：Scout 多视角陶瓷图片 → 本地可见特征观察 → 结构化结果
> 状态：软件配置已形成；模型性能与结果质量仍须在客户 DGX Spark 和授权样本上验收

## 结论

第一台 DGX Spark 的首选生产试运行组合为：

- **模型：Qwen3.6-35B-A3B**；
- **运行时：NVIDIA NIM for VLM，DGX Spark 专用 `1.7.1-variant`**；
- **精度：先让 NIM 在目标机器选择兼容 profile，并把实际 profile ID 冻结到配置；优先评估 NVFP4 Fast MTP 与 FP8 DFlash；**
- **产品模式：1–8 张图片一次多图请求，关闭 thinking，温度为 0，返回受校验 JSON；**
- **视频：第一阶段关闭。视频进入独立验收后再启用，不与图片主链路共用发布门槛。**

这个选择不意味着 Qwen3.6 是所有指标上的最新模型。它是当前同时满足中文、多图、开放权重、OpenAI-compatible API，以及 **NVIDIA 明确提供 DGX Spark 专用 ARM64 NIM profile** 的最低风险组合。Qwen3.8-27B 更新、能力上限可能更高，但当前 NIM 文档只给出通用显存门槛，没有把 GB10/DGX Spark 列为单独验证配置；因此先作为同机顺序 A/B 候选。

## 为什么先选这一组合

DGX Spark 的 128 GB 是 CPU/GPU 共享统一内存，操作系统、模型、KV cache、图像预处理和应用共同占用。它适合常驻一套经过量化的多模态模型，不适合为了展示同时常驻多套大模型。NVIDIA 对 Qwen3.6-35B-A3B 提供 DGX Spark 专用 NIM 版本，并列出 FP8 与 NVFP4、MTP/DFlash 等配置；其中模型缓存约为 22–35 GB。模型本身为 35B 总参数、约 3B 激活参数，支持图像和视频，并有中文评测结果。

NIM 作为首选运行时的价值是把硬件探测、模型 profile、推理后端、健康检查、指标和 OpenAI-compatible API 封装为受支持容器。RelicScope 网关只依赖私有 `/v1` 接口，后续仍可在不改变 Scout 协议和证据结构的情况下替换 vLLM、SGLang 或新 NIM。

该 Spark 专用 `1.7.1-variant` 还有一个部署差异：NVIDIA 明确注明其容器不支持
`docker run -u`。因此本项目不为 NIM 服务设置自定义容器用户，并保留可写的私有
NIM cache；网络隔离、关闭运行期下载、不注入 NGC 凭据和固定镜像/profile 承担
模型服务的主要运行边界。网关和 HTTPS ingress 继续按非 root 最小权限运行。

## 候选矩阵

| 槽位 | 模型与运行时 | 主要价值 | 当前限制 | 决策 |
|---|---|---|---|---|
| 主模型 | Qwen3.6-35B-A3B + NVIDIA NIM `1.7.1-variant` | 中文、多图、图像/视频；DGX Spark 专用 profile；35B/3B active | 仍需真实陶瓷数据验证；NIM profile 和容器条款需现场冻结 | **第一台 Spark 默认** |
| 质量挑战者 | Qwen3.8-27B FP8 + NVIDIA NIM `2.1.1-variant` | 更新的 dense 原生视觉语言模型；图像/视频；Apache-2.0 | 发布较新；当前矩阵为通用 `>36 GB`，未单列 Spark；dense 27B 延迟需实测 | 第二台或同机顺序 A/B |
| 视频挑战者 | Nemotron 3 Nano Omni 30B-A3B NVFP4 | 图像、视频、音频统一；官方模型卡列出 DGX Spark；约 21 GB | 训练/评测主要为英文，不能直接作为中文报告默认模型 | 独立视频/语音实验 |
| 图像检索插件 | Qwen3-VL-Embedding-2B | 图文/多图/视频向量，支持中文；用于库内同件候选和相关参考 | 需要客户授权数据、独立复拍和开放集校准 | 默认关闭，第二阶段启用 |
| 低成本备选 | MiniCPM-V 4.6 | 较小、适合 Scout 或快速筛选研究 | 没有当前项目所需的 NVIDIA Spark 专用验证与正式领域基线 | 仅做端侧/降本实验 |

## 第一阶段推理契约

主链路只要求模型完成“可见观察”，不要求模型直接鉴定：

1. Scout 采集 1–8 张带视角代码的图片。
2. 网关核对 MIME、解码、像素、哈希、重复图和最低图像质量。
3. 通过质量门的图片在一个请求中按采集顺序送入 VLM。
4. 模型只返回每视角可见事实、跨视角事实、采集问题、限制和 OOD 风险。
5. 服务端用固定 schema、capture ID 白名单和结论边界过滤器校验结果。
6. 原始输入哈希、请求哈希、模型名、NIM 容器 digest、profile ID、输出哈希和延迟写入运行证明。

Scout 请求使用 NVIDIA 建议的 OpenAI-compatible `response_format.type=json_schema`，
并把本次允许的 capture ID 与视角代码写入动态 schema。NIM 的结构化生成先限制
JSON 外形，服务端随后再次验证 capture-to-view 对应关系、字段长度、完整视角覆盖和
科学结论禁区；两层约束都通过后才会形成可见观察。

推荐的初始约束：

| 项目 | 初值 | 理由 |
|---|---:|---|
| 每任务图片数 | 1–8；标准流程 5 张 | 与 Scout 协议一致，保留底足和细节视角 |
| 上下文 | 16K 起步 | 给统一内存留出图像预处理、KV cache 和系统余量 |
| 输出 | 最多 1200 tokens | 结构化观察足够，限制无关长推理 |
| thinking | 关闭 | 降低延迟和输出漂移；科学决策由确定性代码承担 |
| temperature | 0 | 提高复测稳定性 |
| 并发 | 1 起步 | 单台 Spark 先保证稳定和内存余量，再测并发 2 |
| 视频 | 0 | 图片主闭环单独发布；视频需要 FFmpeg ARM64 依赖和独立资源验收 |

以上只是安全起点，不能写成已验证性能。正式值由 `V2_SPARK_ACCEPTANCE.md` 的 1/3/5 图、冷/热、并发 1/2、连续任务测试决定。

## 模型晋级规则

Qwen3.8 或 Nemotron 只有同时通过以下门槛，才可替换主模型：

- 相同输入文件、视角顺序、提示词和结构化 schema；
- 目标 Spark 连续运行，无 OOM、容器重启或静默回退；
- 中文字段完整，capture ID 与视角绑定正确；
- 结论边界违规率不得高于当前基线；
- 文保/陶瓷专家对可见事实准确性、遗漏和补拍建议进行盲评；
- p50/p95 总时长与峰值统一内存满足客户现场节奏；
- 模型许可、容器条款、镜像 digest 和 profile/revision 均可冻结。

“模型更新”本身不是晋级理由。RelicScope 以受控任务上的证据质量、稳定性和可复现性选择模型。

## 微调策略

当前不先做全模型微调。优先级为：

1. 固定拍摄协议和输入质量；
2. 固定输出 schema、禁区和评测集；
3. 完成 prompt baseline 与错误分类；
4. 有足够授权、专家标注、训练/验证严格隔离的数据后，才评估 LoRA/PEFT；
5. 微调目标限定为视角分类、字段抽取、陶瓷术语一致性或补拍建议，不训练“单凭照片给真伪结论”。

第二台 Spark 可运行候选模型、批量评测或 LoRA；也可部署同一主模型作为人工切换备用。两台机器不被强制绑定为固定角色。

## 官方依据

- [NVIDIA DGX Spark Hardware Overview](https://docs.nvidia.com/dgx/dgx-spark/hardware.html)
- [NVIDIA DGX Spark Release Notes](https://docs.nvidia.com/dgx/dgx-spark/release-notes.html)
- [NVIDIA NIM for VLM prerequisites](https://docs.nvidia.com/nim/vision-language-models/latest/get-started/prerequisites.html)
- [Qwen3.6-35B-A3B DGX Spark NIM support matrix](https://docs.nvidia.com/nim/vision-language-models/1.7.0/support-matrix.html#qwen3-6-35b-a3b)
- [Qwen3.6-35B-A3B DGX Spark API guide](https://docs.nvidia.com/nim/vision-language-models/1.7.0/examples/qwen3.6/api-dgx-spark.html)
- [Qwen3.6 NIM container variant notes](https://docs.nvidia.com/nim/vision-language-models/1.7.0/nim-container-variants.html)
- [NVIDIA NIM structured generation](https://docs.nvidia.com/nim/vision-language-models/1.7.0/structured-generation.html)
- [Qwen3.6-35B-A3B official model card](https://huggingface.co/Qwen/Qwen3.6-35B-A3B)
- [Qwen3.8-27B official model card](https://huggingface.co/Qwen/Qwen3.8-27B)
- [Qwen3.8-27B NIM support matrix](https://docs.nvidia.com/nim/vision-language-models/2.1.1-variant/support-matrix.html)
- [Nemotron 3 Nano Omni NVFP4 official model card](https://huggingface.co/nvidia/Nemotron-3-Nano-Omni-30B-A3B-Reasoning-NVFP4)
- [Qwen3-VL-Embedding-2B official model card](https://huggingface.co/Qwen/Qwen3-VL-Embedding-2B)
- [NVIDIA NIM air-gap deployment](https://docs.nvidia.com/nim/vision-language-models/latest/deploy-air-gap.html)
