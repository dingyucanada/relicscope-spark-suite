# 当前可运行栈与目标 NVIDIA 映射

## 1. 判定原则

本文件区分“本交付已经实现的运行路径”“已有适配边界但未安装的组件”和“长期目标架构”。状态描述代码与部署成熟度，不代替两台真实 DGX Spark 上的验收记录。

## 2. 当前可运行栈

| 层 | 当前实现 | 运行边界 |
|---|---|---|
| Spark A 感知推理 | NVIDIA vLLM 容器启动脚本；OpenAI-compatible `/v1` 接口；有限代表帧视觉模型；可选嵌入服务 | `DEPLOYMENT_READY`：交付内已有启动、认证和健康检查路径；镜像、权重、ARM64/GPU及性能仍需在目标 Spark 预缓存并实测 |
| Spark B 控制面 | FastAPI、单一浏览器入口、SQLite会话、图片/视频登记、多帧质量与指纹、本地知识、P01主动检测、证据图、哈希审计、报告 | 原始媒体与持久写入只发生在 Spark B；模型关闭时仍可运行确定性媒体链路 |
| Spark B可选推理 | vLLM报告摘要服务 | 可关闭；不可用时回退到确定性报告模板 |
| 仪器 | `ReplayInstrumentAdapter` | 当前数据为 `DEMO/SYNTHETIC` 回放，不代表Raman、XRF、HSI或其他真实仪器已接入 |
| 网络 | 两个独立应用服务，经固定私网IP和共享Bearer key通信 | 当前没有跨机张量并行、统一显存、Ray或NCCL依赖 |
| 数据 | 版本化演示知识、运行会话、上传、报告和审计记录 | Gold Dataset、Counterfeit Corpus和机构知识摄取仍需真实授权数据建设 |

## 3. NVIDIA目标映射

| NVIDIA组件 | 目标职责 | 当前状态 |
|---|---|---|
| vLLM on DGX Spark | 本地视觉模型；可选本地摘要模型 | `DEPLOYMENT_READY`；9.4 实机通过后升级为 `HARDWARE_VERIFIED` |
| NVIDIA NIM | 标准化、经许可的模型微服务 | `EVALUATION`；没有作为启动硬依赖 |
| NeMo Retriever | 机构许可PDF、OCR、表格和图片摄取 | `EVALUATION`；当前小型确定性知识库无需完整Blueprint |
| Holoscan SDK / Sensor Bridge | 真实相机、采集卡和传感器低延迟数据流 | `PHASE_2`；设备型号和SDK确定后接入 |
| DeepStream Service Maker Python | Flow/Pipeline API、多路视频、区域候选和流式元数据 | `PHASE_2`；当前文件上传与浏览器有限抽帧不需要，不能标记为已集成 |
| cuVS / Milvus | 大规模向量相似检索 | `PHASE_2`；样本规模达到GPU ANN必要性后启用 |
| TAO / TensorRT | 后续模型适配和推理优化 | 目标映射；本交付没有宣称已经完成专项训练或TensorRT引擎验证 |
| VisuCeram | 卫生陶瓷工业缺陷的域外预研数据集（Apache-2.0；3,265 张、7 类） | `EVALUATION`；对象与古陶瓷鉴证不同，不随本包分发，结果不得解释为古陶瓷结论 |

Holoscan、DeepStream、NIM、NeMo Retriever、cuVS和Milvus不会被名称映射自动变成已集成能力。每个组件都要经过ARM64支持、容器版本、真实数据格式、许可证、资源占用、故障降级和端到端验收。

## 4. 科学边界

- 模型观察只进入“观察/候选证据”，不会直接成为真伪结论。
- RGB 图片和视频只支持可见表面观察，不能生成 Raman、XRF、HSI、X-ray、CT 或 TL 测量。
- 异常热图表示相对训练分布的偏离，不等于伪造、年代、窑口或修复判断。
- 哈希链能够发现记录篡改，不能证明原始输入真实，也不替代机构签章和可信时间戳。
- 无真实仪器、校准、参考物、重复性和专家盲测时，不把回放结果写成科学检测实测。
- 所有报告继续保留 `DEMO/SYNTHETIC` 和非鉴定结论声明，直到机构批准进入真实验证阶段。
