# RelicScope AI Spark Demo 规格—测试映射

更新日期：2026-08-30  
对应变更：`build-relicscope-dual-spark-demo`

## 1. 判定口径

- **通过**：当前仓库存在直接自动化用例，或浏览器验收已观察到要求规定的可见结果。
- **部分**：核心行为已有证据，但某个边界场景、真实模型或真实设备条件尚未独立验证。
- **待实机**：只有代码、配置或静态检查证据；必须在两台 DGX Spark 上复验。

表内同一单元格连续列出多个测试函数时，未重复书写路径的后续函数沿用该单元格最近标明的测试文件。

本次基线结果：

- `openspec validate build-relicscope-dual-spark-demo --strict`：通过。
- `python3 -m pytest -q`：**103 passed**。
- 16个`deploy/`与`scripts/` Shell脚本通过`bash -n`及严格模式检查。
- `docker compose -f compose.yml config --quiet` 与 `compose.single.yml`：通过。
- 浏览器证据见 `docs/UI_ACCEPTANCE.md`。

> 以上结果证明本地确定性演示与配置结构可用。任务 9.4 所要求的双 Spark 私网端到端验证尚未执行，原因是仍缺两台设备的固定私网 IP 与操作权限；本文不将双机实机运行标记为通过。

v1.1.0 在现有 `artifact-session`、`multimodal-perception` 和 `evidence-chain-reporting` 规格内加入兼容的视频、多帧、复拍比较与 Next Best Observation。新增测试覆盖视频闭环、重复帧、全拒绝帧、失败原子性、图片比较和上传流式限制；真实 JPEG/MP4 烟雾及重启持久性记录见 `docs/MEDIA_ACCEPTANCE_V1.1.md`。

## 2. `artifact-session`

规格：`openspec/changes/build-relicscope-dual-spark-demo/specs/artifact-session/spec.md`

| Requirement | 自动化证据 | 浏览器/部署证据 | 判定 |
|---|---|---|---|
| 创建科学鉴证会话 | `tests/test_api.py::test_health_and_session_validation` | UI-05 创建唯一会话并显示协议、版本和下一步 | 通过 |
| 管理科学命题与观察区域 | `tests/test_api.py::test_invalid_order_mime_region_and_knowledge_space_are_non_mutating` | UI-05 上传数据明确绑定 `R1` | 通过 |
| 原始文件完整性登记 | `tests/test_api.py::test_image_knowledge_active_sensing_and_report_flow`；`tests/test_image_analysis.py::test_image_decode_mime_quality_and_fingerprint_are_reproducible`、`test_tiny_and_damaged_images_are_rejected`、`test_excessive_image_dimensions_and_pixel_counts_are_rejected` | UI-05 显示 64 位原始文件 SHA-256 | 通过；“同字节、不同文件名重复上传”未设独立 API 用例，但摘要算法复算已覆盖 |
| 会话状态与版本可观察 | `tests/test_store.py::test_atomic_update_version_and_audit_chain`；`tests/test_api.py::test_invalid_order_mime_region_and_knowledge_space_are_non_mutating` | UI-05、UI-08 显示版本和状态推进 | 通过 |
| 演示数据来源隔离 | `tests/test_api.py::test_image_knowledge_active_sensing_and_report_flow`；`tests/test_knowledge.py::test_manifest_is_sealed_complete_and_demo_isolated` | UI-01、UI-06、UI-09 均显示 `DEMO/SYNTHETIC` | 通过 |

## 3. `local-knowledge-retrieval`

规格：`openspec/changes/build-relicscope-dual-spark-demo/specs/local-knowledge-retrieval/spec.md`

