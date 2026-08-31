package ai.relicscope.scout.quality

import org.junit.Assert.assertEquals
import org.junit.Test
import java.io.File

class FileDigestTest {
    @Test
    fun computesStableSha256() {
        val file = File.createTempFile("scout-digest", ".txt")
        try {
            file.writeText("RelicScope")
            assertEquals(
                "d09a3ce1e9696a4ed7ee1077233aa8aa49ff4d1aeea49b5febf1c8dd4a0a647f",
                FileDigest.sha256(file),
            )
        } finally {
            file.delete()
        }
    }
}
