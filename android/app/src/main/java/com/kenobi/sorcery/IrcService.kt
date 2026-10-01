package com.kenobi.sorcery

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.IBinder

/** Keeps the process (and so the IRC connection) alive while Sorcery is in the background. */
class IrcService : Service() {

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) {
            stopForeground(STOP_FOREGROUND_REMOVE)
            stopSelf()
            return START_NOT_STICKY
        }
        createChannels(this)
        startForeground(ONGOING_ID, ongoing(), ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE)
        return START_NOT_STICKY
    }

    private fun ongoing(): Notification =
        Notification.Builder(this, CHANNEL_CONNECTION)
            .setSmallIcon(R.drawable.ic_notify)
            .setContentTitle("Sorcery")
            .setContentText("Connected to IRC")
            .setContentIntent(openApp(this, null))
            .setOngoing(true)
            .build()

    companion object {
        private const val CHANNEL_CONNECTION = "connection"
        private const val CHANNEL_MESSAGES = "messages"
        private const val ONGOING_ID = 1
        private const val ACTION_STOP = "stop"
        const val EXTRA_BUFFER = "buffer"
        private var nextId = 100

        fun start(context: Context) {
            context.startForegroundService(Intent(context, IrcService::class.java))
        }

        fun stop(context: Context) {
            context.startService(Intent(context, IrcService::class.java).setAction(ACTION_STOP))
        }

        fun createChannels(context: Context) {
            val nm = context.getSystemService(NotificationManager::class.java)
            nm.createNotificationChannel(
                NotificationChannel(CHANNEL_CONNECTION, "Connection", NotificationManager.IMPORTANCE_MIN)
            )
            nm.createNotificationChannel(
                NotificationChannel(CHANNEL_MESSAGES, "Private messages & mentions", NotificationManager.IMPORTANCE_HIGH)
            )
        }

        private fun openApp(context: Context, buffer: String?): PendingIntent {
            val intent = Intent(context, MainActivity::class.java)
                .addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP)
                .putExtra(EXTRA_BUFFER, buffer)
            return PendingIntent.getActivity(
                context, buffer?.hashCode() ?: 0, intent,
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
            )
        }

        /** A notification for a private message or a mention of your nick. */
        fun notifyMessage(context: Context, title: String, text: String, buffer: String) {
            createChannels(context)
            val n = Notification.Builder(context, CHANNEL_MESSAGES)
                .setSmallIcon(R.drawable.ic_notify)
                .setContentTitle(title)
                .setContentText(text)
                .setStyle(Notification.BigTextStyle().bigText(text))
                .setContentIntent(openApp(context, buffer))
                .setAutoCancel(true)
                .build()
            context.getSystemService(NotificationManager::class.java).notify(nextId++, n)
        }
    }
}
