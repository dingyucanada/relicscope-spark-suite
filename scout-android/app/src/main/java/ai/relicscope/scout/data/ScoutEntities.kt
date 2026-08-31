package ai.relicscope.scout.data

import androidx.room.Embedded
import androidx.room.Entity
import androidx.room.ForeignKey
import androidx.room.Index
import androidx.room.PrimaryKey
import androidx.room.Relation

object ScoutJobState {
    const val DRAFT = "DRAFT"
    const val READY = "READY"
    const val UPLOADING = "UPLOADING"
    const val POLLING = "POLLING"
    const val RETRY_REQUESTED = "RETRY_REQUESTED"
    const val SUCCEEDED = "SUCCEEDED"
    const val NEEDS_RECAPTURE = "NEEDS_RECAPTURE"
    const val PARTIAL = "PARTIAL"
    const val MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"
    const val FAILED = "FAILED"

    val terminal = setOf(SUCCEEDED, NEEDS_RECAPTURE, PARTIAL, MODEL_UNAVAILABLE, FAILED)
}

enum class ScoutRetryAction {
    UPLOAD,
    POLL,
    SERVER_MODEL_RETRY,
}

object ScoutRecoveryPolicy {
    fun retryAction(job: ScoutJobEntity, hasCompleteCaptureSet: Boolean): ScoutRetryAction? {
        if (!hasCompleteCaptureSet) return null
        val legacyConfigurationFailure = job.state == ScoutJobState.FAILED &&
            job.errorMessage.orEmpty().let { message ->
                listOf("认证", "配置", "注册", "令牌", "HTTP 401", "HTTP 403")
                    .any(message::contains)
            }
        return when {
            job.state == ScoutJobState.MODEL_UNAVAILABLE && job.serverJobId != null ->
                ScoutRetryAction.SERVER_MODEL_RETRY
            job.state in setOf(ScoutJobState.READY, ScoutJobState.UPLOADING) && job.serverJobId == null ->
                ScoutRetryAction.UPLOAD
            job.state == ScoutJobState.POLLING && job.serverJobId != null ->
                ScoutRetryAction.POLL
            legacyConfigurationFailure && job.serverJobId == null -> ScoutRetryAction.UPLOAD
            legacyConfigurationFailure && job.serverJobId != null -> ScoutRetryAction.POLL
            else -> null
        }
    }
}

@Entity(tableName = "scout_jobs")
data class ScoutJobEntity(
    @PrimaryKey val id: String,
    val createdAtEpochMs: Long,
    val updatedAtEpochMs: Long,
    val state: String = ScoutJobState.DRAFT,
    val serverJobId: String? = null,
    val serverStatus: String? = null,
    val analysisMode: String = "standard",
    val subjectLabel: String? = null,
    val operatorNote: String? = null,
    val resultJson: String? = null,
    val errorMessage: String? = null,
)

@Entity(
    tableName = "scout_captures",
    primaryKeys = ["jobId", "viewCode"],
    foreignKeys = [
        ForeignKey(
            entity = ScoutJobEntity::class,
            parentColumns = ["id"],
            childColumns = ["jobId"],
            onDelete = ForeignKey.CASCADE,
        ),
    ],
    indices = [Index("jobId")],
)
data class ScoutCaptureEntity(
    val jobId: String,
    val viewCode: String,
    val clientCaptureId: String,
    val filename: String,
    val filePath: String,
    val clientSha256: String,
    val capturedAtEpochMs: Long,
    val qualityAlgorithm: String,
    val qualityPassed: Boolean,
    val qualityScore: Double,
    val sharpness: Double,
    val brightnessMean: Double,
    val blackClipRatio: Double,
    val whiteClipRatio: Double,
    val imageWidth: Int,
    val imageHeight: Int,
    val failedChecksCsv: String,
)

data class ScoutJobWithCaptures(
    @Embedded val job: ScoutJobEntity,
    @Relation(parentColumn = "id", entityColumn = "jobId")
    val captures: List<ScoutCaptureEntity>,
)
