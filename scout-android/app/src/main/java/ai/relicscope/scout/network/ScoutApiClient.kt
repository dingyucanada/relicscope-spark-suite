package ai.relicscope.scout.network

import ai.relicscope.scout.BuildConfig
import ai.relicscope.scout.data.ScoutJobWithCaptures
import ai.relicscope.scout.security.DeviceConfig
import android.os.Build
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.MultipartBody
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.asRequestBody
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.time.Instant
import java.util.concurrent.TimeUnit

data class RemoteJob(
    val id: String,
    val status: String,
    val stage: String,
    val resultAvailable: Boolean,
    val errorCode: String?,
)

data class CreateJobResponse(
    val created: Boolean,
    val job: RemoteJob,
)

data class ResultResponse(
    val ready: Boolean,
    val job: RemoteJob,
    val bodyJson: String,
)

class ScoutHttpException(
    val statusCode: Int,
    val responseBody: String,
) : Exception("Scout server returned HTTP $statusCode") {
    val isRetryable: Boolean
        get() = statusCode == 408 || statusCode == 429 || statusCode >= 500
}

class ScoutApiClient(
    private val httpClient: OkHttpClient = OkHttpClient.Builder()
        .connectTimeout(20, TimeUnit.SECONDS)
        .readTimeout(90, TimeUnit.SECONDS)
        .writeTimeout(120, TimeUnit.SECONDS)
        .callTimeout(150, TimeUnit.SECONDS)
        .retryOnConnectionFailure(true)
        .build(),
) {
    fun createJob(config: DeviceConfig, aggregate: ScoutJobWithCaptures): CreateJobResponse {
        val metadata = metadataJson(aggregate)
        val multipart = MultipartBody.Builder()
            .setType(MultipartBody.FORM)
            .addFormDataPart(
                "metadata_json",
                null,
                metadata.toString().toRequestBody(JSON_MEDIA_TYPE),
            )

        aggregate.captures.sortedBy { it.viewCode }.forEach { capture ->
            val file = File(capture.filePath)
            check(file.isFile) { "Capture is missing: ${capture.filename}" }
            multipart.addFormDataPart(
                "files",
                capture.filename,
                file.asRequestBody(JPEG_MEDIA_TYPE),
            )
        }

        val request = authenticatedRequest(
            config,
            "${config.baseUrl}/api/v2/scout/jobs",
        ).post(multipart.build()).build()

        val json = executeJson(request)
        return CreateJobResponse(
            created = json.optBoolean("created", false),
            job = parseRemoteJob(json.getJSONObject("job")),
        )
    }

    fun getJob(config: DeviceConfig, serverJobId: String): RemoteJob {
        val request = authenticatedRequest(
            config,
            "${config.baseUrl}/api/v2/scout/jobs/$serverJobId",
        ).get().build()
        return parseRemoteJob(executeJson(request))
    }

    fun getResult(config: DeviceConfig, serverJobId: String): ResultResponse {
        val request = authenticatedRequest(
            config,
            "${config.baseUrl}/api/v2/scout/jobs/$serverJobId/result",
        ).get().build()
        httpClient.newCall(request).execute().use { response ->
            val rawBody = response.body?.string().orEmpty()
            if (response.code !in 200..299) throw ScoutHttpException(response.code, rawBody.take(2_000))
            val json = JSONObject(rawBody)
            return ResultResponse(
                ready = response.code == 200 && !json.isNull("result"),
                job = parseRemoteJob(json.getJSONObject("job")),
                bodyJson = rawBody,
            )
        }
    }

    fun retryJob(config: DeviceConfig, serverJobId: String): RemoteJob {
        val request = authenticatedRequest(
            config,
            "${config.baseUrl}/api/v2/scout/jobs/$serverJobId/retry",
        ).post(EMPTY_BODY).build()
        return parseRemoteJob(executeJson(request))
    }

    private fun executeJson(request: Request): JSONObject =
        httpClient.newCall(request).execute().use { response ->
            val rawBody = response.body?.string().orEmpty()
            if (!response.isSuccessful) throw ScoutHttpException(response.code, rawBody.take(2_000))
            JSONObject(rawBody)
        }

    private fun authenticatedRequest(config: DeviceConfig, url: String): Request.Builder =
        Request.Builder()
            .url(url)
            .header("X-Scout-Device-ID", config.deviceId)
            .header("Authorization", "Bearer ${config.bearerToken}")
            .header("Accept", "application/json")
            .header("User-Agent", "RelicScope-Scout/${BuildConfig.VERSION_NAME} Android/${Build.VERSION.SDK_INT}")

    companion object {
        private val JSON_MEDIA_TYPE = "application/json; charset=utf-8".toMediaType()
        private val JPEG_MEDIA_TYPE = "image/jpeg".toMediaType()
        private val EMPTY_BODY = ByteArray(0).toRequestBody(null)

        internal fun metadataJson(aggregate: ScoutJobWithCaptures): JSONObject {
            val captures = JSONArray()
            aggregate.captures.sortedBy { it.viewCode }.forEach { capture ->
                val failureCodes = capture.failedChecksCsv.split(',').filter { it.isNotBlank() }
                ScoutMetadataPolicy.quality(capture.qualityAlgorithm, failureCodes)
                val failedChecks = JSONArray()
                failureCodes.forEach(failedChecks::put)
                captures.put(
                    JSONObject()
                        .put("client_capture_id", capture.clientCaptureId)
                        .put("filename", capture.filename)
                        .put("client_sha256", capture.clientSha256)
                        .put("view_code", capture.viewCode)
                        .put("captured_at", Instant.ofEpochMilli(capture.capturedAtEpochMs).toString())
                        .put(
                            "device_quality",
                            JSONObject()
                                .put("algorithm", capture.qualityAlgorithm)
                                .put("passed", capture.qualityPassed)
                                .put("blur_score", capture.sharpness)
                                .put("brightness_mean", capture.brightnessMean)
                                .put("failed_checks", failedChecks),
                        ),
                )
            }
            val subjectLabel = ScoutMetadataPolicy.humanText(
                aggregate.job.subjectLabel,
                fallback = "现场待观察器物",
                maxLength = 120,
                allowBlank = false,
            )
            val operatorNote = ScoutMetadataPolicy.humanText(
                aggregate.job.operatorNote,
                fallback = "",
                maxLength = 500,
                allowBlank = true,
            )
            return JSONObject()
                .put("schema_version", "relicscope-scout-job-v2")
                .put("client_job_id", aggregate.job.id)
                .put("capture_protocol", "porcelain-v1")
                .put("analysis_mode", aggregate.job.analysisMode)
                .put("subject_label", subjectLabel)
                .put("operator_note", operatorNote)
                .put("app_version", BuildConfig.VERSION_NAME)
                .put("device_model", "${Build.MANUFACTURER} ${Build.MODEL}".trim())
                .put("captures", captures)
        }

        private fun parseRemoteJob(json: JSONObject) = RemoteJob(
            id = json.getString("id"),
            status = json.getString("status"),
            stage = json.optString("stage", "UNKNOWN"),
            resultAvailable = json.optBoolean("result_available", false),
            errorCode = json.optString("error_code").takeIf { it.isNotBlank() && it != "null" },
        )
    }
}