| Requirement | 自动化证据 | 浏览器/部署证据 | 判定 |
|---|---|---|---|
| 知识条目具有完整来源信息 | `tests/test_knowledge.py::test_manifest_is_sealed_complete_and_demo_isolated`、`test_strict_manifest_validation_rejects_missing_tampered_or_unlabelled_data` | UI-06 展示来源、分数、位置和版本 | 通过 |
| 知识库版本与检索快照可复现 | `tests/test_knowledge.py::test_sealing_a_revision_creates_a_new_content_addressed_version`、`test_same_query_and_version_return_a_reproducible_candidate_set`、`test_audit_payload_contains_version_algorithm_hashes_and_actual_references` | UI-01、UI-06 显示内容寻址知识版本 | 通过 |
| 本地多模态检索返回可解释结果 | `tests/test_knowledge.py::test_hybrid_retrieval_explains_text_structure_and_visual_scores`、`test_space_access_license_scope_and_minimum_score_are_hard_filters` | UI-06 实际返回 3 条本地候选及相似性边界 | 通过 |
| 下游陈述必须绑定引用 | `tests/test_knowledge.py::test_actual_retrieval_citations_pass_and_model_invented_citations_fail`、`test_query_snapshot_tampering_is_rejected_before_citation_validation`；`tests/test_vlm_guardrails.py::test_reasoner_citations_must_be_bound_to_report_results` | UI-06 引用卡来自实际 API 返回 | 通过 |
| 离线运行不得隐式访问外部知识服务 | `tests/test_knowledge.py::test_offline_mode_never_calls_a_networked_provider_and_reports_degraded`、`test_provider_outage_fails_closed_to_deterministic_local_index` | DEP-02、DEP-03；UI-04 显示本机降级与 `LOCAL / PRIVATE` | 通过本地策略；真实隔离网络流量复核归入 9.4 |
| 演示知识与正式知识严格隔离 | `tests/test_knowledge.py::test_manifest_is_sealed_complete_and_demo_isolated`、`test_space_access_license_scope_and_minimum_score_are_hard_filters` | UI-06 每条结果标示 `DEMO/SYNTHETIC` | 通过 |
| 资料使用边界在检索时持续生效 | `tests/test_knowledge.py::test_space_access_license_scope_and_minimum_score_are_hard_filters` | 界面只呈现通过策略的候选，不显示受限正文 | 通过 |

## 4. `multimodal-perception`

规格：`openspec/changes/build-relicscope-dual-spark-demo/specs/multimodal-perception/spec.md`

| Requirement | 自动化证据 | 浏览器/部署证据 | 判定 |
|---|---|---|---|
| 图像质量检查 | `tests/test_image_analysis.py::test_image_decode_mime_quality_and_fingerprint_are_reproducible`、`test_tiny_and_damaged_images_are_rejected`、`test_excessive_image_dimensions_and_pixel_counts_are_rejected` | UI-05 展示总门禁、逐项指标和失败语义 | 通过 |
| 确定性视觉指纹 | `tests/test_image_analysis.py::test_image_decode_mime_quality_and_fingerprint_are_reproducible` | UI-05 显示 64 位指纹和算法标识 | 通过 |
| 可观察的候选区域 | 同上验证返回两个归一化候选区域；API 全流程验证其进入证据图 | UI-05 在原图上渲染 ROI 框 | 通过 |
| 视觉语言模型观察限界 | `tests/test_vlm_guardrails.py::test_compliant_vision_output_is_accepted`、`test_vision_verdicts_are_rejected`；`tests/test_api.py::test_model_failures_are_recorded_and_report_falls_back` | UI-01、UI-09 始终保留非鉴定边界 | 通过；真实 Spark VLM 内容仍需 9.4 抽检 |
| 模型运行全程追溯 | `tests/test_api.py::test_image_knowledge_active_sensing_and_report_flow`；`test_model_failures_are_recorded_and_report_falls_back` | UI-01 展示模型身份、节点状态；UI-08 图谱保留模型运行节点 | 通过本地/替身；实际 A/B 节点身份待 9.4 |
| 相似参考召回及解释边界 | `tests/test_knowledge.py::test_hybrid_retrieval_explains_text_structure_and_visual_scores`；API 全流程 | UI-06 返回 3 条、按分数展示，并明确相似性不等于真伪或年代 | 通过 |

## 5. `active-scientific-sensing`

规格：`openspec/changes/build-relicscope-dual-spark-demo/specs/active-scientific-sensing/spec.md`

