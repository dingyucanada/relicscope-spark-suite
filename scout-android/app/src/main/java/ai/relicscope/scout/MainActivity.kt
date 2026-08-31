package ai.relicscope.scout

import ai.relicscope.scout.data.ScoutJobState
import ai.relicscope.scout.data.ScoutJobWithCaptures
import ai.relicscope.scout.data.ScoutRecoveryPolicy
import ai.relicscope.scout.data.ScoutRetryAction
import ai.relicscope.scout.databinding.ActivityMainBinding
import ai.relicscope.scout.model.CaptureView
import ai.relicscope.scout.model.OperatorMetadataPresentation
import ai.relicscope.scout.quality.ImageQualityAssessor
import ai.relicscope.scout.quality.QualitySnapshot
import ai.relicscope.scout.security.DeviceConfig
import ai.relicscope.scout.work.ScoutWorkScheduler
import android.Manifest
import android.content.pm.PackageManager
import android.graphics.Color
import android.os.Bundle
import android.text.InputType
import android.view.View
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.camera.core.CameraSelector
import androidx.camera.core.ImageAnalysis
import androidx.camera.core.ImageCapture
import androidx.camera.core.ImageCaptureException
import androidx.camera.core.Preview
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.core.content.ContextCompat
import androidx.core.view.setPadding
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.repeatOnLifecycle
import com.google.android.material.dialog.MaterialAlertDialogBuilder
import com.google.android.material.snackbar.Snackbar
import com.google.android.material.textfield.TextInputEditText
import com.google.android.material.textfield.TextInputLayout
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors

class MainActivity : AppCompatActivity() {
    private lateinit var binding: ActivityMainBinding
    private lateinit var container: ScoutContainer
    private lateinit var cameraExecutor: ExecutorService
    private var imageCapture: ImageCapture? = null
    private var currentJobId: String? = null
    private var currentAggregate: ScoutJobWithCaptures? = null
    private var currentView: CaptureView = CaptureView.FRONT
    private var lastPreviewAssessmentAt = 0L
    private var jobObserver: Job? = null

    private val cameraPermission = registerForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { granted ->
        if (granted) startCamera() else showCameraPermissionError()
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)
        container = (application as ScoutApplication).container
        cameraExecutor = Executors.newSingleThreadExecutor()

        binding.captureButton.setOnClickListener {
            val terminal = currentAggregate?.job?.state?.let { it in ScoutJobState.terminal } == true
            if (terminal) createNewJob() else captureCurrentView()
        }
        binding.submitButton.setOnClickListener {
            val aggregate = currentAggregate
            when {
                aggregate == null -> Unit
                isRecoverableAction(aggregate) -> retryCurrentJob(aggregate)
                aggregate.job.state in ScoutJobState.terminal -> showResultDialog(aggregate)
                else -> submitCurrentJob()
            }
        }
        binding.configureButton.setOnClickListener { showDeviceConfigDialog() }

        lifecycleScope.launch {
            currentJobId = savedInstanceState?.getString(STATE_JOB_ID)
                ?: container.repository.resumeOrCreateDraft()
            beginObserving(requireNotNull(currentJobId))
        }

