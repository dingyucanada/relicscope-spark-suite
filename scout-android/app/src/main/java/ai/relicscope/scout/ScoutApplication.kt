package ai.relicscope.scout

import ai.relicscope.scout.data.ScoutDatabase
import ai.relicscope.scout.data.ScoutRepository
import ai.relicscope.scout.network.ScoutApiClient
import ai.relicscope.scout.security.SecureDeviceConfig
import ai.relicscope.scout.data.ScoutJobState
import ai.relicscope.scout.work.ScoutWorkScheduler
import android.app.Application
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch

class ScoutApplication : Application() {
    private val applicationScope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    lateinit var container: ScoutContainer
        private set

    override fun onCreate() {
        super.onCreate()
        val database = ScoutDatabase.create(this)
        container = ScoutContainer(
            database = database,
            repository = ScoutRepository(database.scoutDao()),
            secureDeviceConfig = SecureDeviceConfig(this),
            apiClient = ScoutApiClient(),
        )
        reconcileDurableWork()
    }

    /** Room is authoritative; this repairs scheduling after force-stop, reboot, or app upgrade. */
    private fun reconcileDurableWork() {
        applicationScope.launch {
            container.database.scoutDao().getRecoverableJobs().forEach { job ->
                when {
                    job.state == ScoutJobState.RETRY_REQUESTED && job.serverJobId != null ->
                        ScoutWorkScheduler.enqueueModelRetry(this@ScoutApplication, job.id)
                    job.state == ScoutJobState.POLLING && job.serverJobId != null ->
                        ScoutWorkScheduler.enqueuePoll(this@ScoutApplication, job.id)
                    else -> ScoutWorkScheduler.enqueueUpload(this@ScoutApplication, job.id)
                }
            }
        }
    }
}

data class ScoutContainer(
    val database: ScoutDatabase,
    val repository: ScoutRepository,
    val secureDeviceConfig: SecureDeviceConfig,
    val apiClient: ScoutApiClient,
)
