package ai.relicscope.scout.quality

import android.graphics.BitmapFactory
import androidx.camera.core.ImageProxy
import java.io.File
import kotlin.math.max
import kotlin.math.min

const val QUALITY_ALGORITHM_ID = "scout-android-quality-v1"
val QUALITY_FAILED_CHECK_IDS = setOf(
    "resolution_too_low",
    "underexposed",
    "overexposed",
    "shadow_clipping",
    "highlight_clipping",
    "not_sharp",
    "decode_failed",
)

data class QualityThresholds(
    val minLongEdge: Int = 1_200,
    val minShortEdge: Int = 900,
    val minBrightness: Double = 45.0,
    val maxBrightness: Double = 215.0,
    val maxBlackClipRatio: Double = 0.12,
    val maxWhiteClipRatio: Double = 0.12,
    val minSharpness: Double = 120.0,
)

data class QualitySnapshot(
    val algorithm: String = QUALITY_ALGORITHM_ID,
    val passed: Boolean,
    val sharpness: Double,
    val brightnessMean: Double,
    val blackClipRatio: Double,
    val whiteClipRatio: Double,
    val width: Int,
    val height: Int,
    val failedChecks: List<String>,
) {
    init {
        require(algorithm == QUALITY_ALGORITHM_ID) { "Unsupported Scout quality algorithm" }
        require(failedChecks.size == failedChecks.distinct().size) {
            "Scout quality failure codes must be unique"
        }
        require(failedChecks.all { it in QUALITY_FAILED_CHECK_IDS }) {
            "Unknown Scout quality failure code"
        }
    }

    val qualityScore: Double
        get() {
            val exposure = 1.0 - min(1.0, kotlin.math.abs(brightnessMean - 130.0) / 130.0)
            val clipping = 1.0 - min(1.0, (blackClipRatio + whiteClipRatio) / 0.24)
            val detail = min(1.0, sharpness / 320.0)
            return (0.35 * exposure + 0.25 * clipping + 0.40 * detail).coerceIn(0.0, 1.0)
        }
}

object ImageQualityAssessor {
    fun assessJpeg(
        file: File,
        thresholds: QualityThresholds = QualityThresholds(),
    ): QualitySnapshot {
        val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        BitmapFactory.decodeFile(file.absolutePath, bounds)
        if (bounds.outWidth <= 0 || bounds.outHeight <= 0) {
            return invalid("decode_failed")
        }

        var sampleSize = 1
        while (max(bounds.outWidth, bounds.outHeight) / sampleSize > 1_024) {
            sampleSize *= 2
        }
        val options = BitmapFactory.Options().apply {
            inSampleSize = sampleSize
            inPreferredConfig = android.graphics.Bitmap.Config.ARGB_8888
        }
        val bitmap = BitmapFactory.decodeFile(file.absolutePath, options)
            ?: return invalid("decode_failed")
        return try {
            val width = bitmap.width
            val height = bitmap.height
            val argb = IntArray(width * height)
            bitmap.getPixels(argb, 0, width, 0, 0, width, height)
            val luma = IntArray(argb.size) { index ->
                val pixel = argb[index]
                val red = pixel shr 16 and 0xff
                val green = pixel shr 8 and 0xff
                val blue = pixel and 0xff
                ((77 * red + 150 * green + 29 * blue) shr 8)
            }
            evaluateLuma(
                luma = luma,
                width = width,
                height = height,
                sourceWidth = bounds.outWidth,
                sourceHeight = bounds.outHeight,
                thresholds = thresholds,
            )
        } finally {
            bitmap.recycle()
        }
    }

    /** Fast live check over CameraX's Y plane. The captured JPEG is assessed again before acceptance. */
    fun assessPreview(
        image: ImageProxy,
        thresholds: QualityThresholds = QualityThresholds(minLongEdge = 1, minShortEdge = 1),
    ): QualitySnapshot {
        val plane = image.planes.first()
        val buffer = plane.buffer.duplicate()
        val rowStride = plane.rowStride
        val pixelStride = plane.pixelStride
        val width = image.width
        val height = image.height
        val luma = IntArray(width * height)
        for (row in 0 until height) {
            val rowOffset = row * rowStride
            for (column in 0 until width) {
                luma[row * width + column] = buffer.get(rowOffset + column * pixelStride).toInt() and 0xff
            }
        }
        return evaluateLuma(luma, width, height, width, height, thresholds)
    }

    fun evaluateLuma(
        luma: IntArray,
        width: Int,
        height: Int,
        sourceWidth: Int = width,
        sourceHeight: Int = height,
        thresholds: QualityThresholds = QualityThresholds(),
    ): QualitySnapshot {
        require(width > 2 && height > 2) { "Image must be at least 3 x 3" }
        require(luma.size >= width * height) { "Luma buffer is smaller than dimensions" }

        var sum = 0L
        var black = 0
        var white = 0
        for (index in 0 until width * height) {
            val value = luma[index].coerceIn(0, 255)
            sum += value
            if (value <= 5) black++
            if (value >= 250) white++
        }

        var laplaceSum = 0.0
        var laplaceSquareSum = 0.0
        var laplaceCount = 0
        for (row in 1 until height - 1) {
            for (column in 1 until width - 1) {
                val index = row * width + column
                val laplace = (
                    4 * luma[index] -
                        luma[index - 1] -
                        luma[index + 1] -
                        luma[index - width] -
                        luma[index + width]
                    ).toDouble()
                laplaceSum += laplace
                laplaceSquareSum += laplace * laplace
                laplaceCount++
            }
        }

        val pixelCount = width * height
        val brightness = sum.toDouble() / pixelCount
        val laplaceMean = laplaceSum / laplaceCount
        val sharpness = max(0.0, laplaceSquareSum / laplaceCount - laplaceMean * laplaceMean)
        val blackRatio = black.toDouble() / pixelCount
        val whiteRatio = white.toDouble() / pixelCount
        val failures = buildList {
            val longEdge = max(sourceWidth, sourceHeight)
            val shortEdge = min(sourceWidth, sourceHeight)
            if (longEdge < thresholds.minLongEdge || shortEdge < thresholds.minShortEdge) {
                add("resolution_too_low")
            }
            if (brightness < thresholds.minBrightness) add("underexposed")
            if (brightness > thresholds.maxBrightness) add("overexposed")
            if (blackRatio > thresholds.maxBlackClipRatio) add("shadow_clipping")
            if (whiteRatio > thresholds.maxWhiteClipRatio) add("highlight_clipping")
            if (sharpness < thresholds.minSharpness) add("not_sharp")
        }
        return QualitySnapshot(
            passed = failures.isEmpty(),
            sharpness = sharpness,
            brightnessMean = brightness,
            blackClipRatio = blackRatio,
            whiteClipRatio = whiteRatio,
            width = sourceWidth,
            height = sourceHeight,
            failedChecks = failures,
        )
    }

    private fun invalid(reason: String) = QualitySnapshot(
        passed = false,
        sharpness = 0.0,
        brightnessMean = 0.0,
        blackClipRatio = 0.0,
        whiteClipRatio = 0.0,
        width = 0,
        height = 0,
        failedChecks = listOf(reason),
    )
}
