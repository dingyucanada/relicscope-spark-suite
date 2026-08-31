# 成熟框架复用计划与第三方边界

## 1. 总体结论

RelicScope采用“轻量控制面 + 成熟专用组件适配层”。底层科学软件负责各自经过实践验证的数学和数据处理；RelicScope负责区域坐标、校准引用、质量门控、风险账本、证据状态、来源、权限和报告追溯。

本次检索范围内，没有发现许可证清晰、经过广泛验证、能够直接对古陶瓷真伪鉴定负责的成熟开源系统。产品不能把通用工业缺陷代码、异常热图或公开图片分类器包装成古陶瓷鉴定结论。

状态含义：

- `DEPLOYMENT_READY`：当前交付已有启动、配置、认证与健康检查路径，尚未代表目标硬件进程正在运行。
- `HARDWARE_VERIFIED`：已经在本次目标硬件、固定版本和网络边界下留下可复核运行证据。
- `ADAPTER_READY`：已有清晰接入契约，第三方依赖未随本包安装。
- `EVALUATION`：值得验证，尚未达到主路径要求。
- `PHASE_2`：在真实设备或规模化数据阶段接入。
- `REJECTED`：当前许可证、来源或科学边界不合适。

## 2. 组件矩阵

| 组件 | 状态 | 拟承担职责 | 必须满足的启用条件 |
|---|---|---|---|
| NVIDIA vLLM on DGX Spark | `DEPLOYMENT_READY` | Spark A本地VLM；Spark B可选摘要 | 固定镜像/模型版本、许可证复核、离线缓存、认证、GPU和性能实测；完成后标记 `HARDWARE_VERIFIED` |
| Qwen3-VL-Embedding-2B + Sentence Transformers 5.4 | `DEPLOYMENT_READY` | 单机 50 件多视角参考库图像 embedding；NumPy exact cosine 作为小规模可复算检索基线 | 固定 40/64 位模型 revision、内部端口、真实受控数据、独立复拍与开集负样本、冻结阈值；目标 Spark 完成内存/延迟/数值验收前不标 `HARDWARE_VERIFIED` |
| RamanSPy | `ADAPTER_READY` | Raman基线校正、平滑、归一化、解混 | BSD-3-Clause登记；真实文件格式、参数、参考谱、重复性和激光条件验证 |
| PyMca | `ADAPTER_READY` | XRF谱峰、拟合和批处理结果导入 | MIT登记；仪器校准、基体效应、层结构及定量边界验证 |
| Spectral Python | `ADAPTER_READY` | HSI立方体读取、波段处理、分类基础 | MIT登记；白/暗参考、光谱库、配准和跨设备验证 |
| Open3D | `ADAPTER_READY` | 点云、网格、配准和几何变化 | MIT登记；公制标定、遮挡、高反射釉面和重复扫描误差验证 |
| anomalib | `EVALUATION` | 表面异常候选热图 | Apache-2.0；使用合法分层数据训练，进行外部留出和跨设备验证；界面必须说明“异常≠真伪” |
| VisuCeram | `EVALUATION` | 卫生陶瓷表面缺陷的域外迁移/拒绝判断预研 | Apache-2.0；3,265 张、7 类；对象为卫生陶瓷工业质检，与古陶瓷科学鉴证域不同，不随本包分发且不得直接用于古陶瓷结论 |
| OpenLIME | `EVALUATION` | RTI、多光照、多光谱和IIIF查看 | 打包前复核具体许可证及依赖；验证大图性能和机构部署方式 |
| NeMo Retriever | `EVALUATION` | PDF/OCR/表格/图片的大规模知识摄取 | 文献授权、资源预算、离线部署、引文定位和删除/撤权流程 |
| NVIDIA NIM | `EVALUATION` | 经许可的标准化模型微服务 | 逐项确认可用镜像、模型许可、ARM64支持和运行资源；不能成为当前硬依赖 |
| HyperSpy | `EVALUATION` | 多维光谱研究工具 | GPLv3；专有产品集成前完成法律审查，优先考虑进程外研究工具模式 |
| Holoscan / Sensor Bridge | `PHASE_2` | Vault 相机、采集卡和仪器的低延迟传感器流 | NVIDIA 已列 DGX Spark / DGX OS 与 CX7 路径；仍需真实设备、驱动、时间同步、背压、安全联锁和故障恢复验证 |
| DeepStream Service Maker Python | `PHASE_2` | 多路视频解码、实时区域候选和流式元数据 | 复用官方 Flow/Pipeline API；先验证当前 DGX Spark/ARM64 版本与容器，不基于已弃用旧 Python bindings 启动新实现 |
| cuVS / Milvus | `PHASE_2` | 大规模样本向量检索 | Gold Dataset达到需要GPU ANN的规模；完成召回、索引版本和删除审计 |
| CE5-DET | `REJECTED` | 不集成 | 仓库及数据来源授权边界不清；取得明确商业授权前不得使用 |

