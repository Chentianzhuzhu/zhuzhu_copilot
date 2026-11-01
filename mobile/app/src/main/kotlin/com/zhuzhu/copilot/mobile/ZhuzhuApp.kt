package com.zhuzhu.copilot.mobile

import android.app.Application
import com.zhuzhu.copilot.data.repo.SettingsRepository
import com.zhuzhu.copilot.ui.Appearance
import com.zhuzhu.copilot.ui.AppearanceHolder
import com.zhuzhu.copilot.ui.AppearanceMode
import dagger.hilt.android.HiltAndroidApp
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch
import javax.inject.Inject

@HiltAndroidApp
class ZhuzhuApp : Application() {
    @Inject
    lateinit var settingsRepository: SettingsRepository

    private val appScope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    override fun onCreate() {
        super.onCreate()
        appScope.launch {
            val mode = runCatching { AppearanceMode.valueOf(settingsRepository.appearanceMode()) }
                .getOrDefault(AppearanceMode.DARK)
            val color = settingsRepository.backgroundColor()
            val image = settingsRepository.backgroundImage()
            AppearanceHolder.apply(
                Appearance(mode = mode, customColor = color, backgroundImage = image)
            )
        }
    }
}
