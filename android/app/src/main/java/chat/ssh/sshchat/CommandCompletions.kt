package chat.ssh.sshchat

/** Same command completion rules as sshchat_gui / client.py (incl. room & nick args). */
object CommandCompletions {
    private val TOP = listOf(
        "/help", "/lang", "/language", "/names", "/users", "/rooms",
        "/join", "/switch", "/part", "/msg", "/sendfile", "/file",
        "/canvas", "/board", "/leave", "/unmsg", "/announce", "/game",
        "/news", "/library", "/lib", "/dict", "/clear", "/cls", "/dnd",
    )

    private val SUBS = mapOf(
        "/game" to listOf(
            "help", "list", "new", "join", "show", "move", "resign",
            "undo", "abort", "end", "on", "off", "seats", "rating", "pgn",
        ),
        "/news" to listOf("中文", "国际", "科技", "all", "detail", "详情", "fetch", "全文"),
        "/library" to listOf(
            "open", "read", "next", "n", "prev", "p", "page", "find", "search",
            "bookmarks", "bookmark", "reset", "close", "info", "show", "help",
        ),
        "/lib" to listOf(
            "open", "read", "next", "n", "prev", "p", "page", "find", "search",
            "bookmarks", "bookmark", "reset", "close", "info", "show", "help",
        ),
        "/dict" to listOf("en", "cn", "hh", "help", "英", "中", "汉"),
        "/dnd" to listOf("on", "off"),
        "/lang" to listOf("en", "zh", "english", "chinese", "中文", "英文"),
        "/language" to listOf("en", "zh", "english", "chinese", "中文", "英文"),
    )

    private val NESTED = mapOf(
        ("/game" to "undo") to listOf("accept", "reject", "cancel"),
    )

    private val ROOM_ARG_CMDS = setOf("/join", "/switch", "/part")
    private val USER_OR_ROOM_ARG_CMDS = setOf("/msg", "/sendfile", "/file")
    private val USER_ARG_CMDS = setOf("/leave", "/unmsg")

    private fun sorted(items: List<String>, defaultOrder: List<String>): List<String> {
        if (items.isEmpty()) return items
        return CommandUsage.sort(items, defaultOrder)
    }

    private fun uniq(items: Iterable<String>): List<String> {
        val seen = linkedSetOf<String>()
        val out = mutableListOf<String>()
        for (raw in items) {
            val key = raw.trim()
            if (key.isEmpty()) continue
            val low = key.lowercase()
            if (!seen.add(low)) continue
            out.add(key)
        }
        return out
    }

    fun nameArgCompletions(
        text: String,
        rooms: List<String> = emptyList(),
        users: List<String> = emptyList(),
    ): List<String> {
        if (!text.startsWith("/")) return emptyList()
        val trailingSpace = text.endsWith(" ")
        val parts = text.trimEnd().split(Regex("\\s+")).filter { it.isNotEmpty() }
        if (parts.isEmpty()) return emptyList()
        val cmd = parts[0].lowercase()
        val roomNames = uniq(rooms.map { it.trim().trimStart('#') })
        val userNames = uniq(users)

        val cands = when (cmd) {
            in ROOM_ARG_CMDS -> roomNames
            in USER_OR_ROOM_ARG_CMDS -> userNames + roomNames.map { "#$it" }
            in USER_ARG_CMDS -> userNames
            else -> return emptyList()
        }

        if (trailingSpace && parts.size == 1) {
            return cands.map { "${parts[0]} $it" }
        }
        if (parts.size >= 2 && !trailingSpace) {
            val prefix = parts[1]
            val pl = prefix.lowercase()
            val bare = pl.trimStart('#')
            val matched = cands.filter { c ->
                val cl = c.lowercase()
                when {
                    pl == "#" -> c.startsWith("#")
                    cl.startsWith(pl) -> true
                    c.startsWith("#") && c.drop(1).lowercase().startsWith(bare) -> true
                    !c.startsWith("#") && cl.startsWith(bare) && prefix.startsWith("#") -> true
                    else -> false
                }
            }
            return matched.map { "${parts[0]} $it" }
        }
        return emptyList()
    }

    fun completions(
        text: String,
        rooms: List<String> = emptyList(),
        users: List<String> = emptyList(),
    ): List<String> {
        if (!text.startsWith("/")) return emptyList()
        if (" " !in text) {
            return sorted(TOP.filter { it.startsWith(text) }, TOP)
        }

        val parts = text.trimEnd().split(Regex("\\s+")).filter { it.isNotEmpty() }
        val trailingSpace = text.endsWith(" ")
        if (parts.isEmpty()) return emptyList()
        val cmd = parts[0].lowercase()

        if (parts.size == 1 && !trailingSpace) {
            return sorted(TOP.filter { it.startsWith(parts[0]) }, TOP)
        }

        if (parts.size >= 2) {
            val sub = parts[1].lowercase()
            val nestedItems = NESTED[cmd to sub].orEmpty()
            if (nestedItems.isNotEmpty()) {
                val nestedFull = nestedItems.map { "${parts[0]} ${parts[1]} $it" }
                if (trailingSpace && parts.size == 2) {
                    return sorted(nestedFull, nestedFull)
                }
                if (parts.size >= 3 && !trailingSpace) {
                    val prefix = parts[2]
                    return sorted(
                        nestedItems.filter { it.startsWith(prefix) }.map { "${parts[0]} ${parts[1]} $it" },
                        nestedFull,
                    )
                }
                if (!(parts.size == 2 && !trailingSpace)) {
                    return emptyList()
                }
            }
        }

        val subs = SUBS[cmd].orEmpty()
        if (subs.isNotEmpty()) {
            val subsFull = subs.map { "${parts[0]} $it" }
            if (trailingSpace && parts.size == 1) {
                return sorted(subsFull, subsFull)
            }
            if (parts.size >= 2 && !trailingSpace) {
                val prefix = parts[1]
                return sorted(
                    subs.filter { it.startsWith(prefix) }.map { "${parts[0]} $it" },
                    subsFull,
                )
            }
            return emptyList()
        }

        val nameItems = nameArgCompletions(text, rooms, users)
        return sorted(nameItems, nameItems)
    }

    fun longestCommonPrefix(values: List<String>): String {
        if (values.isEmpty()) return ""
        var prefix = values[0]
        for (v in values.drop(1)) {
            while (!v.startsWith(prefix)) {
                prefix = prefix.dropLast(1)
                if (prefix.isEmpty()) return ""
            }
        }
        return prefix
    }

    /** Tab-like: unique match → fill + space; else extend shared prefix. */
    fun applyTab(
        text: String,
        rooms: List<String> = emptyList(),
        users: List<String> = emptyList(),
    ): String? {
        if (!text.startsWith("/")) return null
        val items = completions(text, rooms, users)
        if (items.isEmpty()) return null
        if (items.size == 1) {
            val one = items[0]
            return if (one.endsWith(" ")) one else "$one "
        }
        val shared = longestCommonPrefix(items)
        return if (shared.length > text.length) shared else null
    }
}
