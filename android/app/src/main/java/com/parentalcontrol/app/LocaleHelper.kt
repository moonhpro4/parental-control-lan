package com.parentalcontrol.app

import android.content.Context
import android.content.res.Configuration
import java.util.Locale

object LocaleHelper {
    val SUPPORTED = listOf(
        "en" to "English",
        "fr" to "Français",
        "ar" to "العربية",
        "es" to "Español",
        "de" to "Deutsch",
        "pt" to "Português",
        "tr" to "Türkçe",
        "ru" to "Русский",
    )

    private const val PREFS = "settings"
    private const val KEY = "lang"

    fun saved(context: Context): String? =
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getString(KEY, null)

    fun save(context: Context, lang: String) {
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().putString(KEY, lang).apply()
    }

    /** Call from attachBaseContext() so the whole activity (including its own chrome) uses the chosen language. */
    fun wrap(base: Context): Context {
        val lang = saved(base) ?: return base
        val locale = Locale(lang)
        Locale.setDefault(locale)
        val config = Configuration(base.resources.configuration)
        config.setLocale(locale)
        config.setLayoutDirection(locale)
        return base.createConfigurationContext(config)
    }
}
