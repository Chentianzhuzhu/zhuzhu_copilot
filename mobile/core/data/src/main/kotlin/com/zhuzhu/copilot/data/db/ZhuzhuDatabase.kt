package com.zhuzhu.copilot.data.db

import androidx.room.Database
import androidx.room.RoomDatabase

@Database(
    entities = [
        ProviderEntity::class,
        ConversationEntity::class,
        MessageEntity::class,
        TaskEntity::class,
        MemoryEntity::class,
    ],
    version = 1,
    exportSchema = true,
)
abstract class ZhuzhuDatabase : RoomDatabase() {
    abstract fun providerDao(): ProviderDao
}
