package ai.relicscope.scout.work

import ai.relicscope.scout.ScoutApplication
import ai.relicscope.scout.data.ScoutJobEntity
import ai.relicscope.scout.data.ScoutJobState
import ai.relicscope.scout.network.RemoteJob
import ai.relicscope.scout.network.ScoutHttpException
import android.content.Context
import androidx.work.CoroutineWorker
import androidx.work.WorkerParameters
import java.io.IOException

class PollScoutJobWorker(
    appContext: Context,
    params: WorkerParameters,
) : CoroutineWorker(appContext, params) {
    override suspend fun doWork(): Result {
        val localJobId = inputData.getString(ScoutWorkScheduler.KEY_LOCAL_JOB_ID)
            ?: return Result.failure()
        val container = (applicationContext as ScoutApplication).container
        val dao = container.database.scoutDao()
        val local = dao.getJob(localJobId) ?: return Result.failure()
        if (local.state in ScoutJobState.terminal) return Result.success()
        if (local.state != ScoutJobState.POLLING) return Result.success()
        val serverJobId = local.serverJobId ?: return fail(local, "任务缺少 Spark 服务器编号")
        val config = container.secureDeviceConfig.load()
            ?: return parkForAction(local, "设备安全配置无法读取，请重新注册后继续同步")

        return try {
            val remote = container.apiClient.getJob(config, serverJobId)
            dao.updatePollingStatus(
                localJobId,
                remote.status,
                null,
                System.currentTimeMillis(),
            )
            if (remote.status in TERMINAL_STATUSES) {
                completeFromRemote(localJobId, remote, config)
            } else if (runAttemptCount >= MAX_POLL_ATTEMPTS) {
                parkForAction(local, "本轮自动等待已暂停；Spark 任务仍保留，可点击继续同步")
            } else {
                Result.retry()
            }
        } catch (error: ScoutHttpException) {
            when {
                error.statusCode == 401 || error.statusCode == 403 ->
                    parkForAction(local, "设备认证失败，请更新令牌后继续同步")
                error.isRetryable -> retryWithMessage(local, "Spark 暂时不可用，将自动继续同步")
                else -> fail(local, "读取任务状态失败（HTTP ${error.statusCode}）")
            }
        } catch (error: IOException) {
            retryWithMessage(local, "网络中断，将在联网后自动继续同步")
        } catch (error: Exception) {
            fail(local, error.message ?: "读取分析结果失败")
        }
    }

    private suspend fun completeFromRemote(
        localJobId: String,
        remote: RemoteJob,
        config: ai.relicscope.scout.security.DeviceConfig,
    ): Result {
        val container = (applicationContext as ScoutApplication).container
        val dao = container.database.scoutDao()
        if (dao.getJob(localJobId) == null) return Result.failure()
        val resultJson = if (remote.resultAvailable) {
            val result = container.apiClient.getResult(config, remote.id)
            if (!result.ready) return Result.retry()
            result.bodyJson
        } else {
            null
        }
        dao.completePolling(
            localJobId,
            mapTerminalState(remote.status),
            remote.status,
            resultJson,
            remote.errorCode,
            System.currentTimeMillis(),
        )
        return Result.success()
    }

    private suspend fun retryWithMessage(local: ScoutJobEntity, message: String): Result {
        val dao = (applicationContext as ScoutApplication).container.database.scoutDao()
        dao.updatePollingStatus(
            local.id,
            local.serverStatus,
            message,
            System.currentTimeMillis(),
        )
        return Result.retry()
    }

    private suspend fun parkForAction(local: ScoutJobEntity, message: String): Result {
        val dao = (applicationContext as ScoutApplication).container.database.scoutDao()
        dao.updatePollingStatus(
            local.id,
            local.serverStatus,
            message.take(500),
            System.currentTimeMillis(),
        )
        return Result.failure()
    }

    private suspend fun fail(local: ScoutJobEntity, message: String): Result {
        val dao = (applicationContext as ScoutApplication).container.database.scoutDao()
        dao.compareAndSetState(
            local.id,
            ScoutJobState.POLLING,
            ScoutJobState.FAILED,
            System.currentTimeMillis(),
            message.take(500),
        )
        return Result.failure()
    }

    companion object {
        private const val MAX_POLL_ATTEMPTS = 120
        private val TERMINAL_STATUSES = setOf(
            "SUCCEEDED",
            "PARTIAL",
            "NEEDS_RECAPTURE",
            "MODEL_UNAVAILABLE",
            "FAILED",
            "CANCELLED",
        )

        private fun mapTerminalState(remoteStatus: String): String = when (remoteStatus) {
            "SUCCEEDED" -> ScoutJobState.SUCCEEDED
            "PARTIAL" -> ScoutJobState.PARTIAL
            "NEEDS_RECAPTURE" -> ScoutJobState.NEEDS_RECAPTURE
            "MODEL_UNAVAILABLE" -> ScoutJobState.MODEL_UNAVAILABLE
            else -> ScoutJobState.FAILED
        }
    }
}
