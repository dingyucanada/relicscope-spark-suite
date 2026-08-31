# Change: 增加古陶瓷参考样本识别与假货反证检索

## Why

当前系统能够分析单幅图像、视频和科学证据，但还没有把真实参考器物、多视角照片和已审核假货资料组织成可校准的本地识别系统。首批演示需要回答两个不同问题：上传照片是否来自参考库中的某一件器物；若不在库内，与哪些参考器物、工艺或已知假货模式相关。两类结果必须使用不同状态和阈值，避免把相似度误写成身份或真伪结论。

## What Changes

- 建立一个版本化的本地参考样本库，正式演示发布门槛为 50 件器物、每件至少 5 个规定视角，并保存照片哈希、来源、许可、专家审核和采集条件。
- 建立独立的假货/争议样本空间，只有具有可定位审核依据的记录才能进入反证检索；“疑似”资料与“经确认”资料分级保存。
- 使用本地多模态嵌入服务生成图像向量，对 250 张以上参考图执行精确余弦检索；50 件规模默认使用精确扫描，保留 cuVS 后端适配边界。
- 增加多视角聚合、第一名/第二名间隔、图像质量、视角覆盖和开放集拒识。系统只在所有门槛通过时返回“参考库同件候选”；否则进入开放集相关性报告。
- 将真品参考相似性、假货模式相似性、冲突项、资料等级和局限性写入会话证据图与报告；生成式模型只能解释应用计算出的状态，不能改写分数或拒识结论。
- 提供真实资料导入模板、完整性校验、索引构建、阈值校准和冻结测试集验收工具。仓库不内置伪造的真实藏品履历。

## Impact

- Affected code: `app/config.py`, `app/main.py`, `app/orchestrator.py`, `app/schemas.py`, `app/services/`, `app/static/`, `compose.single.yml`, deployment scripts and tests.
- New runtime assets: reference manifest, counterfeit manifest, immutable vector index, calibration record and local multimodal embedding endpoint.
- Operational consequence: single-Spark deployment additionally preloads Qwen3-VL-Embedding-2B; the 30B generation model and 2B embedding model may coexist, while Qwen/Nemotron generation candidates remain sequential.
- Data dependency: formal 50-item performance claims remain blocked until the owner supplies or licenses the required multi-view photographs and expert-reviewed metadata.

