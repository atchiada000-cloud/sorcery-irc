package com.kenobi.sorcery

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.background
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.consumeWindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.Send
import androidx.compose.material.icons.filled.AlternateEmail
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Menu
import androidx.compose.material.icons.filled.People
import androidx.compose.material3.Badge
import androidx.compose.material3.BadgedBox
import androidx.compose.material3.Button
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.ModalDrawerSheet
import androidx.compose.material3.ModalNavigationDrawer
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.rememberDrawerState
import androidx.compose.material3.DrawerValue
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.TextRange
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.ui.text.input.TextFieldValue
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.launch

// Tokyo Night, to match Omarchy's default theme.
private object Palette {
    val bg = Color(0xFF1A1B26)
    val panel = Color(0xFF16161E)
    val surface = Color(0xFF24283B)
    val fg = Color(0xFFC0CAF5)
    val dim = Color(0xFF565F89)
    val red = Color(0xFFF7768E)
    val green = Color(0xFF9ECE6A)
    val yellow = Color(0xFFE0AF68)
    val blue = Color(0xFF7AA2F7)
    val magenta = Color(0xFFBB9AF7)
    val cyan = Color(0xFF7DCFFF)
    val orange = Color(0xFFFF9E64)
    val teal = Color(0xFF73DACA)
    val nicks = listOf(red, green, yellow, blue, magenta, cyan, orange, teal, Color(0xFF2AC3DE), Color(0xFFB4F9F8))
}

private fun nickColour(nick: String) = Palette.nicks[Math.floorMod(nick.lowercase().hashCode(), Palette.nicks.size)]

private data class Cmd(val template: String, val description: String, val section: String)

private val COMMANDS = listOf(
    Cmd("/join #", "Join a channel", "Channels"),
    Cmd("/part ", "Leave this channel (optional reason)", "Channels"),
    Cmd("/topic ", "Show the topic, or type text to set it", "Channels"),
    Cmd("/names", "Refresh the user list", "Channels"),
    Cmd("/list", "List channels (works after ~2 min connected)", "Channels"),
    Cmd("/close", "Close this window", "Channels"),
    Cmd("/msg ", "Private message: /msg nick text", "People"),
    Cmd("/query ", "Open a private chat with someone", "People"),
    Cmd("/me ", "Do an action: * you waves", "People"),
    Cmd("/notice ", "Send a notice: /notice nick text", "People"),
    Cmd("/whois ", "Find out about someone", "People"),
    Cmd("/nick ", "Change your nick", "You"),
    Cmd("/ns IDENTIFY ", "Log in to your registered nick", "You"),
    Cmd("/ns REGISTER ", "Register your nick: password email", "You"),
    Cmd("/ms SEND ", "Leave a note for someone offline: nick message", "Services"),
    Cmd("/ms LIST", "See notes left for you", "Services"),
    Cmd("/ms READ ", "Read a note by number", "Services"),
    Cmd("/ns INFO ", "Look up a registered nick", "Services"),
    Cmd("/cs INFO #", "Look up a registered channel", "Services"),
    Cmd("/networks", "Switch to another IRC network", "Connection"),
    Cmd("/server ", "Connect to any server: host[:port]", "Connection"),
    Cmd("/reconnect", "Reconnect to this network", "Connection"),
    Cmd("/clear", "Clear this window", "Connection"),
    Cmd("/quit", "Leave SorceryNet", "Connection"),
    Cmd("/help", "Show help in this window", "Connection"),
)

