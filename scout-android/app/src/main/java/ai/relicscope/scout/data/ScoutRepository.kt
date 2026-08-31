package ai.relicscope.scout.data

import ai.relicscope.scout.model.CaptureView
import ai.relicscope.scout.quality.FileDigest
import ai.relicscope.scout.quality.QualitySnapshot
import kotlinx.coroutines.flow.Flow
import java.io.File
import java.util.UUID

class ScoutRepository(private val dao: ScoutDao) {
    suspend fun resumeOrCreateDraft(): String {
        dao.getLatestOpenJob()?.let { return it.id }
        return createDraft()
    }

    suspend fun createDraft(): String {
        val now = System.currentTimeMillis()
        val id = UUID.randomUUID().toString()
        dao.insertJob(
            ScoutJobEntity(
                id = id,
                createdAtEpochMs = now,
                updatedAtEpochMs = now,
            ),
        )
        return id
    }

    fun observeJob(jobId: String): Flow<ScoutJobWithCaptures?> = dao.observeJob(jobId)

    suspend fun saveCapture(
        jobId: String,
        view: CaptureView,
        file: File,
        quality: QualitySnapshot,
    ) {
        check(quality.passed) { "A failed quality capture cannot enter the upload set" }
        val now = System.currentTimeMillis()
        val existing = dao.getJob(jobId) ?: error("Unknown Scout job")
        check(existing.state == ScoutJobState.DRAFT) { "Job is no longer editable" }
        dao.upsertCapture(
            ScoutCaptureEntity(
                jobId = jobId,
                viewCode = view.code,
                clientCaptureId = UUID.randomUUID().toString(),
                filename = file.name,
                filePath = file.absolutePath,
                clientSha256 = FileDigest.sha256(file),
                capturedAtEpochMs = now,
                qualityAlgorithm = quality.algorithm,
                qualityPassed = quality.passed,
                qualityScore = quality.qualityScore,
                sharpness = quality.sharpness,
                brightnessMean = quality.brightnessMean,
                blackClipRatio = quality.blackClipRatio,
                whiteClipRatio = quality.whiteClipRatio,
                imageWidth = quality.width,
                imageHeight = quality.height,
                failedChecksCsv = quality.failedChecks.joinToString(","),
            ),
        )
        dao.updateJob(existing.copy(updatedAtEpochMs = now, errorMessage = null))
    }

    suspend fun markReady(jobId: String) {
        val aggregate = dao.getJobWithCaptures(jobId) ?: error("Unknown Scout job")
        val acceptedViews = aggregate.captures.filter { it.qualityPassed }.map { it.viewCode }.toSet()
        val missing = CaptureView.required.map { it.code }.filterNot { it in acceptedViews }
        check(missing.isEmpty()) { "Missing accepted views: ${missing.joinToString()}" }
        dao.updateJob(
            aggregate.job.copy(
                state = ScoutJobState.READY,
                updatedAtEpochMs = System.currentTimeMillis(),
                errorMessage = null,
            ),
        )
    }

    suspend fun prepareRetry(jobId: String): ScoutRetryAction {
        val aggregate = dao.getJobWithCaptures(jobId) ?: error("Unknown Scout job")
        val acceptedViews = aggregate.captures.filter { it.qualityPassed }.map { it.viewCode }.toSet()
        val capturedAll = CaptureView.required.all { it.code in acceptedViews }
        val action = ScoutRecoveryPolicy.retryAction(aggregate.job, capturedAll)
            ?: error("This job cannot be retried in place with its current capture set")
        val now = System.currentTimeMillis()
        return when (action) {
            ScoutRetryAction.SERVER_MODEL_RETRY -> {
                check(dao.requestModelRetry(jobId, now) == 1) { "Job state changed before retry" }
                ScoutRetryAction.SERVER_MODEL_RETRY
            }

            ScoutRetryAction.UPLOAD -> {
                check(
                    dao.compareAndSetState(
                        jobId,
                        aggregate.job.state,
                        ScoutJobState.READY,
                        now,
                        null,
                    ) == 1,
                ) { "Job state changed before retry" }
                ScoutRetryAction.UPLOAD
            }

            ScoutRetryAction.POLL -> {
                check(
                    dao.compareAndSetState(
                        jobId,
                        aggregate.job.state,
                        ScoutJobState.POLLING,
                        now,
                        null,
                    ) == 1,
                ) { "Job state changed before retry" }
                ScoutRetryAction.POLL
            }
        }
    }
}
