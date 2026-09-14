package chat.ssh.sshchat

/** Where the next text / file / voice message goes. */
sealed class SendTarget {
    abstract fun label(): String
    abstract fun sendfileCommand(): String

    data class CurrentRoom(val room: String) : SendTarget() {
        override fun label() = "当前房间 #$room"
        override fun sendfileCommand() = "/sendfile"
    }

    data class User(val nick: String) : SendTarget() {
        override fun label() = "私聊 $nick"
        override fun sendfileCommand() = "/sendfile $nick"
    }

    data class NamedRoom(val room: String) : SendTarget() {
        override fun label() = "房间 #$room"
        override fun sendfileCommand() = "/sendfile #$room"
    }

    companion object {
        fun outboundText(target: SendTarget, draft: String): String {
            val t = draft.trim()
            if (t.startsWith("/")) return draft
            return when (target) {
                is CurrentRoom -> draft
                is User -> "/msg ${target.nick} $draft"
                is NamedRoom -> "/msg #${target.room} $draft"
            }
        }
    }
}
