package ai.relicscope.scout.data

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class ScoutRecoveryPolicyTest {
    @Test
    fun modelUnavailableRetriesTheSameServerJob() {
        val job = job(ScoutJobState.MODEL_UNAVAILABLE, "spark-job-1")

        assertEquals(
            ScoutRetryAction.SERVER_MODEL_RETRY,
            ScoutRecoveryPolicy.retryAction(job, hasCompleteCaptureSet = true),
        )
    }

    @Test
    fun authenticationFailuresResumeAtTheCorrectTransportStage() {
        assertEquals(
            ScoutRetryAction.UPLOAD,
            ScoutRecoveryPolicy.retryAction(job(ScoutJobState.READY), hasCompleteCaptureSet = true),
        )
        assertEquals(
            ScoutRetryAction.POLL,
            ScoutRecoveryPolicy.retryAction(
                job(ScoutJobState.POLLING, "spark-job-1"),
                hasCompleteCaptureSet = true,
            ),
        )
    }

    @Test
    fun pausedPollingRemainsResumableAfterAutomaticWaitLimit() {
        val paused = job(ScoutJobState.POLLING, "spark-job-1").copy(
            errorMessage = "本轮自动等待已暂停；Spark 任务仍保留，可点击继续同步",
        )

        assertEquals(
            ScoutRetryAction.POLL,
            ScoutRecoveryPolicy.retryAction(paused, hasCompleteCaptureSet = true),
        )
    }

    @Test
    fun neverRetriesWithoutTheOriginalCompleteCaptureSet() {
        assertNull(
            ScoutRecoveryPolicy.retryAction(
                job(ScoutJobState.MODEL_UNAVAILABLE, "spark-job-1"),
                hasCompleteCaptureSet = false,
            ),
        )
    }

    @Test
    fun recoversAuthenticationFailuresWrittenByAnEarlierClientVersion() {
        assertEquals(
            ScoutRetryAction.UPLOAD,
            ScoutRecoveryPolicy.retryAction(
                job(ScoutJobState.FAILED).copy(errorMessage = "上传被拒绝（HTTP 401）"),
                hasCompleteCaptureSet = true,
            ),
        )
        assertEquals(
            ScoutRetryAction.POLL,
            ScoutRecoveryPolicy.retryAction(
                job(ScoutJobState.FAILED, "spark-job-1").copy(errorMessage = "设备配置无法读取"),
                hasCompleteCaptureSet = true,
            ),
        )
    }

    private fun job(state: String, serverJobId: String? = null) = ScoutJobEntity(
        id = "local-job-1",
        createdAtEpochMs = 1L,
        updatedAtEpochMs = 1L,
        state = state,
        serverJobId = serverJobId,
    )
}
