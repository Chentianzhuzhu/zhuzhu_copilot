package com.zhuzhu.copilot.data.repo

import android.content.Context
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.longPreferencesKey
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import kotlinx.coroutines.flow.first

private val Context.settingsStore by preferencesDataStore("settings")

/** 应用级设置：外观、确认模式、语言等。全部走 DataStore，键名集中在此。 */
class SettingsRepository(private val context: Context) {
    private val appearanceModeKey = stringPreferencesKey("appearance_mode")
    private val backgroundColorKey = longPreferencesKey("background_color")
    private val backgroundImageKey = stringPreferencesKey("background_image")
    private val confirmModeKey = stringPreferencesKey("confirm_mode")
    private val languageKey = stringPreferencesKey("language")

    suspend fun appearanceMode(): String =
        context.settingsStore.data.first()[appearanceModeKey] ?: "DARK"

    suspend fun setAppearanceMode(value: String) =
        context.settingsStore.edit { it[appearanceModeKey] = value }

    suspend fun backgroundColor(): Long =
        context.settingsStore.data.first()[backgroundColorKey] ?: 0xFF000000L

    suspend fun setBackgroundColor(value: Long) =
        context.settingsStore.edit { it[backgroundColorKey] = value }

    suspend fun backgroundImage(): String =
        context.settingsStore.data.first()[backgroundImageKey] ?: ""

    suspend fun setBackgroundImage(value: String) =
        context.settingsStore.edit { it[backgroundImageKey] = value }

    suspend fun confirmMode(): String =
        context.settingsStore.data.first()[confirmModeKey] ?: "SENSITIVE_AND_ABOVE"

    suspend fun setConfirmMode(value: String) =
        context.settingsStore.edit { it[confirmModeKey] = value }

    suspend fun language(): String =
        context.settingsStore.data.first()[languageKey] ?: "zh"

    suspend fun setLanguage(value: String) =
        context.settingsStore.edit { it[languageKey] = value }
}
