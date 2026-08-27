import SwiftUI

struct WatchRootView: View {
    @StateObject private var vm = WatchChatViewModel()
    @Environment(\.scenePhase) private var scenePhase

    var body: some View {
        NavigationStack {
            Group {
                if vm.connected {
                    WatchChatView(vm: vm)
                } else {
                    WatchConnectView(vm: vm)
                }
            }
            .navigationTitle("SSHChat")
            .navigationBarTitleDisplayMode(.inline)
        }
        .onChange(of: scenePhase) { _, phase in
            if phase == .active {
                vm.handleBecameActive()
            }
        }
        .alert("提示", isPresented: Binding(
            get: { vm.toast != nil },
            set: { if !$0 { vm.toast = nil } }
        )) {
            Button("好", role: .cancel) { vm.toast = nil }
        } message: {
            Text(vm.toast ?? "")
        }
    }
}

struct WatchConnectView: View {
    @ObservedObject var vm: WatchChatViewModel

    var body: some View {
        Form {
            Section("服务器") {
                TextField("用户名", text: $vm.username)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                TextField("主机", text: $vm.sshHost)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                TextField("端口", text: $vm.sshPort)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
            }
            Section {
                Button {
                    vm.connect()
                } label: {
                    if vm.busy {
                        ProgressView()
                    } else {
                        Text("连接")
                    }
                }
                .disabled(vm.busy)
            } footer: {
                Text(vm.status)
            }
            Section("本机公钥") {
                Button("查看公钥") { vm.showPubkey = true }
                Text(vm.keys.comment)
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }
        }
        .sheet(isPresented: $vm.showPubkey) {
            NavigationStack {
                ScrollView {
                    Text(vm.keys.publicOpenSshLine)
                        .font(.system(.caption2, design: .monospaced))
                        .padding()
                }
                .navigationTitle("公钥")
                .toolbar {
                    ToolbarItem(placement: .cancellationAction) {
                        Button("关闭") { vm.showPubkey = false }
                    }
                }
            }
        }
    }
}

struct WatchChatView: View {
    @ObservedObject var vm: WatchChatViewModel

    var body: some View {
        VStack(spacing: 4) {
            HStack {
                Circle()
                    .fill(vm.connected ? Color.green : Color.orange)
                    .frame(width: 6, height: 6)
                Text(vm.status)
                    .font(.caption2)
                    .lineLimit(1)
                Spacer(minLength: 0)
                Button("断") { vm.disconnect() }
                    .font(.caption2)
            }
            .padding(.horizontal, 2)

            ScrollViewReader { proxy in
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 4) {
                        ForEach(vm.rows) { row in
                            WatchRowView(row: row)
                                .id(row.id)
                        }
                    }
                }
                .onChange(of: vm.rows.count) { _, _ in
                    if let last = vm.rows.last?.id {
                        withAnimation { proxy.scrollTo(last, anchor: .bottom) }
                    }
                }
            }

            TextField("消息 / 听写", text: $vm.draft)
                .textInputAutocapitalization(.never)
                .onSubmit { vm.sendDraft() }

            HStack(spacing: 6) {
                Button("发") { vm.sendDraft() }
                    .buttonStyle(.borderedProminent)
                    .disabled(vm.draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                Menu("快") {
                    ForEach(WatchChatViewModel.quickReplies, id: \.self) { q in
                        Button(q) { vm.sendQuick(q) }
                    }
                }
                Button("#") { vm.setRoomTarget() }
                    .font(.caption2)
            }
            .font(.caption)
        }
        .padding(.horizontal, 2)
    }
}

private struct WatchRowView: View {
    let row: WatchChatRow

    var body: some View {
        switch row {
        case .bubble(_, let mine, let room, let sender, let body):
            VStack(alignment: mine ? .trailing : .leading, spacing: 1) {
                HStack(spacing: 2) {
                    if let room {
                        Text("#\(room)")
                            .foregroundStyle(.secondary)
                    }
                    Text(sender)
                        .foregroundStyle(mine ? .blue : .primary)
                }
                .font(.system(size: 10, weight: .semibold))
                Text(body)
                    .font(.system(size: 13))
                    .frame(maxWidth: .infinity, alignment: mine ? .trailing : .leading)
            }
        case .pm(_, let from, let body):
            VStack(alignment: .leading, spacing: 1) {
                Text("PM \(from)")
                    .font(.system(size: 10, weight: .semibold))
                    .foregroundStyle(.orange)
                Text(body)
                    .font(.system(size: 13))
            }
        case .system(_, let text):
            Text(text)
                .font(.system(size: 11))
                .foregroundStyle(.secondary)
                .frame(maxWidth: .infinity, alignment: .center)
        }
    }
}
