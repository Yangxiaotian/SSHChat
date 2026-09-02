import SwiftUI

struct WatchRootView: View {
    @StateObject private var vm = WatchChatViewModel()
    @Environment(\.scenePhase) private var scenePhase

    var body: some View {
        NavigationView {
            Group {
                if vm.connected {
                    WatchChatView(vm: vm)
                } else {
                    WatchConnectView(vm: vm)
                }
            }
            .navigationTitle("SSHChat")
        }
        .onChange(of: scenePhase) { phase in
            if phase == .active {
                vm.handleBecameActive()
            }
        }
        .alert(isPresented: Binding(
            get: { vm.toast != nil },
            set: { if !$0 { vm.toast = nil } }
        )) {
            Alert(
                title: Text("提示"),
                message: Text(vm.toast ?? ""),
                dismissButton: .cancel(Text("好")) { vm.toast = nil }
            )
        }
    }
}

struct WatchConnectView: View {
    @ObservedObject var vm: WatchChatViewModel

    var body: some View {
        Form {
            Section(header: Text("服务器")) {
                TextField("用户名", text: $vm.username)
                    .disableAutocorrection(true)
                TextField("主机", text: $vm.sshHost)
                    .disableAutocorrection(true)
                TextField("端口", text: $vm.sshPort)
                    .disableAutocorrection(true)
            }
            Section(footer: Text(vm.status)) {
                Button(action: { vm.connect() }) {
                    if vm.busy {
                        ProgressView()
                    } else {
                        Text("连接")
                    }
                }
                .disabled(vm.busy)
            }
            Section(header: Text("本机公钥")) {
                Button("查看公钥") { vm.showPubkey = true }
                Text(vm.keys.comment)
                    .font(.caption2)
                    .foregroundColor(.secondary)
            }
        }
        .sheet(isPresented: $vm.showPubkey) {
            NavigationView {
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
    @State private var showQuick = false

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
                .onChange(of: vm.rows.count) { _ in
                    if let last = vm.rows.last?.id {
                        withAnimation { proxy.scrollTo(last, anchor: .bottom) }
                    }
                }
            }

            TextField("消息 / 听写", text: $vm.draft)
                .disableAutocorrection(true)
                .onSubmit { vm.sendDraft() }

            HStack(spacing: 6) {
                Button("发") { vm.sendDraft() }
                    .disabled(vm.draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                Button("快") { showQuick = true }
                Button("#") { vm.setRoomTarget() }
                    .font(.caption2)
            }
            .font(.caption)
        }
        .padding(.horizontal, 2)
        .sheet(isPresented: $showQuick) {
            NavigationView {
                List {
                    ForEach(WatchChatViewModel.quickReplies, id: \.self) { q in
                        Button(q) {
                            vm.sendQuick(q)
                            showQuick = false
                        }
                    }
                }
                .navigationTitle("快捷回复")
            }
        }
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
                            .foregroundColor(.secondary)
                    }
                    Text(sender)
                        .foregroundColor(mine ? .blue : .primary)
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
                    .foregroundColor(.orange)
                Text(body)
                    .font(.system(size: 13))
            }
        case .system(_, let text):
            Text(text)
                .font(.system(size: 11))
                .foregroundColor(.secondary)
                .frame(maxWidth: .infinity, alignment: .center)
        }
    }
}