| Requirement | 自动化证据 | 浏览器/部署证据 | 判定 |
|---|---|---|---|
| 检测候选必须先通过安全硬约束 | `tests/test_active_sensing.py::test_material_and_device_preconditions_are_hard_constraints`、`test_p01_replay_sequence` | UI-07 显示 XRF 超预算阻断及候选理由 | 通过 |
| 执行前必须原子预留风险预算 | `tests/test_active_sensing.py::test_second_plan_cannot_reserve_while_action_is_active`、`test_concurrent_duplicate_execute_invokes_adapter_once_and_returns_result`；`tests/test_api.py::test_concurrent_plans_reserve_budget_once` | UI-07 显示预留，执行后切换为 `SETTLED · 无预留` | 通过 |
| 实际暴露必须先于质量判断结算 | `tests/test_active_sensing.py::test_p01_replay_sequence`、`test_missing_telemetry_is_settled_conservatively`、`test_adapter_failure_is_conservatively_settled_and_locks_channel` | UI-07 Raman 质量失败后风险仍由 0.50 结算至 0.73 | 通过 |
| 预留释放与实耗结算保持一致 | `tests/test_active_sensing.py::test_p01_replay_sequence`、`test_settlement_rejects_mismatched_execution_identity`、`test_adapter_failure_is_conservatively_settled_and_locks_channel`、`test_malformed_adapter_identity_is_conservatively_settled` | UI-07 最终预留为 0、光化学实耗为 0.78 | 部分：一次性结算已覆盖；未做存储中断后的独立恢复注入测试 |
| 质量门控决定证据能否更新命题 | `tests/test_active_sensing.py::test_p01_replay_sequence`；`tests/test_api.py::test_image_knowledge_active_sensing_and_report_flow` 验证 `not_admitted` | UI-07 Raman 后不确定度保持 0.85，HSI 通过后降至 0.48 | 通过 |
| 重复失败动作必须被抑制 | `tests/test_active_sensing.py::test_p01_replay_sequence` 验证失败指纹进入 `retry_blocked` 且 Raman 不被重选 | UI-07 重规划选择 HSI | 部分：抑制已直接覆盖；“重新校准后解除抑制”尚无独立用例 |
| 每轮结果必须触发受约束重规划 | `tests/test_active_sensing.py::test_p01_replay_sequence`；`tests/test_api.py::test_image_knowledge_active_sensing_and_report_flow` | UI-07 一键流程执行两轮真实 `/plan` | 通过 |
| 系统必须能够保守弃权和升级 | API 全流程产生 `REVIEW_REQUIRED`；`tests/test_vlm_guardrails.py::test_guardrail_does_not_reject_scientific_abstention_language` | UI-09 只输出复核状态、局限和下一步 | 部分：未为“所有候选均不可执行”建立独立端到端场景 |
| 主动检测状态转换可审计 | `tests/test_active_sensing.py::test_concurrent_duplicate_execute_invokes_adapter_once_and_returns_result`；`tests/test_api.py::test_image_knowledge_active_sensing_and_report_flow`；`tests/test_store.py` | UI-08 按序显示规划、执行、结算与报告事件 | 通过 |

## 6. `evidence-chain-reporting`

规格：`openspec/changes/build-relicscope-dual-spark-demo/specs/evidence-chain-reporting/spec.md`

| Requirement | 自动化证据 | 浏览器/部署证据 | 判定 |
|---|---|---|---|
| 构建可追溯科学证据图 | `tests/test_api.py::test_image_knowledge_active_sensing_and_report_flow` 验证 `derived_from`、`cites`、`not_admitted`、`conflicts_with` | UI-08 一键流程渲染 14 个实际 API 节点；带图像上传的完整流程另增加原始图像、质量与指纹节点 | 通过 |
| 生成并验证哈希审计链 | `tests/test_store.py` 的完整链、载荷篡改、截尾和状态尾指针篡改用例 | UI-08 返回“审计链完整”并列出事件哈希 | 通过 |
| 保守结论与弃权机制 | `tests/test_vlm_guardrails.py` 全部结论边界用例；API 报告回退用例 | UI-01、UI-09 显示 `REVIEW REQUIRED`/`EVIDENCE INSUFFICIENT` 与非鉴定边界 | 部分：高 OOD 触发完整编排路径尚无独立 API 用例 |
| 生成可下载的结构化科学报告 | `tests/test_reporting.py::test_human_report_contains_all_required_audit_sections`；API 全流程验证 JSON/HTML、SHA-256 | UI-09 报告标识、覆盖版本、64 位哈希及下载入口可见 | 部分：新证据后生成第二版报告的版本差异尚无独立用例 |
| DEMO 数据免责声明贯穿输出 | API 全流程；`tests/test_reporting.py` | UI-01 首屏、UI-06 引用、UI-09 报告区均默认可见 | 通过 |

## 7. `dual-spark-runtime`

