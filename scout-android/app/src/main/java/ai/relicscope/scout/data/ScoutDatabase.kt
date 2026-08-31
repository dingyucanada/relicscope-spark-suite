package ai.relicscope.scout.data

import android.content.Context
import androidx.room.Database
import androidx.room.Room
import androidx.room.RoomDatabase

@Database(
    entities = [ScoutJobEntity::class, ScoutCaptureEntity::class],
    version = 1,
    exportSchema = true,
)
abstract class ScoutDatabase : RoomDatabase() {
    abstract fun scoutDao(): ScoutDao

    companion object {
        fun create(context: Context): ScoutDatabase = Room.databaseBuilder(
            context.applicationContext,
            ScoutDatabase::class.java,
            "relicscope-scout-v2.db",
        ).build()
    }
}
