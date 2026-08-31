package ai.relicscope.scout.security

import org.junit.Assert.assertEquals
import org.junit.Assert.assertThrows
import org.junit.Test

class SecureDeviceConfigTest {
    @Test
    fun normalizesHttpsEndpoint() {
        assertEquals(
            "https://scout.spark.local:8443",
            SecureDeviceConfig.normalizeHttpsUrl(" https://scout.spark.local:8443/ "),
        )
    }

    @Test
    fun rejectsCleartextEndpoint() {
        assertThrows(IllegalArgumentException::class.java) {
            SecureDeviceConfig.normalizeHttpsUrl("http://192.168.1.20:8000")
        }
    }

    @Test
    fun rejectsEmbeddedCredentials() {
        assertThrows(IllegalArgumentException::class.java) {
            SecureDeviceConfig.normalizeHttpsUrl("https://operator:secret@scout.spark.local")
        }
    }

    @Test
    fun acceptsOnlyAnHttpsOriginWithoutApplicationPath() {
        assertEquals(
            "https://scout.spark.local:8443",
            SecureDeviceConfig.normalizeHttpsUrl("https://scout.spark.local:8443/"),
        )
        assertThrows(IllegalArgumentException::class.java) {
            SecureDeviceConfig.normalizeHttpsUrl("https://scout.spark.local:8443/api/v2/scout")
        }
    }
}
