package com.kenobi.sorcery

import java.io.BufferedReader
import java.io.InputStreamReader
import java.io.OutputStream
import java.net.InetSocketAddress
import java.net.Socket
import java.security.cert.X509Certificate
import javax.net.ssl.HttpsURLConnection
import javax.net.ssl.SSLException
import javax.net.ssl.SSLSocket
import javax.net.ssl.SSLSocketFactory

/**
 * An IRC network. [certDomain] is for networks whose servers present certificates
 * for their own names rather than the round-robin host (SorceryNet's are
 * circe.sorcery.net etc., not irc.sorcery.net).
 */
data class Network(
    val name: String,
    val host: String,
    val description: String,
    val users: Int? = null, // rough count seen on 2026-10-01
    val tls: Boolean = true,
    val certDomain: String? = null,
) {
    val port: Int get() = if (tls) 6697 else 6667
}

// Checked 2026-10-01: each one connected, its certificate verified (TLS ones) and it
// let a client sign in. User counts are a snapshot from that day.
val NETWORKS = listOf(
    Network("SorceryNet", "irc.sorcery.net", "Fantasy, role-play and old friends — home", 356, certDomain = "sorcery.net"),
    Network("Libera.Chat", "irc.libera.chat", "Free & open-source projects, tech", 31548),
    Network("OFTC", "irc.oftc.net", "Open-source projects (Debian, Tor…)", 15224),
    Network("Rizon", "irc.rizon.net", "Anime, gaming and general chat", 9263),
    Network("freenode", "irc.freenode.net", "General chat (post-2021 freenode)", 6183),
    Network("hackint", "irc.hackint.org", "Hackers, CCC and maker community", 4170),
    Network("KampungChat", "irc.kampungchat.org", "Malaysian & Asian social chat", 1905),
    Network("Abjects", "irc.abjects.net", "General chat", 1718),
    Network("EsperNet", "irc.esper.net", "Gaming and Minecraft development", 1393),
    Network("Snoonet", "irc.snoonet.org", "Reddit communities", 1130),
    Network("tilde.chat", "irc.tilde.chat", "The tildeverse and small web", 738),
    Network("PTnet", "irc.ptnet.org", "Portuguese-speaking chat", 607),
    Network("Furnet", "irc.furnet.org", "Furry community", 552),
    Network("AfterNET", "irc.afternet.org", "General chat", 324),
    Network("SwiftIRC", "irc.swiftirc.net", "RuneScape and gaming", 292),
    Network("PIRC", "irc.pirc.pl", "Polish chat", 262),
    Network("DarkMyst", "irc.darkmyst.org", "Fantasy role-playing", 245),
    Network("Ergo", "irc.ergo.chat", "The Ergo IRC server project", 197),
    Network("SpotChat", "irc.spotchat.org", "General chat", 185),
    Network("AnonOps", "irc.anonops.com", "Anonymous / activism chat", 117),
    Network("ScoutLink", "irc.scoutlink.net", "Scouts and Guides worldwide", 85),
    Network("Interlinked", "irc.interlinked.me", "Tech and general chat", 65),
    Network("DALnet", "irc.dal.net", "Classic general chat, since 1994"),
    // Classic networks without working TLS: plain text, readable by anyone on the path.
    Network("EFnet", "irc.efnet.org", "The original IRC network (1990)", tls = false),
    Network("IRCnet", "open.ircnet.net", "Classic European network", tls = false),
    Network("Undernet", "irc.undernet.org", "Classic general chat", tls = false),
    Network("QuakeNet", "irc.quakenet.org", "Gaming, esports", tls = false),
)

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

/** One connection to an IRC network. Blocking; call from Dispatchers.IO. */
class IrcConnection(private val network: Network) {
    private var socket: Socket? = null
    private var out: OutputStream? = null
    var serverName = network.host
        private set

    fun connect() {
        if (!network.tls) {
            val s = Socket()
            s.connect(InetSocketAddress(network.host, network.port), 15_000)
            socket = s
            out = s.outputStream
            return
        }
        // The default factory checks the certificate chain but not the hostname,
        // so the name is checked here.
        val s = SSLSocketFactory.getDefault().createSocket(network.host, network.port) as SSLSocket
        s.startHandshake()
        val cert = s.session.peerCertificates.first() as X509Certificate
        val names = cert.subjectAlternativeNames.orEmpty().filter { it[0] == 2 }.map { it[1] as String }
        val ok = HttpsURLConnection.getDefaultHostnameVerifier().verify(network.host, s.session) ||
            network.certDomain?.let { d -> names.any { it == d || it.endsWith(".$d") } } == true
        if (!ok) {
            s.close()
            throw SSLException("the server's certificate isn't for ${network.host}")
        }
        serverName = names.firstOrNull { !it.startsWith("*") } ?: network.host
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
