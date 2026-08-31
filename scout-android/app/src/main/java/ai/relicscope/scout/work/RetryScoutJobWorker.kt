package ai.relicscope.scout.work

import ai.relicscope.scout.ScoutApplication
import ai.relicscope.scout.data.ScoutJobState
import ai.relicscope.scout.network.RemoteJob
import ai.relicscope.scout.network.ScoutHttpException
import android.content.Context
import androidx.work.CoroutineWorker
import androidx.work.WorkerParameters
import java.io.IOException

/** Requests an in-place retry of a MODEL_UNAVAILABLE Spark job without re-uploading media. */
class RetryScoutJobWorker(
    appContext: Context,
    params: WorkerParameters,
) : CoroutineWorker(appContext, params) {
    override suspend fun doWork(): Result {
        val localJobId = inputData.getString(ScoutWorkScheduler.KEY_LOCAL_JOB_ID)
            ?: return Result.failure()
        val container = (applicationContext as ScoutApplication).container
        val dao = container.database.scoutDao()
        val local = dao.getJob(localJobId) ?: return Result.failure()
        if (local.state != ScoutJobState.RETRY_REQUESTED) return Result.success()
        val serverJobId = local.serverJobId
            ?: return restoreActionRequired(localJobId, "任务缺少 Spark 服务器编号")
        val config = container.secureDeviceConfig.load()
            ?: return restoreActionRequired(localJobId, "设备配置不可用，请重新注册后重试")

        return try {
            resumePolling(localJobId, container.apiClient.retryJob(config, serverJobId))
        } catch (error: ScoutHttpException) {
            when {
                error.statusCode == 409 -> reconcileAfterConflict(localJobId, serverJobId)
                error.statusCode == 401 || error.statusCode == 403 ->
                    restoreActionRequired(localJobId, "设备认证失败，请更新令牌后重试")
                error.isRetryable -> retryWithMessage(localJobId, "Spark 暂时不可用，将自动重试")
                else -> restoreActionRequired(localJobId, "模型重试被拒绝（HTTP ${error.statusCode}）")
            }
        } catch (_: IOException) {
            retryWithMessage(localJobId, "网络中断，将在联网后自动重试")
        } catch (error: Exception) {
            restoreActionRequired(localJobId, error.message ?: "无法重新启动 Spark 分析")
        }
    }

    /**
     * A server retry can succeed immediately before the app process stops. A repeated POST then
     * returns 409, so read the same job and continue polling if Spark already accepted the retry.
     */
    private suspend fun reconcileAfterConflict(localJobId: String, serverJobId: String): Result {
        val container = (applicationContext as ScoutApplication).container
        val config = container.secureDeviceConfig.load()
            ?: return restoreActionRequired(localJobId, "设备配置不可用，请重新注册后重试")
        return try {
            val remote = container.apiClient.getJob(config, serverJobId)
            if (remote.status == "MODEL_UNAVAILABLE") {
                restoreActionRequired(localJobId, "Spark 模型仍不可用，请确认模型服务后重试")
            } else {
                resumePolling(localJobId, remote)
            }
        } catch (error: ScoutHttpException) {
            if (error.statusCode == 401 || error.statusCode == 403) {
                restoreActionRequired(localJobId, "设备认证失败，请更新令牌后重试")
            } else if (error.isRetryable) {
                retryWithMessage(localJobId, "Spark 暂时不可用，将自动重试")
            } else {
                restoreActionRequired(localJobId, "无法核对 Spark 任务（HTTP ${error.statusCode}）")
            }
        } catch (_: IOException) {
            retryWithMessage(localJobId, "网络中断，将在联网后自动重试")
        }
    }

    private suspend fun resumePolling(localJobId: String, remote: RemoteJob): Result {
        val dao = (applicationContext as ScoutApplication).container.database.scoutDao()
        if (
            dao.completeModelRetryRequest(
                localJobId,
                remote.status,
                System.currentTimeMillis(),
            ) != 1
        ) {
            return Result.success()
        }
        ScoutWorkScheduler.enqueuePoll(applicationContext, localJobId)
        return Result.success()
    }

    private suspend fun retryWithMessage(localJobId: String, message: String): Result {
        val dao = (applicationContext as ScoutApplication).container.database.scoutDao()
        dao.compareAndSetState(
            localJobId,
            ScoutJobState.RETRY_REQUESTED,
            ScoutJobState.RETRY_REQUESTED,
            System.currentTimeMillis(),
            message,
        )
        return Result.retry()
    }

    private suspend fun restoreActionRequired(localJobId: String, message: String): Result {
        val dao = (applicationContext as ScoutApplication).container.database.scoutDao()
        dao.compareAndSetState(
            localJobId,
            ScoutJobState.RETRY_REQUESTED,
            ScoutJobState.MODEL_UNAVAILABLE,
            System.currentTimeMillis(),
            message.take(500),
        )
        return Result.failure()
    }
}
