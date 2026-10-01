package com.kenobi.sorcery

import java.io.BufferedReader
import java.io.InputStreamReader
import java.io.OutputStream
import java.security.cert.X509Certificate
import javax.net.ssl.SSLException
import javax.net.ssl.SSLSocket
import javax.net.ssl.SSLSocketFactory

const val HOST = "irc.sorcery.net"
const val PORT = 6697 // TLS

data class Message(val prefix: String, val command: String, val params: List<String>) {
    val nick: String get() = prefix.substringBefore('!')
    val text: String get() = params.lastOrNull() ?: ""
    fun p(i: Int): String = params.getOrElse(i) { "" }
}

fun parse(raw: String): Message {
    var line = raw
    if (line.startsWith("@")) line = line.substringAfter(' ', "") // IRCv3 tags; unused
    var prefix = ""
    if (line.startsWith(":")) {
        prefix = line.substring(1).substringBefore(' ')
        line = line.substringAfter(' ', "")
    }
    var trailing: String? = null
    val idx = line.indexOf(" :")
    if (idx >= 0) {
        trailing = line.substring(idx + 2)
        line = line.substring(0, idx)
    } else if (line.startsWith(":")) {
        trailing = line.substring(1)
        line = ""
    }
    val parts = line.split(' ').filter { it.isNotEmpty() }
    val params = parts.drop(1).toMutableList()
    if (trailing != null) params += trailing
    return Message(prefix, parts.firstOrNull()?.uppercase() ?: "", params)
}

// mIRC formatting: colours (\x03fg,bg), bold, italic, underline, reverse, reset...
private val FORMATTING = Regex("\u0003(\\d{1,2}(,\\d{1,2})?)?|[\u0002\u000f\u0011\u0016\u001d\u001e\u001f]")
fun stripFormatting(s: String): String = s.replace(FORMATTING, "")

/** One TLS connection to SorceryNet. Blocking; call from Dispatchers.IO. */
class IrcConnection {
    private var socket: SSLSocket? = null
    private var out: OutputStream? = null
    var serverName = HOST
        private set

    fun connect() {
        // The default factory checks the certificate chain but not the hostname.
        // SorceryNet's servers present certs for their own names (circe.sorcery.net,
        // ...), not the round-robin irc.sorcery.net, so the name is checked below.
        val s = SSLSocketFactory.getDefault().createSocket(HOST, PORT) as SSLSocket
        s.startHandshake()
        val cert = s.session.peerCertificates.first() as X509Certificate
        val names = cert.subjectAlternativeNames.orEmpty().filter { it[0] == 2 }.map { it[1] as String }
        if (names.none { it == "sorcery.net" || it.endsWith(".sorcery.net") }) {
            s.close()
            throw SSLException("server certificate is not for sorcery.net")
        }
        serverName = names.first()
        socket = s
        out = s.outputStream
    }

    /** Reads lines until the connection closes. */
    fun readLines(onLine: (String) -> Unit) {
        val reader = BufferedReader(InputStreamReader(socket!!.inputStream, Charsets.UTF_8))
        while (true) {
            val line = reader.readLine() ?: return
            onLine(line)
        }
    }

    @Synchronized
    fun send(line: String) {
        val o = out ?: return
        // IRC lines are capped at 512 bytes including CRLF.
        val bytes = line.replace("\r", "").replace("\n", " ").toByteArray(Charsets.UTF_8)
        o.write(bytes, 0, minOf(bytes.size, 510))
        o.write("\r\n".toByteArray())
        o.flush()
    }

    fun close() {
        runCatching { socket?.close() }
        socket = null
        out = null
    }

    val connected: Boolean get() = socket?.isClosed == false
}