private val Mono = TextStyle(fontFamily = FontFamily.Monospace, fontSize = 14.sp, lineHeight = 20.sp)

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        Client.init(applicationContext)
        Client.notifier = { title, text, buffer -> IrcService.notifyMessage(applicationContext, title, text, buffer) }
        if (checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), 1)
        }
        openFromNotification(intent)
        setContent {
            MaterialTheme(
                colorScheme = darkColorScheme(
                    primary = Palette.blue, onPrimary = Palette.bg, background = Palette.bg,
                    surface = Palette.bg, onSurface = Palette.fg, onBackground = Palette.fg,
                    surfaceVariant = Palette.surface, onSurfaceVariant = Palette.dim,
                    surfaceContainerLow = Palette.panel, surfaceContainer = Palette.panel,
                    surfaceContainerHigh = Palette.surface, outline = Palette.dim, error = Palette.red,
                )
            ) {
                Surface(Modifier.fillMaxSize(), color = Palette.bg) {
                    if (Client.started) ChatScreen() else NickScreen()
                }
            }
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        openFromNotification(intent)
    }

    private fun openFromNotification(intent: Intent?) {
        intent?.getStringExtra(IrcService.EXTRA_BUFFER)?.let { name ->
            Client.find(name)?.let { Client.switchTo(it.key) }
        }
    }

    override fun onStart() {
        super.onStart()
        Client.appVisible = true
    }

    override fun onStop() {
        super.onStop()
        Client.appVisible = false
    }
}

