package ai.relicscope.scout.quality

import org.junit.Assert.assertFalse
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test

class ImageQualityTest {
    @Test
    fun flatImageFailsSharpnessGate() {
        val luma = IntArray(64 * 64) { 128 }

        val result = ImageQualityAssessor.evaluateLuma(
            luma,
            64,
            64,
            thresholds = QualityThresholds(minLongEdge = 1, minShortEdge = 1),
        )

        assertFalse(result.passed)
        assertTrue("not_sharp" in result.failedChecks)
    }

    @Test
    fun checkerboardPassesExposureAndSharpness() {
        val luma = IntArray(64 * 64) { index ->
            val row = index / 64
            val column = index % 64
            if ((row + column) % 2 == 0) 80 else 180
        }

        val result = ImageQualityAssessor.evaluateLuma(
            luma,
            64,
            64,
            thresholds = QualityThresholds(minLongEdge = 1, minShortEdge = 1),
        )

        assertTrue(result.passed)
        assertTrue(result.qualityScore in 0.0..1.0)
    }

    @Test
    fun clippedDarkImageReportsExposureFailures() {
        val luma = IntArray(32 * 32) { 2 }

        val result = ImageQualityAssessor.evaluateLuma(
            luma,
            32,
            32,
            thresholds = QualityThresholds(minLongEdge = 1, minShortEdge = 1),
        )

        assertFalse(result.passed)
        assertTrue("underexposed" in result.failedChecks)
        assertTrue("shadow_clipping" in result.failedChecks)
    }

    @Test
    fun portraitAndLandscapeUseShortAndLongEdgesForResolution() {
        val luma = IntArray(64 * 64) { index -> if (index % 2 == 0) 80 else 180 }

        val portrait = ImageQualityAssessor.evaluateLuma(
            luma,
            64,
            64,
            sourceWidth = 900,
            sourceHeight = 1_200,
        )
        val landscape = ImageQualityAssessor.evaluateLuma(
            luma,
            64,
            64,
            sourceWidth = 1_200,
            sourceHeight = 900,
        )

        assertFalse("resolution_too_low" in portrait.failedChecks)
        assertFalse("resolution_too_low" in landscape.failedChecks)
    }

    @Test
    fun resolutionRejectsAnUndersizedShortEdgeInEitherOrientation() {
        val luma = IntArray(64 * 64) { index -> if (index % 2 == 0) 80 else 180 }

        val result = ImageQualityAssessor.evaluateLuma(
            luma,
            64,
            64,
            sourceWidth = 1_400,
            sourceHeight = 899,
        )

        assertTrue("resolution_too_low" in result.failedChecks)
    }

    @Test
    fun qualityEvidenceRejectsUnregisteredAlgorithmsAndFailureCodes() {
        assertThrows(IllegalArgumentException::class.java) {
            QualitySnapshot(
                algorithm = "operator-claim-v1",
                passed = false,
                sharpness = 0.0,
                brightnessMean = 0.0,
                blackClipRatio = 0.0,
                whiteClipRatio = 0.0,
                width = 900,
                height = 1_200,
                failedChecks = listOf("authenticity_failed"),
            )
        }
    }
}
