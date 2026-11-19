package net.aniello.dailyreports

import android.content.Context
import java.io.File
import java.io.PrintWriter
import java.io.StringWriter
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * Startup diagnostics (added in v1.2 to hunt the on-device launch fault
 * that cannot be reproduced in the build sandbox).
 *
 * - Records a phase trail ("app starting", "ui built", "creds ok", ...)
 * - Installs an uncaught-exception handler that saves the stack trace
 *   before the process dies, so the NEXT launch can show exactly what
 *   failed, on screen, where Luddy can read it back.
 * Everything is best-effort and wrapped: diagnostics must never be the
 * thing that breaks the app.
 */
object Diag {
    private const val LOG_FILE = "diag.log"
    private const val CRASH_FILE = "last-crash.txt"
    private var installed = false

    private fun stamp(): String =
        SimpleDateFormat("yyyy-MM-dd HH:mm:ss", Locale.US).format(Date())

    private fun append(context: Context, name: String, text: String) {
        try {
            File(context.filesDir, name).appendText(text)
        } catch (e: Exception) { /* ignore */ }
    }

    fun phase(context: Context, what: String) {
        append(context, LOG_FILE, "${stamp()}  $what\n")
    }

    fun record(context: Context, where: String, t: Throwable) {
        val sw = StringWriter()
        t.printStackTrace(PrintWriter(sw))
        val text = "${stamp()}  $where\n$sw"
        append(context, LOG_FILE, text + "\n")
        try {
            File(context.filesDir, CRASH_FILE).writeText(text)
        } catch (e: Exception) { /* ignore */ }
    }

    /** One-line summary of the last recorded failure, or null. */
    fun lastCrashSummary(context: Context): String? {
        return try {
            val f = File(context.filesDir, CRASH_FILE)
            if (!f.exists()) null else {
                val lines = f.readText().lineSequence().filter { it.isNotBlank() }.take(3).toList()
                if (lines.isEmpty()) null else lines.joinToString("\n")
            }
        } catch (e: Exception) { null }
    }

    /** The full last recorded failure text, or null. */
    fun lastCrashFull(context: Context): String? {
        return try {
            val f = File(context.filesDir, CRASH_FILE)
            if (!f.exists()) null else f.readText().ifBlank { null }
        } catch (e: Exception) { null }
    }

    fun clearCrash(context: Context) {
        try { File(context.filesDir, CRASH_FILE).delete() } catch (e: Exception) { /* ignore */ }
    }

    /** The tail of the running log, for the Settings Diagnostics view. */
    fun logTail(context: Context, maxChars: Int = 9000): String {
        return try {
            val f = File(context.filesDir, LOG_FILE)
            if (!f.exists()) "(no diagnostics recorded yet)" else {
                val text = f.readText()
                if (text.length <= maxChars) text else "…" + text.takeLast(maxChars)
            }
        } catch (e: Exception) {
            "(diagnostics unavailable)"
        }
    }

    fun clearLog(context: Context) {
        try { File(context.filesDir, LOG_FILE).delete() } catch (e: Exception) { /* ignore */ }
    }

    fun install(context: Context) {
        if (installed) return
        installed = true
        val appContext = context.applicationContext
        phase(appContext, "app starting (v" + BuildConfig.VERSION_NAME + ")")
        val previous = Thread.getDefaultUncaughtExceptionHandler()
        Thread.setDefaultUncaughtExceptionHandler { thread, throwable ->
            record(appContext, "UNCAUGHT on ${thread.name}", throwable)
            previous?.uncaughtException(thread, throwable)
        }
    }
}
