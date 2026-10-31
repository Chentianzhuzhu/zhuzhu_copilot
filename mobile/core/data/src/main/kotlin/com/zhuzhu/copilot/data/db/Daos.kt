package com.zhuzhu.copilot.data.db

import androidx.room.Dao
import androidx.room.Query
import androidx.room.Upsert

@Dao
interface ProviderDao {
    @Query("SELECT * FROM provider")
    suspend fun all(): List<ProviderEntity>

    @Query("SELECT * FROM provider WHERE active = 1 LIMIT 1")
    suspend fun active(): ProviderEntity?

    @Upsert
    suspend fun upsert(entity: ProviderEntity)

    @Query("UPDATE provider SET active = 0")
    suspend fun clearActive()
}
