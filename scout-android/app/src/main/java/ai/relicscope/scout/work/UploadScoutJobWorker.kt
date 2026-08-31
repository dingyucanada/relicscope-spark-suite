package ai.relicscope.scout.work

import ai.relicscope.scout.ScoutApplication
import ai.relicscope.scout.data.ScoutJobState
import ai.relicscope.scout.network.ScoutHttpException
import android.content.Context
import androidx.work.CoroutineWorker
import androidx.work.WorkerParameters
import java.io.IOException

class UploadScoutJobWorker(
    appContext: Context,
    params: WorkerParameters,
) : CoroutineWorker(appContext, params) {
    override suspend fun doWork(): Result {
        val localJobId = inputData.getString(ScoutWorkScheduler.KEY_LOCAL_JOB_ID)
            ?: return Result.failure()
        val container = (applicationContext as ScoutApplication).container
        val dao = container.database.scoutDao()
        val aggregate = dao.getJobWithCaptures(localJobId) ?: return Result.failure()
        if (aggregate.job.state in ScoutJobState.terminal) return Result.success()
        val config = container.secureDeviceConfig.load()
            ?: return parkForAction(localJobId, "设备尚未注册或安全配置无法读取")

        if (
            dao.compareAndSetState(
                localJobId,
                aggregate.job.state,
                ScoutJobState.UPLOADING,
                System.currentTimeMillis(),
                null,
            ) != 1
        ) return Result.success()
        return try {
            val response = container.apiClient.createJob(config, aggregate)
            if (
                dao.completeUpload(
                    localJobId,
                    response.job.id,
                    response.job.status,
                    System.currentTimeMillis(),
                ) != 1
            ) return Result.success()
            ScoutWorkScheduler.enqueuePoll(applicationContext, localJobId)
            Result.success()
        } catch (error: ScoutHttpException) {
            when {
                error.statusCode == 401 || error.statusCode == 403 ->
                    parkForAction(localJobId, "设备认证失败，请更新令牌后使用原照片重试")
                error.isRetryable -> retry(localJobId, "服务器暂时不可用（HTTP ${error.statusCode}）")
                else -> fail(localJobId, "上传被拒绝（HTTP ${error.statusCode}）")
            }
        } catch (error: IOException) {
            retry(localJobId, "网络中断，将在联网后自动重试")
        } catch (error: Exception) {
            fail(localJobId, error.message ?: "无法提交任务")
        }
    }

    private suspend fun retry(localJobId: String, message: String): Result {
        val dao = (applicationContext as ScoutApplication).container.database.scoutDao()
        dao.getJob(localJobId)?.let {
            dao.compareAndSetState(
                localJobId,
                it.state,
                it.state,
                System.currentTimeMillis(),
                message,
            )
        }
        return Result.retry()
    }

    private suspend fun parkForAction(localJobId: String, message: String): Result {
        val dao = (applicationContext as ScoutApplication).container.database.scoutDao()
        dao.getJob(localJobId)?.let {
            if (it.state !in ScoutJobState.terminal) {
                dao.compareAndSetState(
                    localJobId,
                    it.state,
                    ScoutJobState.READY,
                    System.currentTimeMillis(),
                    message.take(500),
                )
            }
        }
        return Result.failure()
    }

    private suspend fun fail(localJobId: String, message: String): Result {
        val dao = (applicationContext as ScoutApplication).container.database.scoutDao()
        dao.getJob(localJobId)?.let {
            if (it.state !in ScoutJobState.terminal) {
                dao.compareAndSetState(
                    localJobId,
                    it.state,
                    ScoutJobState.FAILED,
                    System.currentTimeMillis(),
                    message.take(500),
                )
            }
        }
        return Result.failure()
    }
}
