package ai.relicscope.scout.security

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import androidx.core.content.edit
import java.net.URI
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

data class DeviceConfig(
    val baseUrl: String,
    val deviceId: String,
    val bearerToken: String,
)

/**
 * Keeps enrollment data encrypted at rest with a non-exportable Android Keystore key.
 * TLS policy is enforced independently by Network Security Config and by URL validation here.
 */
class SecureDeviceConfig(context: Context) {
    private val preferences = context.getSharedPreferences(PREFERENCES_NAME, Context.MODE_PRIVATE)

    fun save(config: DeviceConfig) {
        val normalizedUrl = normalizeHttpsUrl(config.baseUrl)
        require(config.deviceId.isNotBlank()) { "Device ID is required" }
        require(config.bearerToken.isNotBlank()) { "Bearer token is required" }
        preferences.edit {
            putString(KEY_BASE_URL, encrypt(normalizedUrl))
            putString(KEY_DEVICE_ID, encrypt(config.deviceId.trim()))
            putString(KEY_TOKEN, encrypt(config.bearerToken.trim()))
        }
    }

    fun load(): DeviceConfig? = try {
        val baseUrl = decrypt(preferences.getString(KEY_BASE_URL, null) ?: return null)
        val deviceId = decrypt(preferences.getString(KEY_DEVICE_ID, null) ?: return null)
        val token = decrypt(preferences.getString(KEY_TOKEN, null) ?: return null)
        DeviceConfig(normalizeHttpsUrl(baseUrl), deviceId, token)
    } catch (_: Exception) {
        null
    }

    fun clear() {
        preferences.edit { clear() }
    }

    private fun encrypt(plainText: String): String {
        val cipher = Cipher.getInstance(TRANSFORMATION)
        cipher.init(Cipher.ENCRYPT_MODE, getOrCreateKey())
        val iv = Base64.encodeToString(cipher.iv, Base64.NO_WRAP)
        val ciphertext = Base64.encodeToString(
            cipher.doFinal(plainText.toByteArray(Charsets.UTF_8)),
            Base64.NO_WRAP,
        )
        return "v1:$iv:$ciphertext"
    }

    private fun decrypt(envelope: String): String {
        val parts = envelope.split(':', limit = 3)
        require(parts.size == 3 && parts[0] == "v1") { "Unsupported secure config envelope" }
        val cipher = Cipher.getInstance(TRANSFORMATION)
        val iv = Base64.decode(parts[1], Base64.NO_WRAP)
        cipher.init(Cipher.DECRYPT_MODE, getOrCreateKey(), GCMParameterSpec(128, iv))
        return cipher.doFinal(Base64.decode(parts[2], Base64.NO_WRAP)).toString(Charsets.UTF_8)
    }

    private fun getOrCreateKey(): SecretKey {
        val keyStore = KeyStore.getInstance(ANDROID_KEYSTORE).apply { load(null) }
        (keyStore.getKey(KEY_ALIAS, null) as? SecretKey)?.let { return it }
        val keyGenerator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, ANDROID_KEYSTORE)
        keyGenerator.init(
            KeyGenParameterSpec.Builder(
                KEY_ALIAS,
                KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT,
            )
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .setKeySize(256)
                .build(),
        )
        return keyGenerator.generateKey()
    }

    companion object {
        private const val ANDROID_KEYSTORE = "AndroidKeyStore"
        private const val TRANSFORMATION = "AES/GCM/NoPadding"
        private const val KEY_ALIAS = "relicscope_scout_device_config_v1"
        private const val PREFERENCES_NAME = "secure_device_config"
        private const val KEY_BASE_URL = "base_url"
        private const val KEY_DEVICE_ID = "device_id"
        private const val KEY_TOKEN = "bearer_token"

        fun normalizeHttpsUrl(rawUrl: String): String {
            val normalized = rawUrl.trim().trimEnd('/')
            val uri = URI(normalized)
            require(uri.scheme.equals("https", ignoreCase = true)) { "Only HTTPS endpoints are allowed" }
            require(!uri.host.isNullOrBlank()) { "A valid HTTPS host is required" }
            require(uri.userInfo == null && uri.query == null && uri.fragment == null) {
                "Credentials, query parameters and fragments are not allowed in the server URL"
            }
            require(uri.rawPath.isNullOrEmpty() || uri.rawPath == "/") {
                "The server URL must be an HTTPS origin without a path"
            }
            return normalized
        }
    }
}
