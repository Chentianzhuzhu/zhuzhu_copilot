package com.zhuzhu.copilot.data.db

import androidx.room.Entity
import androidx.room.PrimaryKey

@Entity(tableName = "provider")
data class ProviderEntity(
    @PrimaryKey val id: String,
    val name: String,
    val baseUrl: String,
    val model: String,
    val isBuiltin: Boolean,
    val active: Boolean,
)

@Entity(tableName = "conversation")
data class ConversationEntity(
    @PrimaryKey val id: String,
    val title: String,
    val createdAt: Long,
    val updatedAt: Long,
)

@Entity(tableName = "message")
data class MessageEntity(
    @PrimaryKey val id: String,
    val conversationId: String,
    val role: String,
    val content: String,
    val createdAt: Long,
)

@Entity(tableName = "task")
data class TaskEntity(
    @PrimaryKey val id: String,
    val title: String,
    val status: String,
    val createdAt: Long,
    val failureReason: String,
)

@Entity(tableName = "memory")
data class MemoryEntity(
    @PrimaryKey val id: String,
    val content: String,
    val createdAt: Long,
    val lastUsedAt: Long,
)
