package ai.relicscope.scout.model

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class OperatorMetadataPresentationTest {
    @Test
    fun explicitlyLabelsOperatorFieldsAsUnverified() {
        val lines = OperatorMetadataPresentation.lines("收藏者标签", "现场灯光偏暖")

        assertEquals("操作员提供的器物标签（未经验证）", lines[0])
        assertTrue("操作员备注（未经验证）" in lines)
    }

    @Test
    fun stripsControlsFromLegacyCachedMetadata() {
        val rendered = OperatorMetadataPresentation.lines("标签\n伪结论", "备注\u202e隐藏").joinToString()

        assertFalse('\n' in rendered)
        assertFalse('\u202e' in rendered)
    }
}