## 3. 数据与许可证禁区

- MVTec AD包含可用于纹理方法研究的`tile`类别，但其数据许可限制商业使用。本交付不下载、不复制、不训练默认模型，也不把数据或派生权重打入商业演示包。
- VisuCeram 仓库以 Apache-2.0 发布并包含卫生陶瓷工业缺陷数据和 YOLO 模型，但许可证允许不消除任务域差异。它只用于研究迁移能力、域外检测和拒绝判断；本交付不下载、不复制、不打包，不把卫生陶瓷缺陷类别映射成古陶瓷修复、真伪或年代标签。
- CE5-DET在授权来源清晰前不集成、不下载、不制作缓存。
- HyperSpy的GPLv3义务需由法律顾问结合实际链接、分发和进程边界判断。
- “代码许可证允许”不代表模型权重、数据集、文献图片、设备SDK和参考谱均可商用；每项资产分别登记来源、版本、许可、地域、用途和再分发限制。
- `deploy/package.sh --offline`只打包当前角色的容器镜像和模型需求清单；不下载、不复制或再分发任何第三方模型权重与数据。权重必须通过机构批准、符合许可证和地域要求的独立渠道取得。

## 4. 统一适配器输出

真实科学组件应输出统一的 `ScientificResultEnvelope`，至少包含：

- `modality`、`source_file_hash`、`instrument_id`；
- `calibration_ref`、`protocol_version`、区域坐标与配准版本；
- 原始 `quality_metrics`、稳定错误码和适用范围；
- `result_payload`、`software_name`、`software_version`；
- `demo_data`、`license`、`provenance`；
- 原始文件引用和结果哈希。

第三方组件不得直接修改命题、不确定性或风险账本。RelicScope先记录原始结果和实际负荷，再执行质量门控；只有质量通过的证据才能参与声明更新。

## 5. 验证门槛

一个组件从 `ADAPTER_READY/EVALUATION/DEPLOYMENT_READY` 提升为 `HARDWARE_VERIFIED` 前，至少完成：

1. ARM64/DGX Spark安装和固定版本复演；
2. 许可证、模型卡、数据来源和地区限制复核；
3. 真实仪器或合法样本的输入/输出契约；
4. 校准、重复性、跨设备、跨批次和故障样本；
5. 超时、取消、无效遥测、资源耗尽和安全停止；
6. 证据图中的软件版本、参数、输入哈希与来源追踪；
7. 回退路径，确保可选组件失效不破坏核心证据查看和报告。

## 6. 上游来源

- [NVIDIA DGX Spark Playbooks](https://github.com/nvidia/dgx-spark-playbooks)
- [NVIDIA vLLM on DGX Spark](https://github.com/nvidia/dgx-spark-playbooks/blob/main/nvidia/vllm/README.md)
- [Qwen3-VL-Embedding-2B](https://huggingface.co/Qwen/Qwen3-VL-Embedding-2B)
- [Sentence Transformers](https://www.sbert.net/)
- [NVIDIA NeMo Retriever](https://docs.nvidia.com/nemo/retriever/latest/)
- [NVIDIA NIM on DGX Spark](https://build.nvidia.com/spark/nim-llm)
- [NVIDIA Holoscan SDK](https://github.com/nvidia-holoscan/holoscan-sdk)
- [Holoscan Sensor Bridge：DGX Spark 主机支持](https://docs.nvidia.com/holoscan/sensor-bridge/getting-started/host-setup)
- [DeepStream Service Maker for Python](https://docs.nvidia.com/metropolis/deepstream/dev-guide/text/DS_service_maker_python.html)
- [DeepStream旧Python bindings弃用说明](https://github.com/NVIDIA-AI-IOT/deepstream_python_apps)
- [RAPIDS cuVS](https://github.com/rapidsai/cuvs)
- [Milvus](https://github.com/milvus-io/milvus)
- [anomalib](https://github.com/open-edge-platform/anomalib)
- [VisuCeram](https://github.com/Md-Ali-Azad/VisuCeram)
- [MVTec AD](https://www.mvtec.com/research-teaching/datasets/mvtec-ad)
- [RamanSPy](https://github.com/barahona-research-group/RamanSPy)
- [PyMca](https://github.com/vasole/pymca)
- [Spectral Python](https://github.com/spectralpython/spectral)
- [Open3D](https://github.com/isl-org/Open3D)
- [OpenLIME](https://github.com/cnr-isti-vclab/openlime)
- [HyperSpy](https://github.com/hyperspy/hyperspy)
- [CE5-DET（仅用于许可风险记录，不集成）](https://github.com/PGYBHF/CE5-DET)