@OptIn(ExperimentalFoundationApi::class)
@Composable
private fun NickScreen() {
    val context = LocalContext.current
    var nick by rememberSaveable { mutableStateOf(Client.nick) }
    var error by remember { mutableStateOf("") }
    var picking by remember { mutableStateOf(false) }
    val net = Client.network
    fun go() {
        val n = nick.trim()
        if (NICK_RE.matches(n)) {
            Client.start(n)
            IrcService.start(context)
        } else error = NICK_RULES
    }
    Box(Modifier.fillMaxSize().imePadding(), contentAlignment = Alignment.Center) {
        Column(
            Modifier.widthIn(max = 460.dp).padding(24.dp)
                .background(Palette.panel, RoundedCornerShape(16.dp)).padding(28.dp),
            verticalArrangement = Arrangement.spacedBy(14.dp),
        ) {
            Text("☾ Sorcery", fontSize = 26.sp, fontWeight = FontWeight.SemiBold, color = Palette.yellow)
            Row(
                Modifier.fillMaxWidth().background(Palette.surface, RoundedCornerShape(10.dp))
                    .combinedClickable(onClick = { picking = true }).padding(14.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Column(Modifier.weight(1f)) {
                    Text(net.name, color = Palette.fg, fontWeight = FontWeight.SemiBold)
                    Text(net.description, color = Palette.dim, fontSize = 12.sp, maxLines = 1, overflow = TextOverflow.Ellipsis)
                }
                Text("Change", color = Palette.blue, fontSize = 13.sp)
            }
            Text(
                "Pick a nick for this session. Any nick you like — if it's taken or registered, " +
                    "you'll be shown how to switch.",
                color = Palette.fg,
            )
            OutlinedTextField(
                value = nick,
                onValueChange = { nick = it; error = "" },
                label = { Text("Nick") },
                singleLine = true,
                textStyle = Mono,
                keyboardOptions = KeyboardOptions(capitalization = KeyboardCapitalization.None,
                    autoCorrectEnabled = false, imeAction = ImeAction.Go),
                keyboardActions = KeyboardActions(onGo = { go() }),
                modifier = Modifier.fillMaxWidth(),
            )
            if (error.isNotEmpty()) Text(error, color = Palette.red, fontSize = 13.sp)
            Button(onClick = { go() }, modifier = Modifier.fillMaxWidth()) { Text("Connect") }
            Text("${net.host} · port ${net.port} · " + if (net.tls) "encrypted" else "not encrypted",
                color = if (net.tls) Palette.dim else Palette.orange, fontSize = 12.sp)
        }
    }
    if (picking) {
        NetworkSheet(current = net, onDismiss = { picking = false }, onPick = {
            Client.selectNetwork(it)
            picking = false
        })
    }
}

@OptIn(ExperimentalMaterial3Api::class, ExperimentalFoundationApi::class)
@Composable
private fun NetworkSheet(current: Network, onDismiss: () -> Unit, onPick: (Network) -> Unit) {
    ModalBottomSheet(onDismissRequest = onDismiss, containerColor = Palette.panel) {
        Text("IRC networks", Modifier.padding(horizontal = 20.dp, vertical = 4.dp),
            color = Palette.yellow, fontSize = 18.sp, fontWeight = FontWeight.SemiBold)
        Text("All checked and working on 1 Oct 2026. User counts are from that day.",
            Modifier.padding(horizontal = 20.dp), color = Palette.dim, fontSize = 12.sp)
        LazyColumn(contentPadding = PaddingValues(top = 8.dp, bottom = 24.dp)) {
            NETWORKS.groupBy { it.tls }.toSortedMap(compareByDescending { it }).forEach { (tls, nets) ->
                item(key = "h-$tls") {
                    Text(if (tls) "ENCRYPTED" else "CLASSIC · NOT ENCRYPTED",
                        Modifier.padding(start = 20.dp, top = 14.dp, bottom = 4.dp),
                        color = if (tls) Palette.dim else Palette.orange, fontSize = 11.sp, letterSpacing = 1.5.sp)
                }
                items(nets, key = { it.host }) { n ->
                    Row(
                        Modifier.fillMaxWidth()
                            .background(if (n.host == current.host) Palette.surface else Color.Transparent)
                            .combinedClickable(onClick = { onPick(n) })
                            .padding(horizontal = 20.dp, vertical = 10.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Column(Modifier.weight(1f)) {
                            Text(n.name, color = if (n.host == current.host) Palette.yellow else Palette.fg,
                                fontWeight = FontWeight.SemiBold)
                            Text(n.description, color = Palette.dim, fontSize = 13.sp)
                        }
                        n.users?.let {
                            Text("%,d".format(it), color = Palette.cyan, style = Mono.copy(fontSize = 13.sp))
                        }
                    }
                }
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun ChatScreen() {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    val drawer = rememberDrawerState(DrawerValue.Closed)
    var showUsers by remember { mutableStateOf(false) }
    var showCommands by remember { mutableStateOf(false) }
    var input by remember { mutableStateOf(TextFieldValue("")) }
    val buffer = Client.active

    LaunchedEffect(Client.started) { if (!Client.started) IrcService.stop(context) }

    fun insertNick(nick: String) {
        val t = input.text
        val add = if (t.isEmpty()) "$nick: " else (if (t.endsWith(" ")) "" else " ") + "$nick "
        input = TextFieldValue(t + add, TextRange((t + add).length))
    }

    ModalNavigationDrawer(
        drawerState = drawer,
        drawerContent = {
            ModalDrawerSheet(drawerContainerColor = Palette.panel) {
                WindowList(onPick = { scope.launch { drawer.close() } })
            }
        },
    ) {
        Scaffold(
            containerColor = Palette.bg,
            topBar = {
                TopAppBar(
                    colors = TopAppBarDefaults.topAppBarColors(containerColor = Palette.panel),
                    navigationIcon = {
                        IconButton(onClick = { scope.launch { drawer.open() } }) {
                            val others = Client.buffers.filter { it !== buffer }
                            BadgedBox(badge = {
                                if (others.any { it.unread > 0 }) {
                                    Badge(containerColor = if (others.any { it.highlight }) Palette.red else Palette.yellow)
                                }
                            }) { Icon(Icons.Default.Menu, "Windows") }
                        }
                    },
                    title = {
                        Column {
                            Text(buffer.name, fontWeight = FontWeight.SemiBold, maxLines = 1)
                            val sub = when {
                                buffer.topic.isNotEmpty() -> stripFormatting(buffer.topic)
                                buffer.kind == Kind.SERVER -> when {
                                    Client.registered -> "connected as ${Client.nick} · ${Client.serverName}"
                                    Client.connecting -> "connecting…"
                                    else -> "offline — /reconnect"
                                }
                                buffer.kind == Kind.QUERY -> "private chat"
                                else -> ""
                            }
                            if (sub.isNotEmpty()) {
                                Text(sub, fontSize = 12.sp, color = Palette.dim, maxLines = 1, overflow = TextOverflow.Ellipsis)
                            }
                        }
                    },
                    actions = {
                        if (buffer.kind == Kind.CHANNEL) {
                            TextButton(onClick = { showUsers = true }) {
                                Icon(Icons.Default.People, "Users", tint = Palette.fg)
                                Spacer(Modifier.width(6.dp))
                                Text("${buffer.users.size}", color = Palette.fg)
                            }
                        }
                        if (buffer.kind == Kind.QUERY) {
                            TextButton(onClick = { Client.close(buffer) }) {
                                Icon(Icons.Default.Close, "Close this private chat", tint = Palette.fg)
                                Spacer(Modifier.width(6.dp))
                                Text("Close", color = Palette.fg)
                            }
                        }
                    },
                )
            },
        ) { padding ->
            Column(Modifier.fillMaxSize().padding(padding).consumeWindowInsets(padding).imePadding()) {
                Messages(buffer, Modifier.weight(1f))
                InputBar(
                    value = input,
                    onChange = { input = it },
                    placeholder = if (buffer.kind == Kind.SERVER) "/join #channel  ·  /help" else "Message ${buffer.name}",
                    onSend = {
                        Client.submit(input.text)
                        input = TextFieldValue("")
                    },
                    onCommands = { showCommands = true },
                    onComplete = {
                        val t = input.text
                        val start = t.lastIndexOf(' ') + 1
                        val stem = t.substring(start)
                        val match = buffer.users.keys.sortedBy { it.lowercase() }
                            .firstOrNull { stem.isNotEmpty() && it.startsWith(stem, ignoreCase = true) }
                        if (match != null) {
                            val done = t.substring(0, start) + match + if (start == 0) ": " else " "
                            input = TextFieldValue(done, TextRange(done.length))
                        }
                    },
                )
            }
        }
    }

    if (Client.showNetworks) {
        NetworkSheet(current = Client.network, onDismiss = { Client.showNetworks = false }, onPick = {
            Client.showNetworks = false
            if (it.host != Client.network.host) Client.switchNetwork(it)
        })
    }

    if (showCommands) {
        ModalBottomSheet(onDismissRequest = { showCommands = false }, containerColor = Palette.panel) {
            CommandList(onPick = { cmd ->
                if (cmd.template.endsWith(" ") || cmd.template.endsWith("#")) {
                    input = TextFieldValue(cmd.template, TextRange(cmd.template.length))
                } else {
                    Client.submit(cmd.template) // complete on its own, e.g. /names
                }
                showCommands = false
            })
        }
    }

    if (showUsers) {
        ModalBottomSheet(onDismissRequest = { showUsers = false }, containerColor = Palette.panel) {
            UserList(buffer, onOpen = { nick ->
                Client.buffer(nick, Kind.QUERY, switch = true)
                showUsers = false
            }, onInsert = { nick ->
                insertNick(nick)
                showUsers = false
            })
        }
    }
}

@OptIn(ExperimentalFoundationApi::class)
@Composable
private fun WindowList(onPick: () -> Unit) {
    Column(Modifier.padding(vertical = 12.dp)) {
        Text("☾ ${Client.network.name}", Modifier.padding(horizontal = 20.dp, vertical = 8.dp),
            color = Palette.yellow, fontSize = 20.sp, fontWeight = FontWeight.SemiBold)
        Text(
            if (Client.registered) "${Client.nick} · connected" else if (Client.connecting) "connecting…" else "offline",
            Modifier.padding(horizontal = 20.dp), color = Palette.dim, fontSize = 13.sp,
        )
        HorizontalDivider(Modifier.padding(vertical = 12.dp), color = Palette.surface)
        LazyColumn(Modifier.weight(1f)) {
            items(Client.buffers, key = { it.key }) { b ->
                val selected = b.key == Client.activeKey
                Row(
                    Modifier.fillMaxWidth()
                        .background(if (selected) Palette.surface else Color.Transparent)
                        .combinedClickable(onClick = { Client.switchTo(b.key); onPick() })
                        .padding(start = 20.dp, end = 8.dp, top = 4.dp, bottom = 4.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Text(
                        (if (b.kind == Kind.SERVER) "" else "  ") + b.name,
                        Modifier.weight(1f).padding(vertical = 8.dp),
                        color = when {
                            b.kind == Kind.CHANNEL && !b.joined -> Palette.dim
                            selected -> Palette.fg
                            else -> Palette.fg.copy(alpha = 0.85f)
                        },
                        fontFamily = FontFamily.Monospace,
                        fontWeight = if (selected) FontWeight.Bold else FontWeight.Normal,
                    )
                    if (b.unread > 0) {
                        Badge(containerColor = if (b.highlight) Palette.red else Palette.yellow) { Text("${b.unread}") }
                    }
                    if (b.kind != Kind.SERVER) {
                        IconButton(onClick = { Client.close(b) }) {
                            Icon(Icons.Default.Close, "Close ${b.name}", tint = Palette.dim)
                        }
                    }
                }
            }
        }
        HorizontalDivider(Modifier.padding(vertical = 8.dp), color = Palette.surface)
        Row(Modifier.padding(horizontal = 12.dp)) {
            TextButton(onClick = { Client.showNetworks = true; onPick() }) { Text("Switch network") }
            TextButton(onClick = { Client.submit("/reconnect"); onPick() }) { Text("Reconnect") }
            TextButton(onClick = { Client.quit() }) { Text("Quit", color = Palette.red) }
        }
    }
}

@OptIn(ExperimentalFoundationApi::class)
@Composable
private fun UserList(buffer: Buffer, onOpen: (String) -> Unit, onInsert: (String) -> Unit) {
    val users = buffer.users.entries
        .sortedWith(compareBy({ RANKS[it.value.firstOrNull()] ?: 9 }, { it.key.lowercase() }))
        .map { it.key to it.value }
    Column(Modifier.padding(bottom = 24.dp)) {
        Text("${buffer.name} · ${users.size} users", Modifier.padding(horizontal = 20.dp, vertical = 8.dp),
            color = Palette.dim, fontSize = 13.sp)
        Text("Tap to chat privately · long-press to mention", Modifier.padding(horizontal = 20.dp),
            color = Palette.dim, fontSize = 12.sp)
        LazyColumn(contentPadding = PaddingValues(vertical = 8.dp)) {
            items(users, key = { it.first }) { (nick, mode) ->
                Text(
                    buildAnnotatedString {
                        withStyle(SpanStyle(color = Palette.green, fontWeight = FontWeight.Bold)) { append(mode.take(1).ifEmpty { " " }) }
                        withStyle(SpanStyle(color = nickColour(nick))) { append(nick) }
                    },
                    Modifier.fillMaxWidth()
                        .combinedClickable(onClick = { onOpen(nick) }, onLongClick = { onInsert(nick) })
                        .padding(horizontal = 20.dp, vertical = 10.dp),
                    style = Mono,
                )
            }
        }
    }
}

@OptIn(ExperimentalFoundationApi::class)
@Composable
private fun CommandList(onPick: (Cmd) -> Unit) {
    Column(Modifier.padding(bottom = 24.dp)) {
        Text("Commands", Modifier.padding(horizontal = 20.dp, vertical = 4.dp),
            color = Palette.yellow, fontSize = 18.sp, fontWeight = FontWeight.SemiBold)
        Text("Tap one to use it — commands that need more go into the message box for you to finish.",
            Modifier.padding(horizontal = 20.dp), color = Palette.dim, fontSize = 12.sp)
        LazyColumn(contentPadding = PaddingValues(vertical = 8.dp)) {
            COMMANDS.groupBy { it.section }.forEach { (section, cmds) ->
                item(key = "h-$section") {
                    Text(section.uppercase(), Modifier.padding(start = 20.dp, top = 14.dp, bottom = 4.dp),
                        color = Palette.dim, fontSize = 11.sp, letterSpacing = 1.5.sp)
                }
                items(cmds, key = { it.template }) { cmd ->
                    Row(
                        Modifier.fillMaxWidth().combinedClickable(onClick = { onPick(cmd) })
                            .padding(horizontal = 20.dp, vertical = 9.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Text(cmd.template.trimEnd(), Modifier.width(150.dp), style = Mono.copy(color = Palette.cyan))
                        Text(cmd.description, color = Palette.fg, fontSize = 14.sp)
                    }
                }
            }
        }
    }
}

@Composable
private fun Messages(buffer: Buffer, modifier: Modifier) {
    val state = rememberLazyListState()
    val count = buffer.lines.size
    LaunchedEffect(buffer.key) { if (count > 0) state.scrollToItem(count - 1) }
    LaunchedEffect(count) {
        val last = state.layoutInfo.visibleItemsInfo.lastOrNull()?.index ?: 0
        if (count > 0 && last >= count - 4) state.animateScrollToItem(count - 1)
    }
    LazyColumn(modifier.fillMaxWidth(), state = state, contentPadding = PaddingValues(horizontal = 12.dp, vertical = 8.dp)) {
        itemsIndexed(buffer.lines) { _, line -> Text(render(line), style = Mono, modifier = Modifier.padding(vertical = 1.dp)) }
    }
}

private fun render(line: Line): AnnotatedString = buildAnnotatedString {
    withStyle(SpanStyle(color = Palette.dim)) { append(line.time + " ") }
    val nickStyle = SpanStyle(color = if (line.mine) Palette.fg else nickColour(line.nick), fontWeight = FontWeight.Bold)
    val bodyStyle = if (line.highlight) SpanStyle(color = Palette.bg, background = Palette.yellow) else SpanStyle(color = Palette.fg)
    when (line.style) {
        Style.CHAT -> {
            withStyle(SpanStyle(color = Palette.dim)) { append("<") }
            withStyle(nickStyle) { append(line.nick) }
            withStyle(SpanStyle(color = Palette.dim)) { append("> ") }
            withStyle(bodyStyle) { append(line.text) }
        }
        Style.ACTION -> {
            withStyle(nickStyle) { append("* ${line.nick} ") }
            withStyle(bodyStyle.merge(SpanStyle(fontStyle = FontStyle.Italic))) { append(line.text) }
        }
        Style.NOTICE -> {
            if (line.nick.isNotEmpty()) withStyle(SpanStyle(color = Palette.magenta, fontWeight = FontWeight.Bold)) { append("-${line.nick}- ") }
            withStyle(if (line.mine) SpanStyle(color = Palette.magenta) else bodyStyle) { append(line.text) }
        }
        Style.INFO -> withStyle(SpanStyle(color = Palette.dim, fontStyle = FontStyle.Italic)) { append(line.text) }
        Style.ERROR -> withStyle(SpanStyle(color = Palette.red, fontWeight = FontWeight.Bold)) { append(line.text) }
        Style.HINT -> withStyle(SpanStyle(color = Palette.yellow)) { append(line.text) }
        Style.EVENT -> withStyle(SpanStyle(color = Palette.dim)) { append(line.text) }
        Style.WHOIS -> withStyle(SpanStyle(color = Palette.cyan)) { append(line.text) }
        Style.MOTD -> withStyle(SpanStyle(color = Palette.fg.copy(alpha = 0.75f))) { append(line.text) }
    }
}

@Composable
private fun InputBar(
    value: TextFieldValue,
    onChange: (TextFieldValue) -> Unit,
    placeholder: String,
    onSend: () -> Unit,
    onCommands: () -> Unit,
    onComplete: () -> Unit,
) {
    Row(
        Modifier.fillMaxWidth().background(Palette.panel).padding(horizontal = 8.dp, vertical = 6.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        TextButton(onClick = onCommands) { Text("/", style = Mono.copy(fontSize = 20.sp, color = Palette.cyan, fontWeight = FontWeight.Bold)) }
        IconButton(onClick = onComplete) { Icon(Icons.Default.AlternateEmail, "Complete nick", tint = Palette.dim) }
        OutlinedTextField(
            value = value,
            onValueChange = onChange,
            placeholder = { Text(placeholder, color = Palette.dim, style = Mono) },
            textStyle = Mono.copy(color = Palette.fg),
            singleLine = true,
            keyboardOptions = KeyboardOptions(capitalization = KeyboardCapitalization.Sentences, imeAction = ImeAction.Send),
            keyboardActions = KeyboardActions(onSend = { onSend() }),
            modifier = Modifier.weight(1f).height(56.dp),
        )
        IconButton(onClick = onSend) { Icon(Icons.AutoMirrored.Filled.Send, "Send", tint = Palette.blue) }
    }
}
