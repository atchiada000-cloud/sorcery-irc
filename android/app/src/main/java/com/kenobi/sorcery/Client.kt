package com.kenobi.sorcery

import android.content.Context
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

enum class Kind { SERVER, CHANNEL, QUERY }
enum class Style { CHAT, ACTION, NOTICE, INFO, ERROR, HINT, EVENT, WHOIS, MOTD }

data class Line(
    val time: String,
    val style: Style,
    val text: String,
    val nick: String = "",
    val mine: Boolean = false,
    val highlight: Boolean = false,
)

class Buffer(name: String, val kind: Kind) {
    var name by mutableStateOf(name)
    val lines = mutableStateListOf<Line>()
    val users = mutableStateMapOf<String, String>() // nick -> mode prefix
    var topic by mutableStateOf("")
    var unread by mutableIntStateOf(0)
    var highlight by mutableStateOf(false)
    var joined by mutableStateOf(false)
    val key: String get() = name.lowercase()
}

const val SERVER = "SorceryNet"
const val VERSION = "Sorcery for Android 1.0 — a custom IRC client"

// RFC 2812-style nick: no dots or spaces, can't start with a digit or '-'.
val NICK_RE = Regex("^[A-Za-z\\[\\]\\\\`_^{|}][A-Za-z0-9\\[\\]\\\\`_^{|}-]{0,29}$")
const val NICK_RULES = "Nicks can use letters, numbers and [ ] \\ ` _ ^ { | } -, can't start with a " +
    "number or '-', and can't contain dots or spaces (max 30 characters)."
val RANKS = mapOf('~' to 0, '&' to 1, '@' to 2, '%' to 3, '+' to 4)
private val SERVICES = setOf("nickserv", "chanserv", "memoserv", "operserv", "hostserv", "botserv")

val HELP = listOf(
    "Commands",
    "  /join #channel · /part [reason] · /close",
    "  /msg nick text · /query nick · /me action · /notice nick text",
    "  /nick NewNick · /topic [text] · /whois nick · /names · /list [*filter*]",
    "  /ns … /cs … /ms …  — NickServ, ChanServ, MemoServ",
    "  /networks — pick another IRC network · /server host[:port] — any server",
    "  /reconnect · /clear · /quit [message] · /quote RAW",
    "Registered nicks",
    "  If it's yours:  /ns IDENTIFY <password>",
    "  Register yours:  /ns REGISTER <password> <email>",
    "  Leave a note for someone offline:  /ms SEND <nick> <message>",
    "Tips: tap ☰ for windows, the people icon for the user list; long-press a user to put their nick in the box.",
)

fun isChannel(name: String) = name.isNotEmpty() && name[0] in "#&+!"

