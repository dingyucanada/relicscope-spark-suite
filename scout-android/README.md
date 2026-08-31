# RelicScope Scout Android V2

这是面向 RelicScope Spark 本地服务器的原生 Android 参考客户端。它把手机定义为“受控采集端”：按五个互补视角引导拍摄，在图片离开手机前执行真实的清晰度、曝光与高光/暗部裁切检查，然后把合格的不可变采集任务交给 Spark 做多模态分析。网络中断不会丢任务或丢图片。

> 当前产品边界：客户端与 Spark 输出的是图像质量复核、可见特征观察和结构化记录，不直接给出真伪、年代、窑口、作者、价值或法律结论。

## 已实现

- 原生 Kotlin + CameraX 后置相机预览与高质量 JPEG 采集；
- `FRONT / BACK / LEFT_PROFILE / TOP / BASE` 五视角逐步引导；
- 两阶段端侧质量门：预览实时提示；保存后基于原始 JPEG 再计算亮度、黑白裁切比例和拉普拉斯方差清晰度，失败图片不会进入上传集；
- 每张合格原图以流式方式计算 SHA-256，并把 `client_sha256` 与图片一同绑定到不可变任务元数据；
- Room 持久化任务、每张图的视角与质量证据、服务器任务编号和返回结果；
- WorkManager 在联网后自动上传并持续轮询，进程退出或重启后仍可继续；
- 认证或设备配置错误会保留原五视角任务，更新配置后可从上传或轮询阶段继续；`MODEL_UNAVAILABLE` 可调用 Spark 的同一任务重试接口，无需重新拍摄或上传；
- OkHttp multipart 对接现有 `POST /api/v2/scout/jobs`，并轮询任务与结果接口；
- 设备地址、设备 ID、令牌由 Android Keystore 非导出 AES-GCM 密钥加密保存；
- release 只信任系统 CA，debug 额外信任用户安装的 Caddy 本地 CA；两个构建均明确禁止明文 HTTP；
- 服务端使用 `client_job_id` 做幂等绑定，同一离线任务重试不会有意创建新任务。

## 数据与任务流

```text
CameraX 五视角采集
        │
        ├─ 实时质量提示
        └─ 原始 JPEG 质量门 ──失败──> 原视角重拍
                     │通过
                     ▼
         Room：DRAFT → READY
                     │
              WorkManager（需要网络）
                     │ HTTPS + 设备令牌
                     ▼
        Spark /api/v2/scout/jobs
                     │
           QUEUED / RUNNING / …
                     │轮询
                     ▼
       结构化 result JSON 保存回 Room
```

Room 中的本地状态为：`DRAFT → READY → UPLOADING → POLLING → SUCCEEDED / PARTIAL / NEEDS_RECAPTURE / MODEL_UNAVAILABLE / FAILED`；原任务模型重试期间会短暂进入 `RETRY_REQUESTED`。Application 启动时会以 Room 为准重新调度中断的上传、轮询或模型重试工作；采集文件位于应用私有目录，不会进入系统相册。认证或配置问题不会把任务变成不可恢复失败，界面会显示“用原照片重试”或“继续同步”。

## 构建

要求：

- JDK 17；
- Android SDK Platform 37 与 Build Tools 36.0.0 或更高；
- 可下载 Google Maven、Maven Central 与 Gradle 发行包的网络；
- Android 8.0（API 26）或更新设备。

使用 Android Studio 打开本目录，选择 JDK 17 后同步。也可在本目录执行：

```bash
./gradlew :app:testDebugUnitTest :app:lintDebug :app:assembleDebug
```

APK 位于 `app/build/outputs/apk/debug/app-debug.apk`。仓库已经包含 Gradle 9.4.1 wrapper 脚本与官方 wrapper JAR；AGP 9.2.1 使用内置 Kotlin，KSP 用于 Room 代码生成。

## Spark 配对与 HTTPS

1. 在主 Spark 仓库根目录创建一个设备配置：

   ```bash
   make v2-enroll \
     SCOUT_NAME="Scout 01" \
     SCOUT_SERVER_URL="https://scout.spark.local:8443" \
     SCOUT_DEVICE_ARGS="--output runtime/provisioning/scout-01.json"
   ```

