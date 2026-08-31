package ai.relicscope.scout.network

import org.junit.Assert.assertEquals
import org.junit.Assert.assertThrows
import org.junit.Test

class ScoutMetadataPolicyTest {
    @Test
    fun trimsOrdinaryOperatorTextAndAllowsAnEmptyNote() {
        assertEquals(
            "现场器物",
            ScoutMetadataPolicy.humanText(" 现场器物 ", "fallback", 120, false),
        )
        assertEquals("", ScoutMetadataPolicy.humanText(null, "", 500, true))
    }

    @Test
    fun rejectsLineAndBidiControls() {
        listOf("标签\n伪装内容", "标签\u202e隐藏方向").forEach { value ->
            assertThrows(IllegalArgumentException::class.java) {
                ScoutMetadataPolicy.humanText(value, "fallback", 120, false)
            }
        }
    }

    @Test
    fun rejectsUnknownPersistedQualityEvidence() {
        assertThrows(IllegalArgumentException::class.java) {
            ScoutMetadataPolicy.quality(
                "scout-android-quality-v1",
                listOf("operator_authenticity_claim"),
            )
        }
        assertThrows(IllegalArgumentException::class.java) {
            ScoutMetadataPolicy.quality("unregistered-quality-v9", emptyList())
        }
    }
}
