package ai.relicscope.scout.data

import androidx.room.Dao
import androidx.room.Insert
import androidx.room.OnConflictStrategy
import androidx.room.Query
import androidx.room.Transaction
import androidx.room.Update
import kotlinx.coroutines.flow.Flow

@Dao
interface ScoutDao {
    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun insertJob(job: ScoutJobEntity)

    @Update
    suspend fun updateJob(job: ScoutJobEntity)

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun upsertCapture(capture: ScoutCaptureEntity)

    @Transaction
    @Query("SELECT * FROM scout_jobs WHERE id = :jobId")
    fun observeJob(jobId: String): Flow<ScoutJobWithCaptures?>

    @Transaction
    @Query("SELECT * FROM scout_jobs WHERE id = :jobId")
    suspend fun getJobWithCaptures(jobId: String): ScoutJobWithCaptures?

    @Query("SELECT * FROM scout_jobs WHERE id = :jobId")
    suspend fun getJob(jobId: String): ScoutJobEntity?

    @Query("SELECT * FROM scout_jobs WHERE state IN ('DRAFT', 'READY', 'UPLOADING', 'POLLING', 'RETRY_REQUESTED', 'MODEL_UNAVAILABLE', 'FAILED') ORDER BY createdAtEpochMs DESC LIMIT 1")
    suspend fun getLatestOpenJob(): ScoutJobEntity?

    @Query("SELECT * FROM scout_jobs WHERE state IN ('READY', 'UPLOADING', 'POLLING', 'RETRY_REQUESTED') ORDER BY createdAtEpochMs")
    suspend fun getRecoverableJobs(): List<ScoutJobEntity>

    @Query(
        """
        UPDATE scout_jobs
        SET state = :newState, updatedAtEpochMs = :updatedAtEpochMs, errorMessage = :errorMessage
        WHERE id = :jobId AND state = :expectedState
        """,
    )
    suspend fun compareAndSetState(
        jobId: String,
        expectedState: String,
        newState: String,
        updatedAtEpochMs: Long,
        errorMessage: String?,
    ): Int

    @Query(
        """
        UPDATE scout_jobs
        SET state = 'RETRY_REQUESTED', resultJson = NULL, errorMessage = NULL,
            updatedAtEpochMs = :updatedAtEpochMs
        WHERE id = :jobId AND state = 'MODEL_UNAVAILABLE' AND serverJobId IS NOT NULL
        """,
    )
    suspend fun requestModelRetry(jobId: String, updatedAtEpochMs: Long): Int

    @Query(
        """
        UPDATE scout_jobs
        SET state = 'POLLING', serverStatus = :serverStatus, errorMessage = NULL,
            updatedAtEpochMs = :updatedAtEpochMs
        WHERE id = :jobId AND state = 'RETRY_REQUESTED'
        """,
    )
    suspend fun completeModelRetryRequest(
        jobId: String,
        serverStatus: String,
        updatedAtEpochMs: Long,
    ): Int

    @Query(
        """
        UPDATE scout_jobs
        SET state = 'POLLING', serverJobId = :serverJobId, serverStatus = :serverStatus,
            errorMessage = NULL, updatedAtEpochMs = :updatedAtEpochMs
        WHERE id = :jobId AND state = 'UPLOADING'
        """,
    )
    suspend fun completeUpload(
        jobId: String,
        serverJobId: String,
        serverStatus: String,
        updatedAtEpochMs: Long,
    ): Int

    @Query(
        """
        UPDATE scout_jobs
        SET serverStatus = :serverStatus, errorMessage = :errorMessage,
            updatedAtEpochMs = :updatedAtEpochMs
        WHERE id = :jobId AND state = 'POLLING'
        """,
    )
    suspend fun updatePollingStatus(
        jobId: String,
        serverStatus: String?,
        errorMessage: String?,
        updatedAtEpochMs: Long,
    ): Int

    @Query(
        """
        UPDATE scout_jobs
        SET state = :terminalState, serverStatus = :serverStatus, resultJson = :resultJson,
            errorMessage = :errorMessage, updatedAtEpochMs = :updatedAtEpochMs
        WHERE id = :jobId AND state = 'POLLING'
        """,
    )
    suspend fun completePolling(
        jobId: String,
        terminalState: String,
        serverStatus: String,
        resultJson: String?,
        errorMessage: String?,
        updatedAtEpochMs: Long,
    ): Int

    @Query("SELECT * FROM scout_jobs ORDER BY createdAtEpochMs DESC LIMIT 20")
    fun observeRecentJobs(): Flow<List<ScoutJobEntity>>

    @Query("DELETE FROM scout_captures WHERE jobId = :jobId AND viewCode = :viewCode")
    suspend fun deleteCapture(jobId: String, viewCode: String)
}
