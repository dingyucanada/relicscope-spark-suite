package ai.relicscope.scout.model

enum class CaptureView(
    val code: String,
    val displayName: String,
    val instruction: String,
) {
    FRONT(
        "FRONT",
        "正面 / FRONT",
        "器物居中，完整保留口沿与足部，镜头与器身中线等高。",
    ),
    BACK(
        "BACK",
        "背面 / BACK",
        "原地旋转器物 180°，保持距离、机位和光线不变。",
    ),
    LEFT_PROFILE(
        "LEFT_PROFILE",
        "侧面 / PROFILE",
        "旋转至 90° 侧面，确保器形轮廓与耳、流、柄清晰可见。",
    ),
    TOP(
        "TOP",
        "口沿与内壁 / TOP",
        "从上方垂直拍摄，完整记录口沿、内壁、釉面与器底内部。",
    ),
    BASE(
        "BASE",
        "底足与款识 / BASE",
        "安全翻转或抬起器物，垂直记录圈足、胎体、款识及磨损痕迹。",
    );

    companion object {
        val required = entries.toList()

        fun fromCode(code: String): CaptureView = entries.first { it.code == code }
    }
}
