package ai.relicscope.scout.work

import android.content.Context
import androidx.work.BackoffPolicy
import androidx.work.Constraints
import androidx.work.Data
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager
import java.util.concurrent.TimeUnit

object ScoutWorkScheduler {
    const val KEY_LOCAL_JOB_ID = "local_job_id"

    fun enqueueUpload(context: Context, localJobId: String) {
        val request = OneTimeWorkRequestBuilder<UploadScoutJobWorker>()
            .setInputData(jobData(localJobId))
            .setConstraints(connectedNetwork())
            .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, 15, TimeUnit.SECONDS)
            .addTag("scout-upload")
            .addTag("scout-job-$localJobId")
            .build()
        WorkManager.getInstance(context).enqueueUniqueWork(
            "scout-upload-$localJobId",
            ExistingWorkPolicy.KEEP,
            request,
        )
    }

    fun enqueuePoll(context: Context, localJobId: String) {
        val request = OneTimeWorkRequestBuilder<PollScoutJobWorker>()
            .setInputData(jobData(localJobId))
            .setConstraints(connectedNetwork())
            .setBackoffCriteria(BackoffPolicy.LINEAR, 15, TimeUnit.SECONDS)
            .addTag("scout-poll")
            .addTag("scout-job-$localJobId")
            .build()
        WorkManager.getInstance(context).enqueueUniqueWork(
            "scout-poll-$localJobId",
            ExistingWorkPolicy.REPLACE,
            request,
        )
    }

    fun enqueueModelRetry(context: Context, localJobId: String) {
        val request = OneTimeWorkRequestBuilder<RetryScoutJobWorker>()
            .setInputData(jobData(localJobId))
            .setConstraints(connectedNetwork())
            .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, 15, TimeUnit.SECONDS)
            .addTag("scout-model-retry")
            .addTag("scout-job-$localJobId")
            .build()
        WorkManager.getInstance(context).enqueueUniqueWork(
            "scout-model-retry-$localJobId",
            ExistingWorkPolicy.REPLACE,
            request,
        )
    }

    private fun connectedNetwork() = Constraints.Builder()
        .setRequiredNetworkType(NetworkType.CONNECTED)
        .build()

    private fun jobData(localJobId: String) = Data.Builder()
        .putString(KEY_LOCAL_JOB_ID, localJobId)
        .build()
}