        if (ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED) {
            startCamera()
        } else {
            cameraPermission.launch(Manifest.permission.CAMERA)
        }
    }

    override fun onSaveInstanceState(outState: Bundle) {
        currentJobId?.let { outState.putString(STATE_JOB_ID, it) }
        super.onSaveInstanceState(outState)
    }

    override fun onDestroy() {
        cameraExecutor.shutdown()
        super.onDestroy()
    }

    private fun beginObserving(jobId: String) {
        jobObserver?.cancel()
        jobObserver = lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.STARTED) {
                container.repository.observeJob(jobId).collect { aggregate ->
                    currentAggregate = aggregate
                    aggregate?.let(::renderJob)
                }
            }
        }
    }

    private fun renderJob(aggregate: ScoutJobWithCaptures) {
        val accepted = aggregate.captures.filter { it.qualityPassed }.map { it.viewCode }.toSet()
        currentView = CaptureView.required.firstOrNull { it.code !in accepted } ?: CaptureView.BASE
        val capturedAll = CaptureView.required.all { it.code in accepted }
        val draft = aggregate.job.state == ScoutJobState.DRAFT

        binding.viewStepText.text = if (capturedAll) {
            "05 / 05 · 采集完成"
        } else {
            "%02d / 05".format(accepted.size + 1)
        }
        binding.viewTitleText.text = currentView.displayName
        binding.viewInstructionText.text = if (capturedAll) {
            "五个规定视角均已通过端侧质量门。提交后即使离线，系统也会保留任务并自动重试。"
        } else {
            currentView.instruction
        }
        binding.captureButton.isEnabled = draft && !capturedAll
        binding.captureButton.text = getString(R.string.capture)
        binding.submitButton.isEnabled = draft && capturedAll
        binding.jobStateText.text = statusText(aggregate)

        if (
            aggregate.job.state == ScoutJobState.MODEL_UNAVAILABLE ||
            (aggregate.job.state == ScoutJobState.FAILED && isRecoverableAction(aggregate))
        ) {
            binding.captureButton.isEnabled = true
            binding.captureButton.text = "新建采集"
            binding.submitButton.isEnabled = isRecoverableAction(aggregate)
            binding.submitButton.text = "用原照片重试"
        } else if (aggregate.job.state in ScoutJobState.terminal) {
            binding.captureButton.isEnabled = true
            binding.captureButton.text = "新建采集"
            binding.submitButton.isEnabled = aggregate.job.resultJson != null
            binding.submitButton.text = "查看结构化结果"
        } else if (!draft) {
            binding.captureButton.isEnabled = false
            val actionRequired = isRecoverableAction(aggregate)
            binding.submitButton.isEnabled = actionRequired
            binding.submitButton.text = when {
                actionRequired && aggregate.job.state == ScoutJobState.POLLING -> "继续同步"
                actionRequired -> "用原照片重试"
                aggregate.job.state == ScoutJobState.READY || aggregate.job.state == ScoutJobState.UPLOADING -> "等待上传"
                aggregate.job.state == ScoutJobState.POLLING -> "Spark 分析中"
                aggregate.job.state == ScoutJobState.RETRY_REQUESTED -> "正在重启模型任务"
                else -> "任务已结束"
            }
        }
    }

    private fun statusText(aggregate: ScoutJobWithCaptures): String {
        val error = aggregate.job.errorMessage?.takeIf { it.isNotBlank() }
        return when (aggregate.job.state) {
            ScoutJobState.DRAFT -> "现场采集 · 已安全保存 ${aggregate.captures.size}/5"
            ScoutJobState.READY -> error ?: "已进入本地上传队列"
            ScoutJobState.UPLOADING -> error ?: "正在加密连接 Spark…"
            ScoutJobState.POLLING -> "Spark ${aggregate.job.serverStatus ?: "QUEUED"} · ${error ?: "分析任务已接收"}"
            ScoutJobState.RETRY_REQUESTED -> error ?: "正在请求 Spark 使用原任务重新分析"
            ScoutJobState.SUCCEEDED -> "结构化观察报告已返回并保存在本机"
            ScoutJobState.PARTIAL -> "部分分析完成，请按报告建议复核"
            ScoutJobState.NEEDS_RECAPTURE -> "服务器质量复核要求补拍"
            ScoutJobState.MODEL_UNAVAILABLE -> "采集已保存，但 Spark 本地模型暂不可用"
            ScoutJobState.FAILED -> error ?: "任务失败，请检查配置后重试"
            else -> aggregate.job.state
        }
    }

    private fun startCamera() {
        val providerFuture = ProcessCameraProvider.getInstance(this)
        providerFuture.addListener(
            {
                val provider = providerFuture.get()
                val preview = Preview.Builder().build().also {
                    it.surfaceProvider = binding.previewView.surfaceProvider
                }
                imageCapture = ImageCapture.Builder()
                    .setCaptureMode(ImageCapture.CAPTURE_MODE_MAXIMIZE_QUALITY)
                    .setJpegQuality(95)
                    .build()
                val analysis = ImageAnalysis.Builder()
                    .setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST)
                    .build()
                    .also { stream ->
                        stream.setAnalyzer(cameraExecutor) { image ->
                            try {
                                val now = System.currentTimeMillis()
                                if (now - lastPreviewAssessmentAt >= 350) {
                                    lastPreviewAssessmentAt = now
                                    val quality = ImageQualityAssessor.assessPreview(image)
                                    runOnUiThread { renderLiveQuality(quality) }
                                }
                            } catch (_: Exception) {
                                // A dropped preview-quality frame must never interrupt capture.
                            } finally {
                                image.close()
                            }
                        }
                    }
                provider.unbindAll()
                provider.bindToLifecycle(
                    this,
                    CameraSelector.DEFAULT_BACK_CAMERA,
                    preview,
                    imageCapture,
                    analysis,
                )
            },
            ContextCompat.getMainExecutor(this),
        )
    }

    private fun renderLiveQuality(quality: QualitySnapshot) {
        binding.liveQualityText.text = when {
            "underexposed" in quality.failedChecks -> "光线不足"
            "overexposed" in quality.failedChecks -> "光线过强"
            "shadow_clipping" in quality.failedChecks -> "暗部丢失"
            "highlight_clipping" in quality.failedChecks -> "高光反射"
            "not_sharp" in quality.failedChecks -> "请保持稳定"
            else -> "质量良好"
        }
        binding.liveQualityText.setTextColor(
            ContextCompat.getColor(
                this,
                if (quality.passed) R.color.scout_green else R.color.scout_warning,
            ),
        )
    }

    private fun captureCurrentView() {
        val jobId = currentJobId ?: return
        val capture = imageCapture ?: return
        binding.captureButton.isEnabled = false
        binding.captureButton.text = "正在质检…"

        val directory = File(filesDir, "captures/$jobId").apply { mkdirs() }
        val file = File(directory, "${currentView.code.lowercase()}-${System.currentTimeMillis()}.jpg")
        val output = ImageCapture.OutputFileOptions.Builder(file).build()
        capture.takePicture(
            output,
            ContextCompat.getMainExecutor(this),
            object : ImageCapture.OnImageSavedCallback {
                override fun onImageSaved(outputFileResults: ImageCapture.OutputFileResults) {
                    lifecycleScope.launch {
                        var saved = false
                        val quality = withContext(Dispatchers.Default) {
                            ImageQualityAssessor.assessJpeg(file)
                        }
                        if (!quality.passed) {
                            withContext(Dispatchers.IO) { file.delete() }
                            showQualityFailure(quality)
                        } else {
                            runCatching {
                                container.repository.saveCapture(jobId, currentView, file, quality)
                                saved = true
                            }.onFailure { error ->
                                Snackbar.make(binding.root, error.message ?: "保存失败", Snackbar.LENGTH_LONG).show()
                            }
                        }
                        binding.captureButton.text = getString(R.string.capture)
                        if (!saved) binding.captureButton.isEnabled = true
                    }
                }

                override fun onError(exception: ImageCaptureException) {
                    binding.captureButton.text = getString(R.string.capture)
                    binding.captureButton.isEnabled = true
                    Snackbar.make(binding.root, "拍摄失败，请重试", Snackbar.LENGTH_LONG).show()
                }
            },
        )
    }

    private fun showQualityFailure(quality: QualitySnapshot) {
        val reasons = quality.failedChecks.map { failure ->
            when (failure) {
                "resolution_too_low" -> "分辨率不足"
                "underexposed" -> "光线不足"
                "overexposed" -> "曝光过强"
                "shadow_clipping" -> "暗部细节丢失"
                "highlight_clipping" -> "反光或高光溢出"
                "not_sharp" -> "画面不够清晰"
                else -> "图像无法读取"
            }
        }.distinct().joinToString("、")
        Snackbar.make(binding.root, "$reasons，请保持机位并重拍。", Snackbar.LENGTH_LONG).show()
    }

    private fun submitCurrentJob() {
        val jobId = currentJobId ?: return
        if (container.secureDeviceConfig.load() == null) {
            showDeviceConfigDialog(afterSave = { submitCurrentJob() })
            return
        }
        lifecycleScope.launch {
            runCatching {
                container.repository.markReady(jobId)
                ScoutWorkScheduler.enqueueUpload(this@MainActivity, jobId)
            }.onFailure { error ->
                Snackbar.make(binding.root, error.message ?: "无法提交", Snackbar.LENGTH_LONG).show()
            }
        }
    }

    private fun isRecoverableAction(aggregate: ScoutJobWithCaptures): Boolean {
        val accepted = aggregate.captures.filter { it.qualityPassed }.map { it.viewCode }.toSet()
        val capturedAll = CaptureView.required.all { it.code in accepted }
        return ScoutRecoveryPolicy.retryAction(aggregate.job, capturedAll) != null &&
            (
                aggregate.job.state == ScoutJobState.MODEL_UNAVAILABLE ||
                    aggregate.job.state == ScoutJobState.FAILED ||
                    !aggregate.job.errorMessage.isNullOrBlank()
                )
    }

    private fun retryCurrentJob(aggregate: ScoutJobWithCaptures) {
        val configurationProblem = aggregate.job.errorMessage.orEmpty().let { message ->
            "认证" in message || "配置" in message || "注册" in message || "令牌" in message ||
                "HTTP 401" in message || "HTTP 403" in message
        }
        if (container.secureDeviceConfig.load() == null || configurationProblem) {
            showDeviceConfigDialog(afterSave = { performRetry(aggregate.job.id) })
        } else {
            performRetry(aggregate.job.id)
        }
    }

    private fun performRetry(jobId: String) {
        lifecycleScope.launch {
            runCatching { container.repository.prepareRetry(jobId) }
                .onSuccess { action ->
                    when (action) {
                        ScoutRetryAction.UPLOAD -> ScoutWorkScheduler.enqueueUpload(this@MainActivity, jobId)
                        ScoutRetryAction.POLL -> ScoutWorkScheduler.enqueuePoll(this@MainActivity, jobId)
                        ScoutRetryAction.SERVER_MODEL_RETRY ->
                            ScoutWorkScheduler.enqueueModelRetry(this@MainActivity, jobId)
                    }
                    Snackbar.make(binding.root, "原始五视角照片已保留，任务正在恢复", Snackbar.LENGTH_SHORT).show()
                }
                .onFailure { error ->
                    Snackbar.make(binding.root, error.message ?: "无法恢复任务", Snackbar.LENGTH_LONG).show()
                }
        }
    }

    private fun showDeviceConfigDialog(afterSave: (() -> Unit)? = null) {
        val existing = container.secureDeviceConfig.load()
        val content = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(24))
        }
        val baseUrl = inputField(content, "Spark HTTPS 地址", existing?.baseUrl ?: "https://scout.spark.local:8443")
        val deviceId = inputField(content, "设备编号", existing?.deviceId.orEmpty())
        val token = inputField(content, "设备令牌", existing?.bearerToken.orEmpty(), password = true)

        val dialog = MaterialAlertDialogBuilder(this)
            .setTitle("注册 Scout 设备")
            .setMessage("凭据使用 Android Keystore 加密保存；客户端只接受 HTTPS。")
            .setView(content)
            .setNegativeButton("取消", null)
            .setPositiveButton("安全保存", null)
            .create()
        dialog.setOnShowListener {
            dialog.getButton(android.app.AlertDialog.BUTTON_POSITIVE).setOnClickListener {
                runCatching {
                    container.secureDeviceConfig.save(
                        DeviceConfig(
                            baseUrl = baseUrl.text.toString(),
                            deviceId = deviceId.text.toString(),
                            bearerToken = token.text.toString(),
                        ),
                    )
                }.onSuccess {
                    dialog.dismiss()
                    Toast.makeText(this, "设备配置已加密保存", Toast.LENGTH_SHORT).show()
                    afterSave?.invoke()
                }.onFailure { error ->
                    baseUrl.error = error.message ?: "配置无效"
                }
            }
        }
        dialog.show()
    }

    private fun inputField(
        parent: LinearLayout,
        label: String,
        value: String,
        password: Boolean = false,
    ): EditText {
        val editText = TextInputEditText(this).apply {
            setText(value)
            setTextColor(Color.WHITE)
            inputType = if (password) {
                InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_PASSWORD
            } else {
                InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_URI
            }
        }
        val wrapper = TextInputLayout(this).apply {
            hint = label
            if (password) endIconMode = TextInputLayout.END_ICON_PASSWORD_TOGGLE
            addView(editText)
        }
        parent.addView(
            wrapper,
            LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT).apply {
                bottomMargin = dp(8)
            },
        )
        return editText
    }

    private fun showCameraPermissionError() {
        binding.captureButton.isEnabled = false
        binding.liveQualityText.text = getString(R.string.camera_permission_required)
        binding.captureGuide.visibility = View.INVISIBLE
    }

    private fun showResultDialog(aggregate: ScoutJobWithCaptures) {
        val raw = aggregate.job.resultJson ?: return
        val reportText = runCatching { conciseResult(JSONObject(raw)) }
            .getOrElse { "结果已安全保存，但当前版本无法格式化这份报告。\n\n服务器状态：${aggregate.job.serverStatus}" }
        val report = TextView(this).apply {
            text = reportText
            setTextColor(ContextCompat.getColor(this@MainActivity, R.color.scout_text))
            textSize = 15f
            setLineSpacing(0f, 1.25f)
            setPadding(dp(24))
            setTextIsSelectable(true)
        }
        val scroll = ScrollView(this).apply { addView(report) }
        MaterialAlertDialogBuilder(this)
            .setTitle("Spark 结构化观察")
            .setView(scroll)
            .setNegativeButton("新建采集") { _, _ -> createNewJob() }
            .setPositiveButton("完成", null)
            .show()
    }

    private fun conciseResult(root: JSONObject): String {
        val result = root.getJSONObject("result")
        val assessment = result.optJSONObject("capture_assessment")
        val lines = mutableListOf<String>()
        val operatorMetadata = result.optJSONObject("operator_metadata")
        lines += OperatorMetadataPresentation.lines(
            subjectLabel = operatorMetadata?.optString("subject_label")
                ?.takeIf { it.isNotBlank() }
                ?: result.optString("subject_label", "现场待观察器物"),
            operatorNote = operatorMetadata?.optString("operator_note")
                ?.takeIf { it.isNotBlank() },
        )
        if (assessment != null) {
            lines += "图像复核  ${assessment.optInt("accepted")}/${assessment.optInt("total")} 通过"
        }
        appendObservationObjects(lines, "可见特征", result.optJSONArray("visible_observations"), 10)
        appendStrings(lines, "跨视角观察", result.optJSONArray("cross_view_observations"), 6)
        appendStrings(lines, "局限", result.optJSONArray("model_limitations"), 5)
        appendStrings(lines, "下一步", result.optJSONArray("next_actions"), 5)
        val boundary = result.optString("boundary")
        if (boundary.isNotBlank()) lines += "\n边界\n$boundary"
        return lines.joinToString("\n")
    }

    private fun appendObservationObjects(
        lines: MutableList<String>,
        title: String,
        values: JSONArray?,
        limit: Int,
    ) {
        if (values == null || values.length() == 0) return
        lines += "\n$title"
        for (index in 0 until minOf(values.length(), limit)) {
            val item = values.optJSONObject(index) ?: continue
            val viewCode = item.optString("view_code")
            val text = item.optString("text")
            if (text.isNotBlank()) lines += "• ${if (viewCode.isBlank()) "" else "[$viewCode] "}$text"
        }
    }

    private fun appendStrings(
        lines: MutableList<String>,
        title: String,
        values: JSONArray?,
        limit: Int,
    ) {
        if (values == null || values.length() == 0) return
        lines += "\n$title"
        for (index in 0 until minOf(values.length(), limit)) {
            values.optString(index).takeIf { it.isNotBlank() }?.let { lines += "• $it" }
        }
    }

    private fun createNewJob() {
        lifecycleScope.launch {
            val newJobId = container.repository.createDraft()
            currentJobId = newJobId
            currentAggregate = null
            beginObserving(newJobId)
            Snackbar.make(binding.root, "已建立新的五视角采集任务", Snackbar.LENGTH_SHORT).show()
        }
    }

    private fun dp(value: Int): Int = (value * resources.displayMetrics.density).toInt()

    companion object {
        private const val STATE_JOB_ID = "state_job_id"
    }
}
