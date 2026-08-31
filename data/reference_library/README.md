# 本地瓷器参考样本库

本目录只交付数据契约，**不包含、也不伪造 50 件真实器物或专家结论**。真实图片、来源凭证、授权文件、校准证书、专家审签和假货对照证据必须由数据权属方与项目专家提供，经离线导入后保存在受控运行目录；不得提交到公开仓库。

## Demo 验收硬门

默认导入策略要求：

- `reference_artifacts` 恰好 50 件已审签的真实参考器物；
- `counterfeit_records` 至少 10 件已审签的假货对照记录；该数量仅能证明功能可演示，索引会标记 `counterfeit_coverage=DEMO_LIMITED`，不代表统计有效性；
- 每件器物至少 5 张图片，并覆盖至少 5 个不同的有效视角；`DETAIL`、`MARK`、`DAMAGE` 可补充，但不计入五视角硬门；
- 图片 ID、路径和 SHA-256 全库唯一；图片必须可解码，实际格式、尺寸与清单一致；
- 每条记录都有唯一实物 ID、馆藏/来源凭证、有效许可、专家结论与审签文件；
- 每张图片都有拍摄设备、校准配置、校准证书、色彩参照、尺度参照和灯光信息，且拍摄时间在校准有效期内；
- 假货记录必须指向库内至少一件真实参考器物，并附有已知差异说明和证据文件；负向记录仅用于相似性对照，不能由“相似/不相似”直接推导真伪结论；
- `ACTIVE_DISPUTE`、无 `IDENTITY_MATCHING` 准入的真实参考品、或无 `COUNTERFEIT_CROSS_VALIDATION` 准入的负向记录均拒绝入库。

`manifest.schema.json` 描述 JSON 结构。最终权威校验器是 `app/services/reference_library.py`，它还执行 JSON Schema 无法表达的文件哈希、路径逃逸、符号链接、视角唯一性、跨记录引用、许可日期、校准日期、记录哈希和总清单哈希校验。

## 受控目录建议

首次采集可先生成空白资料工作区：

```bash
make reference-scaffold
```

默认输出到 `runtime/data/reference-library-intake/`，包括 50 件精品、10 条负向案例和每件 5 个标准视角的空白 CSV 采集表与目录。该脚手架被明确标记为 `TEMPLATE/PLACEHOLDER`，故意不能被正式导入；它只帮助团队分配编号、图片和证据文件，所有空白字段必须由数据权属方、拍摄团队与专家填写并审签。

清单中的所有路径都是相对 `--media-root` 的规范 POSIX 路径：

```text
controlled-reference-library/
├── manifest.json
├── images/<physical-object-id>/<view>.jpg
├── sources/<physical-object-id>.<approved-format>
├── reviews/<review-id>.<signed-format>
├── calibrations/<profile-id>.<approved-format>
└── counterfeit-evidence/<physical-object-id>.<approved-format>
```

不要在清单中写绝对路径、`..` 或符号链接。导入器不会联网，也不会自动补齐、猜测或生成任何馆藏、权属、真伪、年代、窑口或专家字段。

## 校验与导入

先只校验，不产生索引：

```bash
.venv/bin/python scripts/import-reference-library.py \
  /approved/controlled-reference-library/manifest.json \
  --media-root /approved/controlled-reference-library \
  --verify-only
```

完整通过后再原子导入本地 SQLite 索引：

```bash
.venv/bin/python scripts/import-reference-library.py \
  /approved/controlled-reference-library/manifest.json \
  --media-root /approved/controlled-reference-library \
  --index /approved/runtime/reference-library.sqlite3
```

任何一个字段、文件、证据或哈希失败都会拒绝整批导入；已有索引不会被部分覆盖。数量策略可以通过命令行显式提高或为独立数据批次调整，实际策略会固化在索引元数据中。默认值仍是 50 件真实参考品、至少 10 件假货对照、每件至少 5 个有效视角。

## 哈希与版本

清单按三层绑定：

1. 文件级 `sha256` / `*_sha256` 绑定图片及所有来源、审签、校准和假货证据；
2. 每条记录的 `record_sha256` 绑定其全部元数据；
3. `content_set_sha256` 绑定记录集合，`manifest_sha256` 绑定完整清单，`version` 必须以 `@<content_set_sha256 前 12 位>` 结尾。

数据准备程序可调用 `seal_manifest(payload)` 计算第 2–3 层哈希；它不会替数据方计算或背书文件级哈希，也不证明元数据陈述真实。

SQLite 元数据索引额外保存诊断指纹算法版本、每张图片的 8 维 `diagnostic_vector` 与质量信息、索引内容哈希、元数据哈希、导入策略和原始 manifest/content 哈希。该 8 维向量仅用于图像质量/基础指纹诊断，**不是实例识别 embedding，也不得送入正式相似检索阈值**。`ReferenceLibraryIndex.metadata()` 默认复算完整索引内容并在不一致时失败。

正式识别 embedding 应使用独立、版本化的索引空间，并逐向量绑定 `image_id`、原图 SHA-256、固定模型来源与 revision、预处理/提示词哈希、向量维度与向量 SHA-256；只有其 manifest、模型、校准和阈值策略全部匹配时才可读取。不要覆盖或复用 `diagnostic_vector` 字段。embedding 构建器应先用 `ReferenceLibraryIndex.iter_images()` 获取受信描述，再通过 `read_image_bytes(image_id)` 复验路径、文件哈希、格式和尺寸后读取原图，不应直接拼接或打开索引里的相对路径。

## 评测边界

入库图片是检索素材，不是独立测试集。准确率、误识别率和阈值校准必须另用按**实物 ID 隔离**、拍摄条件独立、未参与建库或调参的 held-out 正负样本评测；不得用同一批入库图片自匹配后宣称识别准确率。