规格：`openspec/changes/build-relicscope-dual-spark-demo/specs/dual-spark-runtime/spec.md`

| Requirement | 自动化/静态证据 | 浏览器/部署证据 | 判定 |
|---|---|---|---|
| 双机采用独立服务协同拓扑 | `tests/test_api.py::test_health_and_session_validation` 验证 `tensor_parallel=false`；`compose.yml` | DEP-02 配置解析通过；尚无双机启动记录 | 待实机 |
| 逻辑角色必须可配置 | `app/config.py`、`compose.yml`、`compose.single.yml`；`deploy/preflight.sh` 校验节点与角色 | UI-04 能依据模式切换真实双机/本机逻辑标签 | 部分；角色换位实机未验证 |
| 节点通信限制在受信私网 | `tests/test_security.py::test_public_model_endpoint_is_rejected`、`test_private_model_endpoints_are_accepted` | `deploy/preflight.sh` 固定私网 IP/CIDR 检查；`compose.yml` 私有应用网络 | 部分；非受信主机实连拒绝待 9.4 |
| 跨节点 API 必须认证 | `tests/test_security.py::test_configured_private_model_endpoints_require_api_keys`、`test_health_and_audit_do_not_leak_model_keys` | 部署脚本从权限受限密钥文件注入 vLLM API key | 部分；缺失/错误 key 的真实跨机请求待 9.4 |
| 每项服务具有存活与就绪状态 | `tests/test_security.py::test_dual_node_readiness_requires_multimodal_compute` | Compose healthcheck 与 `deploy/preflight.sh --check-running` 已定义 | 部分；真实模型加载/恢复待 9.4 |
| 双机故障必须触发可见降级 | `tests/test_api.py::test_model_failures_are_recorded_and_report_falls_back`；双机就绪降级测试 | UI-04 在 `single-degraded` 中明确显示“逻辑 A/B（本机回退）” | 通过降级语义；真实断开 Spark A 的故障演练待 9.4 |
| 单台 Spark 支持核心演示模式 | `compose.single.yml`、`deploy/spark-b-app.sh --single`、`run_local.sh` | UI-04 本机降级标识；本地完整 P01 已通过 | 部分；尚未在单台 DGX Spark 上测资源与模型组合 |
| 运行数据不得外泄 | 外部端点拒绝测试；本地知识网络 spy；离线 Compose/预缓存策略 | DEP-02、DEP-03 | 部分；隔离网络抓包与完整双机离线流量证明待 9.4 |
| 运行记录可证明实际执行位置 | API 全流程和报告测试验证 `node_id`、模型、输入/输出哈希与降级状态 | UI-01、UI-08 能显示现有运行记录 | **待实机**：当前记录只证明本地/替身路径，不能证明两台 Spark 的实际 A/B 执行位置 |

## 8. v1.1.0 媒体扩展映射

| 既有规格 Requirement | 视频兼容扩展 | 必需证据 | 当前判定 |
|---|---|---|---|
| `artifact-session`：原始文件完整性登记 | 原视频格式/大小验证、服务端 SHA-256、媒体元数据和派生帧关系 | `test_video_upload_frame_analysis_evidence_integrity_and_report`、`test_invalid_or_oversized_video_does_not_mutate_session`；真实烟雾与重启复算 | **通过本机** |
| `multimodal-perception`：图像质量检查 | 每个证据帧重新解码并执行同一质量门控 | 合格、重复、全拒绝、损坏和超限路径 | **通过本机** |
| `multimodal-perception`：确定性视觉指纹 | 近重复抑制、代表帧和对象级聚合指纹 | `test_duplicate_frames_are_retained_but_suppressed_from_admission`；真实视频 8 重复 / 2 代表 | **通过本机** |
| `multimodal-perception`：可观察候选区域 | 候选绑定原视频、帧时间戳和归一化区域 | 自动测试验证证据边；UI 时间轴和 ROI 渲染通过 DOM 烟测 | **通过；人工选视频待现场复演** |
| `multimodal-perception`：VLM 限界与运行追溯 | 只把有限代表帧发往模型；保存节点、模型、输入/输出哈希和降级 | 全拒绝帧不调用模型；守护/降级测试通过 | **通过本机降级；真实 Spark VLM 待 9.4** |
| `local-knowledge-retrieval`：引用与适用边界 | 多帧观察只使用实际返回的版本化本地引用 | 视频闭环与报告测试验证知识快照哈希和引用 | **通过本机** |
| `evidence-chain-reporting`：证据图 | `RawMedia → Frame → Quality/Observation/Fingerprint → Reference/Claim/Report` 可追溯 | 真实媒体报告 29 节点 / 43 边；每帧 `derived_from` 原视频 | **通过本机** |
| `evidence-chain-reporting`：保守结论 | Next Best Observation 先建议低风险补拍，规划仪器不生成测量值 | 近重复与全拒绝视频均先输出补拍，之后才列仪器升级 | **通过本机** |
| `evidence-chain-reporting`：结构化报告 | JSON/HTML 含原视频哈希、帧、质量、指纹、引用、证据、建议、审计 | 报告、哈希、重启持久性及两种下载测试 | **通过本机** |
| `dual-spark-runtime`：最小数据与降级 | Spark B 保存原始视频；Spark A 只接收代表帧 | 跨节点请求记录、错误 key、断网、视觉失联与恢复 | 待双 Spark 实机 |

