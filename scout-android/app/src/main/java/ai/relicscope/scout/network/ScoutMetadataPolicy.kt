package ai.relicscope.scout.network

import ai.relicscope.scout.quality.QUALITY_ALGORITHM_ID
import ai.relicscope.scout.quality.QUALITY_FAILED_CHECK_IDS

object ScoutMetadataPolicy {
    fun humanText(
        rawValue: String?,
        fallback: String,
        maxLength: Int,
        allowBlank: Boolean,
    ): String {
        val value = rawValue ?: fallback
        require(!containsControlOrFormat(value)) {
            "Control and Unicode format characters are not allowed in operator metadata"
        }
        val normalized = value.trim()
        require(allowBlank || normalized.isNotBlank()) { "Operator metadata must not be blank" }
        require(normalized.length <= maxLength) { "Operator metadata is too long" }
        return normalized
    }

    fun quality(algorithm: String, failedChecks: List<String>) {
        require(algorithm == QUALITY_ALGORITHM_ID) { "Unsupported Scout quality algorithm" }
        require(failedChecks.size == failedChecks.distinct().size) {
            "Scout quality failure codes must be unique"
        }
        require(failedChecks.all { it in QUALITY_FAILED_CHECK_IDS }) {
            "Unknown Scout quality failure code"
        }
    }

    private fun containsControlOrFormat(value: String): Boolean {
        var offset = 0
        while (offset < value.length) {
            val codePoint = Character.codePointAt(value, offset)
            val type = Character.getType(codePoint)
            if (type == Character.CONTROL.toInt() || type == Character.FORMAT.toInt()) {
                return true
            }
            offset += Character.charCount(codePoint)
        }
        return false
    }
}