/** The whole client. Lives as long as the process; the foreground service keeps that alive. */
object Client {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main)
    private val outbox = Channel<String>(Channel.UNLIMITED)
    private var conn = IrcConnection(NETWORKS[0])
    private var job: Job? = null
    private var quitting = false
    private lateinit var prefs: android.content.SharedPreferences
    var notifier: ((title: String, text: String, buffer: String) -> Unit)? = null
    var appVisible = false

    var nick by mutableStateOf("")
    var registered by mutableStateOf(false)
    var connecting by mutableStateOf(false)
    var started by mutableStateOf(false)
    var network by mutableStateOf(NETWORKS[0])
    var serverName by mutableStateOf(NETWORKS[0].host)
    var showNetworks by mutableStateOf(false) // the UI shows the network picker when set
    val buffers = mutableStateListOf(Buffer(SERVER, Kind.SERVER))
    var activeKey by mutableStateOf(SERVER.lowercase())

    val active: Buffer get() = find(activeKey) ?: buffers.first()
    private val server: String get() = buffers.first().name

    fun init(context: Context) {
        if (::prefs.isInitialized) return
        prefs = context.getSharedPreferences("sorcery", Context.MODE_PRIVATE)
        nick = prefs.getString("nick", "") ?: ""
        network = networkFor(prefs.getString("network", "") ?: "") ?: NETWORKS[0]
        buffers.first().name = network.name
        activeKey = buffers.first().key
        scope.launch(Dispatchers.IO) {
            for (line in outbox) runCatching { conn.send(line) }
        }
    }

    /** A listed network by name or host, or a custom "host[:port]". */
    fun networkFor(query: String): Network? {
        if (query.isBlank()) return null
        NETWORKS.firstOrNull { it.name.equals(query, true) || it.host.equals(query, true) }?.let { return it }
        val host = query.substringBefore(':')
        if (!host.contains('.')) return null
        val port = query.substringAfter(':', "6697").toIntOrNull() ?: 6697
        return Network(host, host, "Custom server", tls = port != 6667)
    }

    private fun savedChannels(): List<String> =
        prefs.getString("channels:${network.host}", "")!!.split(' ').filter { it.isNotBlank() }

    private fun rememberChannels() {
        prefs.edit().putString("channels:${network.host}",
            buffers.filter { it.kind == Kind.CHANNEL && it.joined }.joinToString(" ") { it.name }).apply()
    }

    fun send(line: String) {
        outbox.trySend(line)
    }

    // ── buffers ───────────────────────────────────────────────────────────
    fun find(name: String): Buffer? = buffers.firstOrNull { it.key == name.lowercase() }

    fun buffer(name: String, kind: Kind, switch: Boolean = false): Buffer {
        val b = find(name) ?: Buffer(name, kind).also { buffers += it }
        if (switch) switchTo(b.key)
        return b
    }

    fun switchTo(key: String) {
        activeKey = key
        active.unread = 0
        active.highlight = false
    }

    private fun stamp(): String = SimpleDateFormat("HH:mm", Locale.getDefault()).format(Date())

    private fun add(name: String, line: Line, count: Boolean = true) {
        val b = find(name) ?: buffers.first()
        b.lines += line
        if (b.lines.size > 2000) b.lines.removeRange(0, b.lines.size - 2000)
        if (b.key != activeKey && count) {
            b.unread++
            if (line.highlight) b.highlight = true
        }
    }

    private fun info(name: String, text: String) = add(name, Line(stamp(), Style.INFO, "── $text"), count = false)
    private fun error(text: String, name: String = activeKey) = add(name, Line(stamp(), Style.ERROR, "✖ $text"), count = false)
    private fun hint(text: String, name: String = activeKey) = add(name, Line(stamp(), Style.HINT, text), count = false)
    private fun event(name: String, text: String) = add(name, Line(stamp(), Style.EVENT, text), count = false)

    private fun mentions(text: String): Boolean =
        nick.isNotEmpty() && Regex("\\b${Regex.escape(nick)}\\b", RegexOption.IGNORE_CASE).containsMatchIn(text)

    private fun nickHelp(why: String, name: String = activeKey) {
        hint("⚠ $why", name)
        hint("   Choose another with:  /nick NewNick", name)
    }

    // ── connection ────────────────────────────────────────────────────────
    fun start(chosenNick: String) {
        nick = chosenNick
        started = true
        connect()
    }

    fun connect() {
        job?.cancel()
        conn.close()
        conn = IrcConnection(network)
        quitting = false
        registered = false
        connecting = true
        info(server, "Connecting to ${network.host}:${network.port}" + if (network.tls) " (encrypted)…" else "…")
        if (!network.tls) hint("⚠ ${network.name} has no working encryption — anything you type can be read in transit.", server)
        val c = conn
        job = scope.launch {
            try {
                withContext(Dispatchers.IO) { c.connect() }
                serverName = c.serverName
                info(server, "Connected to ${c.serverName}. Signing in as $nick…")
                send("NICK $nick")
                send("USER $nick 0 * :$nick")
                withContext(Dispatchers.IO) {
                    c.readLines { line ->
                        scope.launch {
                            if (c !== conn) return@launch // a line from a connection we've since replaced
                            try {
                                handle(parse(line))
                            } catch (e: Exception) {
                                error("(couldn't handle: ${line.take(80)} — ${e.message})", server)
                            }
                        }
                    }
                }
                if (c === conn && !quitting) error("Disconnected from ${network.name}. Type /reconnect to connect again.")
            } catch (e: Exception) {
                if (c === conn && !quitting && e !is kotlinx.coroutines.CancellationException) {
                    error("Connection problem: ${e.message}. Type /reconnect to try again.", server)
                }
            } finally {
                // Only the current connection may update the shared state.
                if (c === conn) {
                    registered = false
                    connecting = false
                    buffers.forEach { it.joined = false }
                }
            }
        }
    }

    /** Choose a network before connecting (nick screen). */
    fun selectNetwork(to: Network) {
        network = to
        buffers.first().name = to.name
        activeKey = buffers.first().key
        prefs.edit().putString("network", to.host).apply()
    }

    /** Leave the current network and connect to another. Windows from the old one are closed. */
    fun switchNetwork(to: Network) {
        if (conn.connected) send("QUIT :switching networks")
        quitting = true
        val serverBuf = buffers.first()
        buffers.retainAll { it === serverBuf }
        serverBuf.lines.clear()
        serverBuf.topic = ""
        serverBuf.name = to.name
        activeKey = serverBuf.key
        network = to
        prefs.edit().putString("network", to.host).apply()
        scope.launch {
            kotlinx.coroutines.delay(300) // let the QUIT go out first
            connect()
        }
    }

    fun quit(message: String = "") {
        quitting = true
        send("QUIT :" + message.ifBlank { "Sorcery, signing off" })
        scope.launch {
            kotlinx.coroutines.delay(400)
            conn.close()
            job?.cancel()
            registered = false
            connecting = false
            started = false
        }
    }

    // ── incoming ──────────────────────────────────────────────────────────
    private fun handle(m: Message) {
        val p = m.params
        when (m.command) {
            "PING" -> send("PONG :${m.text}")
            "ERROR" -> error(m.text, server)

            "001" -> {
                registered = true
                connecting = false
                nick = m.p(0)
                prefs.edit().putString("nick", nick).apply() // only nicks the server accepted
                add(server, Line(stamp(), Style.HINT, "Welcome to ${network.name}, $nick. Type /help for commands."), false)
                savedChannels().forEach { send("JOIN $it") }
            }
            "002", "003", "251", "252", "254", "255", "265", "266", "372", "375", "376", "422" ->
                add(server, Line(stamp(), Style.MOTD, stripFormatting(p.drop(1).joinToString(" "))), false)
            "433", "436" -> {
                nickHelp("The nick '${m.p(1)}' is already in use.", if (registered) activeKey else server.lowercase())
                if (!registered) switchTo(server.lowercase())
            }
            "432" -> nickHelp("'${m.p(1)}' isn't allowed as a nick. $NICK_RULES",
                if (registered) activeKey else server.lowercase())
            "437" -> nickHelp("'${m.p(1)}' is temporarily unavailable.")
            "332" -> {
                val b = buffer(m.p(1), Kind.CHANNEL)
                b.topic = m.p(2)
                info(b.name, "Topic: ${stripFormatting(m.p(2))}")
            }
            "333" -> {
                val whenSet = m.p(3).toLongOrNull()?.let {
                    SimpleDateFormat("yyyy-MM-dd HH:mm", Locale.getDefault()).format(Date(it * 1000))
                } ?: "?"
                info(m.p(1), "Set by ${m.p(2).substringBefore('!')} on $whenSet")
            }
            "353" -> {
                val b = buffer(m.p(2), Kind.CHANNEL)
                m.p(3).split(' ').filter { it.isNotEmpty() }.forEach { entry ->
                    val i = entry.indexOfFirst { it !in RANKS }.let { if (it < 0) entry.length else it }
                    b.users[entry.substring(i)] = entry.substring(0, i)
                }
            }
            "366" -> {}
            "321" -> info(server, "Channel list:")
            "322" -> {
                val topic = stripFormatting(m.p(3).replace(Regex("^\\[\\+[^]]*] ?"), ""))
                if ("Fake channel for confusing spambots" in topic) return
                add(server, Line(stamp(), Style.MOTD, "${m.p(1)}  (${m.p(2)})  $topic"), false)
            }
            "323" -> info(server, "End of list." +
                if (network.certDomain == "sorcery.net") " (SorceryNet only shows real channels after you've been connected ~2 minutes.)" else "")
            "311" -> whois("${m.p(1)} is ${m.p(2)}@${m.p(3)} (${m.text})")
            "317" -> whois("${m.p(1)} has been idle ${(m.p(2).toLongOrNull() ?: 0) / 60} min")
            "318", "369" -> whois("End of WHOIS")
            "312", "313", "319", "301", "330", "338", "378", "671", "314" -> whois(p.drop(1).joinToString(" "))
            "401", "402", "403", "404", "405", "406", "421", "441", "442", "443", "461", "471", "473",
            "474", "475", "477", "482" -> error(p.drop(1).joinToString(" "))

            "PRIVMSG" -> privmsg(m)
            "NOTICE" -> notice(m)
            "JOIN" -> {
                val ch = m.p(0)
                if (m.nick.equals(nick, true)) {
                    val b = buffer(ch, Kind.CHANNEL, switch = true)
                    b.joined = true
                    b.users.clear()
                    info(ch, "You joined $ch")
                    rememberChannels()
                } else {
                    buffer(ch, Kind.CHANNEL).users[m.nick] = ""
                    event(ch, "→ ${m.nick} joined")
                }
            }
            "PART", "KICK" -> {
                val ch = m.p(0)
                val b = find(ch) ?: return
                val who = if (m.command == "KICK") m.p(1) else m.nick
                b.users.remove(who)
                val reason = if (p.size > (if (m.command == "KICK") 2 else 1)) stripFormatting(m.text) else ""
                val why = if (reason.isNotBlank()) " ($reason)" else ""
                if (who.equals(nick, true)) {
                    b.joined = false
                    b.users.clear()
                    rememberChannels()
                    info(ch, (if (m.command == "KICK") "You were kicked by ${m.nick}" else "You left") + why)
                } else {
                    event(ch, "← $who " + (if (m.command == "KICK") "was kicked by ${m.nick}" else "left") + why)
                }
            }
            "QUIT" -> buffers.filter { m.nick in it.users }.forEach {
                it.users.remove(m.nick)
                event(it.name, "← ${m.nick} quit (${stripFormatting(m.text)})")
            }
            "NICK" -> {
                val old = m.nick
                val new = m.text
                if (old.equals(nick, true)) {
                    nick = new
                    prefs.edit().putString("nick", new).apply()
                    info(activeKey, "You are now known as $new")
                    if (new.startsWith("guest", true)) hint("NickServ renamed you. Pick a nick of your own with:  /nick NewNick")
                }
                buffers.filter { old in it.users }.forEach {
                    it.users[new] = it.users.remove(old) ?: ""
                    event(it.name, "$old is now $new")
                }
                find(old)?.takeIf { it.kind == Kind.QUERY }?.let { q ->
                    val wasActive = q.key == activeKey
                    q.name = new
                    if (wasActive) activeKey = q.key
                }
            }
            "TOPIC" -> {
                val b = buffer(m.p(0), Kind.CHANNEL)
                b.topic = m.text
                info(b.name, "${m.nick} set the topic: ${stripFormatting(m.text)}")
            }
            "MODE" -> if (isChannel(m.p(0))) {
                info(m.p(0), "${m.nick} sets mode ${p.drop(1).joinToString(" ")}")
                find(m.p(0))?.users?.clear()
                send("NAMES ${m.p(0)}")
            }
            "INVITE" -> hint("${m.nick} invited you to ${m.text}.  Join with:  /join ${m.text}")
            else -> if (m.command.all { it.isDigit() }) {
                add(server, Line(stamp(), Style.MOTD, stripFormatting(p.drop(1).joinToString(" "))), false)
            }
        }
    }

    private fun whois(text: String) = add(activeKey, Line(stamp(), Style.WHOIS, "ⓘ " + stripFormatting(text)), false)

    private fun privmsg(m: Message) {
        val target = m.p(0)
        var text = m.text
        val key = if (isChannel(target)) target else m.nick
        var style = Style.CHAT
        if (text.startsWith("\u0001") && text.endsWith("\u0001") && text.length > 1) {
            val ctcp = text.trim('\u0001')
            val cmd = ctcp.substringBefore(' ').uppercase()
            val arg = ctcp.substringAfter(' ', "")
            when (cmd) {
                "ACTION" -> { style = Style.ACTION; text = arg }
                "VERSION" -> { send("NOTICE ${m.nick} :\u0001VERSION $VERSION\u0001"); return }
                "PING" -> { send("NOTICE ${m.nick} :\u0001PING $arg\u0001"); return }
                else -> return
            }
        }
        text = stripFormatting(text)
        val private = !isChannel(target)
        val hl = mentions(text)
        buffer(key, if (private) Kind.QUERY else Kind.CHANNEL)
        add(key, Line(stamp(), style, text, m.nick, highlight = hl), count = true)
        if ((private || hl) && !(appVisible && activeKey == key.lowercase())) {
            notifier?.invoke(if (private) m.nick else "${m.nick} in $target", text, key)
        }
    }

    private fun notice(m: Message) {
        val sender = m.nick.ifEmpty { serverName }
        val text = stripFormatting(m.text)
        when {
            sender.lowercase() in SERVICES -> {
                add(if (registered) activeKey else server, Line(stamp(), Style.NOTICE, text, sender), false)
                if (sender.equals("NickServ", true) &&
                    Regex("nickname is registered|is registered and protected", RegexOption.IGNORE_CASE).containsMatchIn(text)
                ) {
                    hint("⚠ '$nick' is a registered nick.  If it's yours:  /ns IDENTIFY <password>")
                    hint("   Otherwise choose another with:  /nick NewNick   (NickServ may rename you soon)")
                }
            }
            '!' !in m.prefix || !registered -> add(server, Line(stamp(), Style.MOTD, text), false)
            else -> {
                val key = if (isChannel(m.p(0))) m.p(0) else activeKey
                add(key, Line(stamp(), Style.NOTICE, text, sender, highlight = mentions(text)))
            }
        }
    }

    // ── outgoing ──────────────────────────────────────────────────────────
    fun submit(input: String) {
        if (input.isBlank()) return
        if (input.startsWith("/") && !input.startsWith("//")) command(input.substring(1))
        else say(if (input.startsWith("//")) input.substring(1) else input)
    }

    private fun say(text: String, target: String? = null) {
        val b = target?.let { find(it) } ?: if (target == null) active else null
        if (b?.kind == Kind.SERVER) {
            hint("This is the server window. Join a channel first:  /join #channel   (or /help)")
            return
        }
        if (!registered) {
            error("Not connected yet.")
            return
        }
        val name = target ?: active.name
        send("PRIVMSG $name :$text")
        val buf = b ?: buffer(name, Kind.QUERY)
        add(buf.name, Line(stamp(), Style.CHAT, text, nick, mine = true), false)
    }

    private fun command(line: String) {
        val cmd = line.substringBefore(' ').lowercase()
        val arg = line.substringAfter(' ', "").trim()
        val b = active
        when (cmd) {
            "help" -> HELP.forEach { add(activeKey, Line(stamp(), Style.WHOIS, it), false) }
            "clear" -> b.lines.clear()
            "quit", "exit" -> quit(arg)
            "server", "network", "networks" -> {
                if (arg.isEmpty()) showNetworks = true
                else networkFor(arg)?.let { switchNetwork(it) }
                    ?: hint("Unknown network '$arg'. Use /networks to pick from the list, or /server host[:port].")
            }
            "reconnect", "connect" -> {
                if (conn.connected) send("QUIT :reconnecting")
                connect()
            }
            "close" -> close(b, arg)
            "nick" -> when {
                arg.isEmpty() -> hint("Your nick is $nick. Change it with:  /nick NewNick")
                !NICK_RE.matches(arg) -> nickHelp("'$arg' isn't a valid nick. $NICK_RULES")
                else -> {
                    if (!registered) nick = arg
                    send("NICK $arg")
                }
            }
            else -> {
                if (!conn.connected) {
                    error("Not connected. Type /reconnect.")
                    return
                }
                online(cmd, arg, b)
            }
        }
    }

    private fun online(cmd: String, arg: String, b: Buffer) {
        when (cmd) {
            "join", "j" -> {
                if (arg.isEmpty()) return hint("Usage:  /join #channel")
                val parts = arg.split(' ')
                val ch = if (isChannel(parts[0])) parts[0] else "#${parts[0]}"
                send("JOIN $ch" + (parts.getOrNull(1)?.let { " $it" } ?: ""))
            }
            "part", "leave" -> if (b.kind != Kind.CHANNEL) hint("Use /part in a channel window (or /close).")
                else send("PART ${b.name}" + (if (arg.isNotEmpty()) " :$arg" else ""))
            "msg", "m" -> {
                val target = arg.substringBefore(' ')
                val text = arg.substringAfter(' ', "")
                if (text.isEmpty()) return hint("Usage:  /msg nick message")
                if (target.lowercase() in SERVICES) {
                    send("PRIVMSG $target :$text")
                    val first = text.substringBefore(' ')
                    val shown = if (first.uppercase() in setOf("IDENTIFY", "REGISTER", "GHOST", "RECOVER")) "$first ••••••" else text
                    add(activeKey, Line(stamp(), Style.NOTICE, "→ $target: $shown", mine = true), false)
                } else say(text, target)
            }
            "ns", "cs", "ms", "nickserv", "chanserv", "memoserv" -> {
                val service = mapOf("ns" to "NickServ", "cs" to "ChanServ", "ms" to "MemoServ",
                    "nickserv" to "NickServ", "chanserv" to "ChanServ", "memoserv" to "MemoServ")[cmd]
                if (arg.isEmpty()) hint("Usage:  /$cmd HELP") else online("msg", "$service $arg", b)
            }
            "query", "q" -> {
                if (arg.isEmpty()) return hint("Usage:  /query nick")
                val target = arg.substringBefore(' ')
                buffer(target, Kind.QUERY, switch = true)
                arg.substringAfter(' ', "").takeIf { it.isNotEmpty() }?.let { say(it, target) }
            }
            "me" -> {
                if (b.kind == Kind.SERVER) return hint("Use /me in a channel or private chat.")
                send("PRIVMSG ${b.name} :\u0001ACTION $arg\u0001")
                add(b.name, Line(stamp(), Style.ACTION, arg, nick, mine = true), false)
            }
            "notice" -> {
                val target = arg.substringBefore(' ')
                send("NOTICE $target :${arg.substringAfter(' ', "")}")
                add(activeKey, Line(stamp(), Style.NOTICE, "→ -$target- ${arg.substringAfter(' ', "")}", mine = true), false)
            }
            "topic" -> when {
                b.kind != Kind.CHANNEL -> hint("Use /topic in a channel.")
                arg.isNotEmpty() -> send("TOPIC ${b.name} :$arg")
                else -> send("TOPIC ${b.name}")
            }
            "whois", "wi" -> send("WHOIS " + arg.ifEmpty { if (b.kind == Kind.QUERY) b.name else nick })
            "names" -> if (b.kind == Kind.CHANNEL) { b.users.clear(); send("NAMES ${b.name}") }
            "list" -> {
                switchTo(server.lowercase())
                send("LIST" + if (arg.isNotEmpty()) " $arg" else "")
            }
            "quote", "raw" -> {
                send(arg)
                info(activeKey, "sent: $arg")
            }
            else -> error("Unknown command /$cmd. Type /help for the list.")
        }
    }

    fun close(b: Buffer, reason: String = "") {
        if (b.kind == Kind.SERVER) return
        if (b.kind == Kind.CHANNEL && b.joined) send("PART ${b.name}" + if (reason.isNotEmpty()) " :$reason" else "")
        val i = buffers.indexOf(b)
        buffers.remove(b)
        rememberChannels()
        switchTo(buffers[maxOf(0, i - 1)].key)
    }
}
