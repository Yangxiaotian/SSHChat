import SwiftUI

struct SendTargetPickerView: View {
    @ObservedObject var model: ChatViewModel
    @Environment(\.dismiss) private var dismiss
    @State private var roomDraft = ""
    @State private var showRoomPrompt = false
    @State private var userDraft = ""
    @State private var showUserPrompt = false

    var body: some View {
        NavigationStack {
            List {
                Section("房间") {
                    Button {
                        model.setSendTarget(.currentRoom(SendTargetStore.loadCurrentRoom()))
                        dismiss()
                    } label: {
                        Text("当前房间 #\(SendTargetStore.loadCurrentRoom())")
                    }
                    Button("指定其他房间 #…") { showRoomPrompt = true }
                    Button("指定用户（可离线留言/发文件）…") { showUserPrompt = true }
                }
                if !userChoices.isEmpty {
                    Section("私聊") {
                        ForEach(userChoices, id: \.self) { nick in
                            Button("私聊 \(nick)") {
                                model.setSendTarget(.user(nick))
                                dismiss()
                            }
                        }
                    }
                }
                Section {
                    Button("刷新在线用户") {
                        model.refreshOnlineUsers()
                    }
                }
            }
            .navigationTitle("发送至")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("关闭") { dismiss() }
                }
            }
            .alert("发送到房间", isPresented: $showRoomPrompt) {
                TextField("房间名（不含 #）", text: $roomDraft)
                Button("确定") {
                    let r = roomDraft.trimmingCharacters(in: .whitespacesAndNewlines)
                        .replacingOccurrences(of: "#", with: "")
                    if !r.isEmpty {
                        model.setSendTarget(.namedRoom(r))
                        dismiss()
                    }
                }
                Button("取消", role: .cancel) {}
            }
            .alert("私聊指定用户", isPresented: $showUserPrompt) {
                TextField("昵称", text: $userDraft)
                Button("确定") {
                    let n = userDraft.trimmingCharacters(in: .whitespacesAndNewlines)
                    if !n.isEmpty {
                        model.setSendTarget(.user(n))
                        dismiss()
                    }
                }
                Button("取消", role: .cancel) {}
            } message: {
                Text("对方不在线时，文字与文件都会以留言形式送达。")
            }
        }
    }

    private var userChoices: [String] {
        let me = model.username.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        var seen = Set<String>()
        var out: [String] = []
        func add(_ nick: String) {
            let n = nick.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !n.isEmpty, n.lowercased() != me else { return }
            let key = n.lowercased()
            guard seen.insert(key).inserted else { return }
            out.append(n)
        }
        model.onlineUsers.forEach { add($0) }
        SendTargetStore.loadRecentUsers().forEach { add($0) }
        return out
    }
}
