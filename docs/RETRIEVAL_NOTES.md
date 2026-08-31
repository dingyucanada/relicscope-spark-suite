# 本地图像检索核心：接口与集成边界

`app/services/artifact_retrieval.py` 提供与 HTTP、数据库和具体模型框架解耦的多视角检索核心。它消费已经过验证的本地图像嵌入，不调用公网服务，也不让生成式模型计算或改写分数。

## 当前能力

- `ArtifactRetrievalEngine`：查询多图对参考多图执行余弦检索，返回目录 Top-K 与假货/争议参考 Top-K 两个独立结果空间。
- `NumpyCosineBackend`：默认可测试的精确扫描基线；约 50 件、250 至 500 张参考图时优先使用它。
- `FaissCosineBackend`、`CuVSCosineBackend`：可选本地适配器。切换前必须以 NumPy 精确结果验证 Top-K 一致性或召回率；两者都不是硬依赖。
- `CallableLocalImageEmbeddingAdapter`：用于把同进程 OpenCLIP、DINO、Transformers 等本地框架 callable 接到核心；带 `networked=True` 的适配器会被拒绝。
- `embedded_views_from_verified_run`：把异步 `LocalImageEmbeddingClient.embed` 已验证的结果转换为 `EmbeddedView`。该函数不执行 I/O，并再次核对固定模型 revision、输入/输出哈希、维度与单位向量。

正式路径明确拒绝 `relicscope-visual-fingerprint-v1` 和任意 8 维向量。`ReferenceLibraryIndex.reference_images.diagnostic_vector_json` 是图像质量/诊断元数据，绝不能作为同件识别向量，也不能用它替代本地多模态嵌入服务。

FAISS 适配器按其官方约定对单位向量使用 `IndexFlatIP`；cuVS 适配器使用官方 Python brute-force `metric="cosine"`，并把返回的 cosine distance 明确转换为 `1 - distance` 的 cosine similarity。两种适配器最终都必须满足与 NumPy 后端相同的“分数降序”契约。

## 多视角分数

对每个候选器物，核心保留完整的查询图 × 参考图余弦矩阵，并输出：

- 每张查询图的最佳参考视角和原始 cosine；
- 质量加权的最佳视角均值 `view_score`；
- 查询与参考的质量加权中心向量 cosine `centroid_score`；
- 两项权重和最终 `score`；
- `query_view_coverage`；
- 通过一对一二分匹配得到的 `distinct_reference_coverage`；
- 两者较小值作为决策 `coverage`；
- 查询质量、匹配参考质量、Top-1/Top-2 margin 和逐视角门控结果。

一对一覆盖可以阻止多次上传同一角度来虚增多视角覆盖。默认阈值配置的 `policy_id` 含 `unvalidated`，只供代码演示；正式阈值必须来自冻结校准记录，而不是人工拍脑袋配置。

## 决策状态

`same_artifact.status` 与 OpenSpec 对齐：

- `KNOWN_ARTIFACT_CANDIDATE`：绝对分数、margin、质量、覆盖、校准记录和强负向冲突门全部通过；仍只表示参考库身份候选。
- `RELATED_REFERENCES_ONLY`：存在有意义的相关参考，但分数、margin 或强负向冲突不允许选定身份。
- `OPEN_SET_NO_MATCH`：没有目录候选达到相关性下限。
- `INSUFFICIENT_CAPTURE`：输入质量或独立视角覆盖不足。
- `CALIBRATION_REQUIRED`：没有 `calibration_record_sha256`；核心绝不会接受同件候选。

本地嵌入运行失败时，`embedded_views_from_verified_run` 抛出 `EmbeddingRunUnavailable("EMBEDDING_UNAVAILABLE")`，由 API 层转换成 OpenSpec 的 `EMBEDDING_UNAVAILABLE` 运行状态。`RetrievalResult.authenticity_state` 固定为 `NOT_ASSESSED`。

## 假货/争议参考

`ReferenceKind.KNOWN_COUNTERFEIT` 必须附带结构化 `NegativeReferenceControl`，不能仅凭自由格式 metadata 触发反证：

- `admissible_for_signal=False` 的记录仍可出现在独立 Top-K 中，但不会产生反证信号；
- `PROVISIONAL` 只能产生 `WEAK` 提示，不能单独阻断目录同件候选；
- `VERIFIED` 达到分数、覆盖和质量门后可产生 `STRONG` 交叉验证信号，并在与目录候选竞争时阻断自动身份选择；
- `DISPUTED` 或 `REJECTED` 不允许设置为可采纳信号。

无论强弱，结果只表示与已登记记录的图像嵌入相似。它不能单独证明查询物为假或为真。

## 追溯字段

`RetrievalResult.to_dict()` 直接产生可写入 API、会话审计和报告的 JSON 数据，包含：

- `reference_library_id`、`catalog_manifest_sha256`、派生 `index_sha256`；
- `embedding_space_id`、模型来源和不可变 revision；
- `policy_id`、`calibration_record_sha256`、实际后端和后端回退原因；
- 每张查询图的输入 SHA-256、向量 SHA-256、质量和视角；
- 两侧 Top-K、分数分解、逐视角匹配、门控、拒识原因和反证审核信息；
- `exact_media_hash_matches` 和 `EXACT_CATALOG_MEDIA_HASH_MATCH` 审计标志。

训练/入库原图的精确哈希回传必须从准确率、FAR、FRR 和独立复拍验收中排除。该标志不等同于伪造，也不改变普通查询的相似性事实。

## Builder / API bridge

检索核心本身不执行 I/O，也不直接依赖 `main.py`、`orchestrator.py`、会话存储或 UI。仓库中的 `reference_recognition.py` 负责向量索引 builder/loader 和异步嵌入桥接，`RelicScopeService` 负责把受控上传、会话审计和证据图接入该服务。集成层应保持以下顺序：

1. 通过 `ReferenceLibraryIndex` 校验参考库清单、权限、文件哈希、审核和争议状态；
2. 从受控媒体根读取原图，以 `LocalImageEmbeddingClient` 分批生成固定 revision 的正式图像向量；
3. 对每批成功结果调用 `embedded_views_from_verified_run`，按器物分组成 `ArtifactReference`；
4. 由已校验的 `expert_review`、`dispute_status`、`admissible_uses` 和来源记录派生 `NegativeReferenceControl`，不得直接相信 UI 或任意 metadata；
5. 从冻结校准文件加载 `RetrievalThresholds`，验证其数据清单、模型、索引和结果哈希后，才把该文件 SHA-256 传给引擎；
6. 把完整 `RetrievalResult.to_dict()` 原样写入审计/证据/报告；可选生成模型只能摘要，不能改分数、状态或引用。

SQLite 元数据索引和正式向量索引是两个不同资产。正式 builder 应原子发布向量、索引哈希和模型身份；不得把当前 SQLite 中的诊断向量误读为已建成的身份索引。
