#!/usr/bin/env python3
"""Check /sendfile argument parsing against the real server handler."""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

tmpdir = tempfile.mkdtemp(prefix="sshchat_cmd_")
os.environ["SSHCHAT_FILE_STORAGE_DIR"] = os.path.join(tmpdir, "files")
os.environ["SSHCHAT_FILE_TRANSFER_STORE"] = os.path.join(tmpdir, "transfers.json")

import server


class FakeHTTP:
    def get_base_url(self):
        return "https://example.com:8443"


alice, bob, carol = object(), object(), object()

server.file_http = FakeHTTP()
server.clients.clear()
server.clients.update({
    alice: {"name": "alice", "rooms": {"dev"}, "current_room": "dev"},
    bob: {"name": "bob", "rooms": {"dev"}, "current_room": "dev"},
    carol: {"name": "carol", "rooms": {"dev"}, "current_room": "dev"},
})
server.rooms.clear()
server.rooms.update({"dev": {alice, bob, carol}, "quiet": {alice}})

captured = []
server.send_line = lambda conn, text: captured.append(text)


def run(payload):
    captured.clear()
    server._handle_sendfile(alice, "alice", payload)
    return "".join(captured)


def transfers():
    return list(server.file_sharing.file_transfer_store.transfers.values())


def latest():
    return max(transfers(), key=lambda t: t.created_at)


# 1. No argument at all: goes to the sender's current room
out = run("/sendfile")
assert "房间 #dev (2 人)" in out, out
t = latest()
assert t.room == "dev" and set(t.download_tokens) == {"bob", "carol"}, t
assert t.filename == "", t.filename
assert t.upload_key not in out.split("上传网址:")[1].split("上传密钥")[0], "key leaked into URL"
print("1. /sendfile 默认发到当前房间 #dev，收件人 bob/carol")

# 2. Nickname target
out = run("/sendfile bob")
assert "接收者: bob" in out, out
t = latest()
assert t.room is None and set(t.download_tokens) == {"bob"}
print("2. /sendfile bob 发给单个用户")

# 3. Explicit room target
out = run("/sendfile #dev")
t = latest()
assert t.room == "dev" and set(t.download_tokens) == {"bob", "carol"}
print("3. /sendfile #dev 发到指定房间")

# 4. Old syntax with a filename still works, with a hint
out = run("/sendfile bob report.pdf")
assert "不用再写文件名" in out, out
t = latest()
assert set(t.download_tokens) == {"bob"} and t.filename == ""
print("4. 旧写法 /sendfile bob report.pdf 仍可用，多余文件名被忽略并提示")

# 5. Room with nobody else
server.clients[alice]["current_room"] = "quiet"
out = run("/sendfile")
assert "没有其他用户" in out, out
server.clients[alice]["current_room"] = "dev"
print("5. 空房间给出友好提示")

# 6. Room the sender is not in
out = run("/sendfile #nosuch")
assert "不存在" in out, out
print("6. 不存在的房间被拒绝")

# 7. Help
out = run("/sendfile help")
assert "/sendfile <昵称>" in out and "文件名不用写" in out, out
print("7. /sendfile help 展示新用法")

# 8. The URL handed to the sender carries no key
out = run("/sendfile bob")
t = latest()
assert f"/upload/{t.upload_token}\n" in out, out
assert "?key=" not in out, out
print("8. 上传网址不含 ?key=，密钥单独一行给出")

# 9. The notification recipients get once the upload lands
store = server.file_sharing.file_transfer_store
uploaded = os.path.join(tmpdir, "上传的文件.png")
with open(uploaded, "wb") as f:
    f.write(b"x" * 2048)
store.upload_complete_callback = None
store.mark_upload_complete(t.upload_token, uploaded, 2048, "上传的文件.png")

captured.clear()
server._notify_file_ready(store.get_transfer_by_token(t.upload_token))
notice = "".join(captured)
assert "文件名: 上传的文件.png" in notice, notice
assert "?key=" not in notice, notice
assert store.get_transfer_by_token(t.upload_token).download_keys["bob"] in notice
assert "只能下载一次" in notice, notice
print("9. 接收者通知含真实文件名，网址与密钥分开给出")

