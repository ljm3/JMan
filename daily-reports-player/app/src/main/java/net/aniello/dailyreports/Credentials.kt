package net.aniello.dailyreports

import android.content.Context
import android.content.SharedPreferences
import android.util.Base64
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey

/**
 * Stores the estate dashboard credentials in app-private storage only.
 *
 * Primary store: EncryptedSharedPreferences (Android Keystore master key).
 * Every access — creation AND reads/writes — is guarded: some devices
 * (notably Samsung Galaxy models) can return a keystore / encrypted-prefs
 * failure when the store is READ back in a later process, and an unguarded
 * throw there crashed the app on launch in v1.0 once credentials existed.
 *
 * If the encrypted store fails at any point, the poisoned file is deleted
 * and the app permanently (for this install) falls back to a plain
 * private-mode SharedPreferences file. Worst case after a failure is one
 * fresh sign-in prompt — never a crash loop.
 * The password is never hardcoded anywhere in this app.
 */
object Credentials {
    private const val FILE = "credentials"
    private const val PLAIN_FILE = "credentials_plain"
    private const val META_FILE = "credentials_meta"
    private const val KEY_FALLBACK = "fell_back_to_plain"
    private const val KEY_USER = "username"
    private const val KEY_PASS = "password"

    @Volatile private var fellBack: Boolean? = null

    private fun meta(context: Context): SharedPreferences =
        context.getSharedPreferences(META_FILE, Context.MODE_PRIVATE)

    private fun usePlain(context: Context): Boolean {
        fellBack?.let { return it }
        val v = try { meta(context).getBoolean(KEY_FALLBACK, false) } catch (e: Exception) { false }
        fellBack = v
        return v
    }

    private fun markFallback(context: Context) {
        fellBack = true
        try { meta(context).edit().putBoolean(KEY_FALLBACK, true).apply() } catch (e: Exception) { /* ignore */ }
        // Remove the poisoned encrypted file so it can never throw again.
        try { context.deleteSharedPreferences(FILE) } catch (e: Exception) { /* ignore */ }
    }

    private fun encryptedPrefs(context: Context): SharedPreferences? {
        return try {
            val masterKey = MasterKey.Builder(context)
                .setKeyScheme(MasterKey.KeyScheme.AES256_GCM)
                .build()
            EncryptedSharedPreferences.create(
                context, FILE, masterKey,
                EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
                EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM
            )
        } catch (e: Exception) {
            null
        }
    }

    private fun plainPrefs(context: Context): SharedPreferences =
        context.getSharedPreferences(PLAIN_FILE, Context.MODE_PRIVATE)

    private fun readString(context: Context, key: String): String? {
        if (!usePlain(context)) {
            val enc = encryptedPrefs(context)
            if (enc != null) {
                try {
                    return enc.getString(key, null)
                } catch (e: Exception) {
                    markFallback(context)
                }
            } else {
                markFallback(context)
            }
        }
        return try { plainPrefs(context).getString(key, null) } catch (e: Exception) { null }
    }

    private fun writeStrings(context: Context, user: String?, pass: String?) {
        if (!usePlain(context)) {
            val enc = encryptedPrefs(context)
            if (enc != null) {
                try {
                    val ed = enc.edit()
                    if (user == null) ed.remove(KEY_USER) else ed.putString(KEY_USER, user)
                    if (pass == null) ed.remove(KEY_PASS) else ed.putString(KEY_PASS, pass)
                    ed.apply()
                    return
                } catch (e: Exception) {
                    markFallback(context)
                }
            } else {
                markFallback(context)
            }
        }
        try {
            val ed = plainPrefs(context).edit()
            if (user == null) ed.remove(KEY_USER) else ed.putString(KEY_USER, user)
            if (pass == null) ed.remove(KEY_PASS) else ed.putString(KEY_PASS, pass)
            ed.apply()
        } catch (e: Exception) { /* nothing more we can do */ }
    }

    fun save(context: Context, username: String, password: String) =
        writeStrings(context, username, password)

    fun username(context: Context): String? = readString(context, KEY_USER)
    fun password(context: Context): String? = readString(context, KEY_PASS)

    fun hasCredentials(context: Context): Boolean =
        !username(context).isNullOrEmpty() && !password(context).isNullOrEmpty()

    fun clear(context: Context) {
        writeStrings(context, null, null)
        try { plainPrefs(context).edit().clear().apply() } catch (e: Exception) { /* ignore */ }
    }

    /** "Basic <base64(user:pass)>" for the Authorization header, or null. */
    fun authHeader(context: Context): String? {
        val u = username(context)
        val p = password(context)
        if (u.isNullOrEmpty() || p.isNullOrEmpty()) return null
        val raw = "$u:$p".toByteArray(Charsets.UTF_8)
        return "Basic " + Base64.encodeToString(raw, Base64.NO_WRAP)
    }
}
