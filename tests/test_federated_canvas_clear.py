#!/usr/bin/env python3
"""Round-switch canvas clear (drawguess) must also reach federation-hosted boards.

Regression test: when a room's canvas board is actually hosted on a peer node
(local record is only a mirror with host_node set), the previous
clear_open_room_board() call silently no-opped and the old drawing stayed on
screen for the next drawer.
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

tmpdir = tempfile.mkdtemp(prefix="sshchat_canvas_clear_")
os.environ["SSHCHAT_CANVAS_STORE"] = os.path.join(tmpdir, "canvas_sessions.json")
os.environ["SSHCHAT_FILE_STORAGE_DIR"] = os.path.join(tmpdir, "files")
os.environ["SSHCHAT_FILE_TRANSFER_STORE"] = os.path.join(tmpdir, "transfers.json")
os.environ["SSHCHAT_DEFAULT_LOCALE"] = "zh"

import server  # noqa: E402
import canvas_sharing  # noqa: E402

store = canvas_sharing.canvas_store

# 1. Board hosted locally: clear_open_room_board() clears it directly, no
#    federation call needed.
local_session = store.create_session(creator="alice", participants=["bob"], room="dev")
local_session.elements = [{"id": "x"}]
local_session.rev = 1
broadcast_log = []
server.broadcast_game = lambda room, lines: broadcast_log.append((room, lines))


class FakeGame:
    def __init__(self, actions):
        self._actions = list(actions)

    def drain_canvas_actions(self):
        out, self._actions = self._actions, []
        return out


server._apply_game_canvas_actions("dev", FakeGame(["clear"]))
assert local_session.elements == [], "locally hosted board should clear directly"
assert broadcast_log and "已清空" in broadcast_log[-1][1][0], broadcast_log
print("1. 本机托管的画板：直接清空，无需联邦转发")

# 2. Board hosted on a federation peer: local record is only a mirror
#    (host_node set) — clear must be forwarded to that peer.
store.register_remote_session(
    session_id="remote-sess",
    creator="alice",
    participants=["alice", "bob"],
    room="fedroom",
    tokens={"alice": "tok-a", "bob": "tok-b"},
    keys={"alice": "KEYAAA", "bob": "KEYBBB"},
    host_node="peerB",
    host_base_url="https://peer-b.example",
)

calls = []


class FakeHub:
    enabled = True


def fake_request_canvas_clear(host_node, room, **kwargs):
    calls.append((host_node, room))
    return {"ok": True, "cleared": True}


server.federation.get_hub = lambda: FakeHub()
server._federation_request_canvas_clear = fake_request_canvas_clear

broadcast_log.clear()
server._apply_game_canvas_actions("fedroom", FakeGame(["clear"]))
assert calls == [("peerB", "fedroom")], calls
assert broadcast_log and "已清空" in broadcast_log[-1][1][0], broadcast_log
print("2. 画板托管在联邦节点：本地记录只是镜像，清空请求被转发给宿主节点 peerB")

# 3. Forward fails / peer says nothing was cleared: no false "cleared" broadcast.
calls.clear()
broadcast_log.clear()
server._federation_request_canvas_clear = lambda host_node, room, **kw: {
    "ok": False,
    "cleared": False,
}
server._apply_game_canvas_actions("fedroom", FakeGame(["clear"]))
assert broadcast_log == [], broadcast_log
print("3. 联邦转发失败时不会误报清空成功")

# 4. The receiving side (the node that actually hosts the board) must honor
#    mode=canvas_clear and clear its locally-hosted session.
hosted = store.create_session(creator="alice", participants=["bob"], room="dev2")
hosted.elements = [{"id": "y"}]
hosted.rev = 5

replies = []


class FakeHub2:
    node_id = "peerB"

    def reply_file_host(self, requester, req_id, reply):
        replies.append((requester, req_id, reply))


server.federation.get_hub = lambda: FakeHub2()
server._fed_on_file_host_request(
    "peerA", "req-1", {"mode": "canvas_clear", "room": "dev2"}
)
assert hosted.elements == [], "host node should clear its own board"
assert replies == [("peerA", "req-1", {
    "ok": True,
    "cleared": True,
    "req_id": "req-1",
    "mode": "canvas_clear",
})], replies
print("4. 宿主节点收到 canvas_clear 请求后正确清空并回复")

print("\n✅ 联邦画板清空转发测试全部通过")