# 10. Offline recipient: leave-message + /leave list/recall
dave = object()
del server.clients[bob]
server.clients[dave] = {"name": "dave", "rooms": {"dev"}, "current_room": "dev"}
out = run("/sendfile eve")
t = latest()
assert set(t.download_tokens) == {"eve"}, t
uploaded2 = os.path.join(tmpdir, "给离线用户.txt")
with open(uploaded2, "wb") as f:
    f.write(b"offline-file")
store.mark_upload_complete(t.upload_token, uploaded2, len(b"offline-file"), "给离线用户.txt")
captured.clear()
prev_box = server.offline_messages.count("eve")
server._notify_file_ready(store.get_transfer_by_token(t.upload_token))
assert "".join(captured) == "", "offline recipient should not get live notice"
assert server.offline_messages.count("eve") == prev_box + 1
listed = server.offline_messages.list_sent_unread("alice", "eve")
assert listed and listed[-1]["kind"] == "file"
assert "[文件] 给离线用户.txt" in listed[-1]["text"]
assert listed[-1]["meta"]["transfer_id"] == t.transfer_id

login = []
server.send_line = lambda conn, text: login.append(text)
n = server.deliver_offline_messages(object(), "Eve")
assert n >= 1
joined = "".join(login)
assert "收到新文件" in joined and "给离线用户.txt" in joined
assert t.download_keys["eve"] in joined
server.send_line = lambda conn, text: captured.append(text)
print("10. 离线收件人上线后能看到文件通知")

# 11. /leave can list and recall a pending offline file (revokes download)
out = run("/sendfile ghost")
t = latest()
uploaded3 = os.path.join(tmpdir, "可撤回.bin")
with open(uploaded3, "wb") as f:
    f.write(b"revoke-me")
store.mark_upload_complete(t.upload_token, uploaded3, 9, "可撤回.bin")
server._notify_file_ready(store.get_transfer_by_token(t.upload_token))
assert server.offline_messages.count("ghost") >= 1
captured.clear()
server.handle_leave_command(alice, "alice", ["/leave", "ghost"])
listed_out = "".join(captured)
assert "[文件] 可撤回.bin" in listed_out, listed_out
captured.clear()
server.handle_leave_command(alice, "alice", ["/leave", "ghost 1"])
ack = "".join(captured)
assert "已撤回" in ack and "文件" in ack, ack
assert server.offline_messages.list_sent_unread("alice", "ghost") == []
assert "ghost" not in store.get_transfer_by_token(t.upload_token).download_tokens
print("11. /leave 可查看并撤回离线文件，下载权同步作废")

# 12. Room /sendfile includes federated remote members
class FakeFedHub:
    enabled = True

    def names_in_room(self, room):
        return ["remote_bob"] if room == "dev" else []

    def has_remote_user(self, nick):
        return nick.lower() == "remote_bob"

    def send_file_notice(self, to_nick, from_name, notice):
        FakeFedHub.last = (to_nick, from_name, notice)
        return True


FakeFedHub.last = None
server.clients[bob] = {"name": "bob", "rooms": {"dev"}, "current_room": "dev"}
server.rooms["dev"].add(bob)
import federation as _fed

_prev_get = _fed.get_hub
_fed.get_hub = lambda: FakeFedHub()
try:
    out = run("/sendfile #dev")
    t = latest()
    assert "remote_bob" in t.download_tokens, t.download_tokens
    assert "bob" in t.download_tokens
    assert "remote_bob" in out or "人" in out
    print("12. /sendfile #dev 收件人含联邦远端 remote_bob")

    uploaded4 = os.path.join(tmpdir, "fed.txt")
    with open(uploaded4, "wb") as f:
        f.write(b"federated")
    store.mark_upload_complete(t.upload_token, uploaded4, 9, "fed.txt")
    FakeFedHub.last = None
    captured.clear()
    server._notify_file_ready(store.get_transfer_by_token(t.upload_token))
    assert FakeFedHub.last is not None, "should federate notice to remote_bob"
    assert FakeFedHub.last[0].lower() == "remote_bob"
    assert FakeFedHub.last[2]["download_url"].startswith("https://example.com:8443/download/")
    assert FakeFedHub.last[2]["download_key"] == t.download_keys["remote_bob"]
    print("13. 上传完成后向联邦远端发送 fnotice（含公网 download_url）")
finally:
    _fed.get_hub = _prev_get

print("\n✅ /sendfile 参数解析全部通过")
