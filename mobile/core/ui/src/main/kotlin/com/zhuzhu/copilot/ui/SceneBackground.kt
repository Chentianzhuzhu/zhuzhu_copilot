package com.zhuzhu.copilot.ui

import android.content.Context
import android.graphics.BitmapFactory
import android.net.Uri
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.produceState
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import dev.chrisbanes.haze.HazeState
import dev.chrisbanes.haze.hazeSource

/**
 * 背景：纯色（深色 / 亮色 / 自定义色），可选用户自定义图片。
 * 不使用任何内置渐变；有图片时玻璃的模糊与折射才有真实内容可采样。
 */
@Composable
fun SceneBackground(hazeState: HazeState, modifier: Modifier = Modifier) {
    val colors = LocalAppColors.current
    val appearance by AppearanceHolder.state.collectAsState()
    val context = LocalContext.current

    Box(
        modifier
            .fillMaxSize()
            .background(colors.background)
            .hazeSource(hazeState),
    ) {
        if (appearance.backgroundImage.isNotEmpty()) {
            val bitmap by produceState<ImageBitmap?>(null, appearance.backgroundImage) {
                value = withContext(Dispatchers.IO) {
                    decodeSampled(context, appearance.backgroundImage)
                }
            }
            bitmap?.let { image ->
                Image(
                    bitmap = image,
                    contentDescription = null,
                    modifier = Modifier.fillMaxSize(),
                    contentScale = ContentScale.Crop,
                )
                Box(
                    Modifier
                        .fillMaxSize()
                        .background(Color.Black.copy(alpha = if (colors.isLight) 0.14f else 0.34f))
                )
            }
        }
    }
}

private fun decodeSampled(context: Context, uriText: String): ImageBitmap? {
    return runCatching {
        val uri = Uri.parse(uriText)
        val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        context.contentResolver.openInputStream(uri)?.use { BitmapFactory.decodeStream(it, null, bounds) }
        var sample = 1
        while (bounds.outWidth / sample > 1600 || bounds.outHeight / sample > 1600) sample *= 2
        val options = BitmapFactory.Options().apply { inSampleSize = sample }
        context.contentResolver.openInputStream(uri)?.use { stream ->
            BitmapFactory.decodeStream(stream, null, options)?.asImageBitmap()
        }
    }.getOrNull()
}
