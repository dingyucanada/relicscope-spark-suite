package ai.relicscope.scout.model

object OperatorMetadataPresentation {
    fun lines(subjectLabel: String?, operatorNote: String?): List<String> {
        val label = safeLegacyText(subjectLabel).ifBlank { "现场待观察器物" }
        val note = safeLegacyText(operatorNote)
        return buildList {
            add("操作员提供的器物标签（未经验证）")
            add(label)
            if (note.isNotBlank()) {
                add("")
                add("操作员备注（未经验证）")
                add(note)
            }
        }
    }

    /** Prevent old locally cached metadata from injecting line or bidi controls into the report. */
    private fun safeLegacyText(rawValue: String?): String {
        val value = rawValue.orEmpty()
        val output = StringBuilder(value.length)
        var offset = 0
        while (offset < value.length) {
            val codePoint = Character.codePointAt(value, offset)
            val type = Character.getType(codePoint)
            if (type != Character.CONTROL.toInt() && type != Character.FORMAT.toInt()) {
                output.appendCodePoint(codePoint)
            }
            offset += Character.charCount(codePoint)
        }
        return output.toString().trim()
    }
}