## 9. 浏览器证据索引

| 编号 | 证据摘要 |
|---|---|
| UI-01 | 首屏免责声明、模式、模型、知识版本和科学边界 |
| UI-02 | 1280 × 720 桌面渲染；建档与媒体入口并列、面板不再被网格强制等高拉伸 |
| UI-03 | 390 × 844 窄屏渲染；核心入口前置、空状态渐进展开、完成态证据图采用可读双列布局 |
| UI-04 | `single-degraded` 显示本机回退，不冒充双 Spark |
| UI-05 | 建档及图片工作台；真实 JPEG 的质量、ROI、SHA-256 与指纹由媒体烟雾调用同一 API 验证 |
| UI-06 | 一键流程返回 3 条本地引用 |
| UI-07 | Raman 失败扣账、重规划、HSI 成功和不确定度变化 |
| UI-08 | 14 节点 P01 证据图、审计链和执行时间线 |
| UI-09 | 保守报告、完整性哈希及 JSON/HTML 下载入口 |
| UI-10 | 无应用脚本异常；Chrome 扩展自身的 message-channel 噪声单独记录 |
| UI-11 | v1.1 图片/视频标签、上传控件、质量、指纹、复拍比较、时间轴和 Next Best Observation 在 DOM/JSDOM 中就绪 |
| UI-12 | 390 × 844 实测 `innerWidth=390`、`scrollWidth=390`，媒体卡转为单列且无横向溢出 |
| MEDIA-01 | `scripts/media-smoke.py` 真实调用 JPEG×2、复拍比较、MP4、10 帧、报告和完整性接口 |
| MEDIA-02 | 重启服务后同一媒体会话、13 个原始/派生文件、报告 ID 与报告哈希仍可读取并复算有效 |

详细记录见 `docs/UI_ACCEPTANCE.md`。

## 10. 部署验证索引与剩余门槛

| 编号 | 当前证据 | 状态 |
|---|---|---|
| DEP-01 | 所有部署脚本与 `run_local.sh` 通过 Bash 语法检查 | 通过 |
| DEP-02 | 双机与单机 Compose 配置均可解析；私网、只读文件系统、降权和健康检查声明存在 | 配置通过，未启动实机容器 |
| DEP-03 | 本地回环、离线、确定性降级模式完成 P01 浏览器流程；媒体 UI 经 DOM/JSDOM 验证，真实 JPEG/MP4 经同一 API 烟雾验证 | 通过本机；人工浏览器选图/选视频待现场一分钟复演 |
| DEP-04 | `deploy/preflight.sh` 已定义 ARM64/Linux、固定私网 IP、密钥权限、模型身份、节点身份及运行健康检查 | 脚本存在；尚未在 Spark 执行 |
| DEP-05 | 两台 Spark 上的请求/响应哈希、A/B 实际节点、断公网业务流量与故障切换 | **任务 9.4 待执行** |

任务 9.4 的通过条件：取得两台 Spark 的固定私网 IP 和操作权限后，在目标 ARM64/Linux 环境运行双节点预检及完整 P01；保存健康响应、模型/服务版本、A/B 运行记录、请求/响应哈希、认证拒绝记录和无公网业务流量证据。完成前不得对外宣称“双 Spark 实机部署已通过”。