2. 导出 Caddy 本地 CA：

   ```bash
   make v2-export-ca
   ```

3. 仅在受控 demo Android 设备上，把 `runtime/provisioning/scout-local-ca.crt` 安装为“CA 证书”。不同厂商入口略有差异，通常位于“设置 → 安全与隐私 → 更多安全设置 → 加密与凭据 → 安装证书”。安装 CA 代表该设备信任该机构，请只在专用演示设备上操作。

4. 安装 **debug APK**，打开“配置设备”，填入一次性配对文件中的：

   - `server_url` → Spark HTTPS 地址；
   - `device_id` → 设备编号；
   - `token` → 设备令牌。

5. 确保 `scout.spark.local` 在现场 DNS 中指向主 Spark。Wi‑Fi 与 USB 共享网络都可以；USB 场景指的是 Android USB 网络共享，仍然走同一个 HTTPS API，并非通过 MTP 复制图片。

release APK 不信任用户自行安装的 CA。正式部署应使用公开受信任证书，或通过企业设备管理把机构 CA 作为系统信任部署。不要把 Caddy、Spark API 或 vLLM 端口直接暴露到公网。

## API 契约

上传请求：

- `POST /api/v2/scout/jobs`
- Header：`X-Scout-Device-ID`、`Authorization: Bearer …`
- multipart：一个 `metadata_json` 与 5 个同名 `files` part
- metadata schema：`relicscope-scout-job-v2`
- capture protocol：`porcelain-v1`
- 质量算法标识：`scout-android-quality-v1`

轮询请求：

- `GET /api/v2/scout/jobs/{job_id}`
- `GET /api/v2/scout/jobs/{job_id}/result`

模型恢复请求：

- `POST /api/v2/scout/jobs/{job_id}/retry`
- 仅用于同一任务的 `MODEL_UNAVAILABLE` 恢复；服务端接受后继续轮询，不重复上传五张原图

令牌从不写入日志、Room、Intent 或 WorkManager input；后台任务每次运行时才从 Keystore 加密存储中读取。

## 质量门与校准

保存后的 JPEG 会先缩小一份仅用于计算，分辨率判断仍使用原图尺寸。当前默认门限是工程初值：短边/长边至少约 `900 × 1200`、平均亮度 `45–215`、纯黑/纯白裁切各不超过 `12%`、拉普拉斯方差至少 `120`。这些值能过滤明显模糊或严重曝光失败的图片，但必须用计划采用的 Android 机型、灯光、背景和 50 件样本重新标定；不能把它们当作跨设备科学测量标准。

## 当前未验证边界

2026-09-01 已使用临时 JDK 17、Android SDK 37.0 和 Build Tools 36.0.0 完成 `testDebugUnitTest`、`lintDebug` 与 `assembleDebug`。生成的是开发调试 APK，不是已签名的发布包。当前环境仍没有模拟器和 Android 真机；发布 APK 仍应在正式 Android 构建机上重新生成、签名并完成以下硬件验收：

- CameraX 在目标手机上的对焦、旋转、色彩和内存表现；
- 用户安装 Caddy CA 后的真实 TLS 握手与局域网 DNS；
- 手机被系统杀进程、重启、切换 Wi‑Fi/USB 网络后的完整恢复；
- 5 张大图遇到服务器大小限制时的现场体验；单个 multipart 请求中途失败会以同一 `client_job_id` 整批重试，目前没有分块续传；
- 服务端 `NEEDS_RECAPTURE` 的逐视角补拍交互；当前版本安全地保存状态，但 UI 尚未自动建立补拍任务；
- 设备扫码/MDM 零接触注册；参考版采用人工录入一次性配对数据；
- 端侧瓷器轻量分类器。当前端侧只做采集质量控制，品类、相似性、知识库与多模态推理由 Spark 承担。

发布前至少要在一台目标 Android 设备上执行一次：五视角全流程、飞行模式提交后恢复网络、进程强杀后恢复、错误令牌、错误 CA、Spark 暂停后恢复，以及服务端 `SUCCEEDED / PARTIAL / NEEDS_RECAPTURE` 三种结果验收。

## 独立结构检查

没有 Android 工具链时，可先运行：

```bash
./scripts/check-structure.sh
```

该检查不能替代 Gradle、Lint、单元测试或真机验收。
