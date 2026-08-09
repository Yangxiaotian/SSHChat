# Offline File Leave Bug Investigation

## 问题描述

当发送文件给离线账号时，如果接收者在联邦的另一个节点上登录并收到留言，发送者在原节点执行 `/leave` 命令时仍然能看到这条记录。

## 预期行为

1. Alice 在节点A发送文件给离线的 Bob
2. 节点A存储离线文件留言并广播到所有联邦节点
3. Bob 在节点B登录并收到文件留言
4. 节点B广播 `fleave_clear` 清理消息到所有节点
5. 节点A收到 `fleave_clear` 并删除本地的离线留言记录
6. Alice 在节点A执行 `/leave bob` 应该看不到这条消息

## 实际行为

第6步中，Alice仍然能看到这条消息。

## 调试信息

新增的日志将帮助诊断问题：

### 1. 发送文件时的日志

当离线文件被发送时，没有特殊日志（保持现有行为）。

### 2. 接收者登录时的日志

当Bob在节点B登录并收到离线文件时，会看到：

```
[FileTransfer] Broadcasting fleave_clear for bob (transfer_id=abc123)
```

如果transfer_id缺失，会看到：

```
[WARNING] deliver_offline_messages: file leave without transfer_id (from=alice, to=bob)
```

### 3. 其他节点收到清理消息时的日志

当节点A收到 `fleave_clear` 消息时，会看到：

```
[FileTransfer] Federation clear: removed 1 file leave(s) for bob (transfer_id=abc123)
```

或者如果没找到对应的消息：

```
[FileTransfer] Federation clear: no file leave found for bob (transfer_id=abc123)
```

## 可能的原因

### 原因1：transfer_id 缺失

如果在创建离线文件留言时 transfer_id 没有被正确设置，那么：
- 接收者登录时不会广播 `fleave_clear`（因为没有tid）
- 发送者的 `/leave` 列表中会继续显示这条消息

**诊断**：查看日志中是否有 "file leave without transfer_id" 警告

### 原因2：fleave_clear 没有被广播

如果节点B没有成功广播 `fleave_clear`：
- 其他节点不会收到清理通知
- 发送者的 `/leave` 列表中会继续显示这条消息

**诊断**：查看节点B的日志，应该有 "Broadcasting fleave_clear" 消息

### 原因3：fleave_clear 没有被接收

如果节点A没有收到节点B的 `fleave_clear` 广播：
- 节点A不会清理本地记录
- 可能是网络问题或联邦连接问题

**诊断**：
- 节点B有 "Broadcasting fleave_clear" 日志
- 但节点A没有对应的 "Federation clear" 日志

### 原因4：清理时找不到对应的消息

如果节点A收到了 `fleave_clear` 但找不到对应的离线留言：
- 可能是 transfer_id 不匹配
- 可能是消息已经被其他方式删除了

**诊断**：查看日志中 "no file leave found" 消息

## 复现步骤

1. 部署两个联邦节点（node-a 和 node-b）
2. 在node-a上以alice身份登录
3. 发送文件给离线的bob：`/sendfile bob`
4. 上传文件完成
5. 在node-b上以bob身份登录
6. Bob应该收到离线文件留言
7. 在node-a上执行：`/leave bob`
8. 检查是否还能看到发送给bob的文件记录

## 调试建议

1. 在两个节点上都启用详细日志
2. 执行复现步骤
3. 收集两个节点的日志输出
4. 查找上述4种可能原因对应的日志模式

## 单元测试

运行以下测试验证基本逻辑：

```bash
python3 test_offline_file_bug.py
```

这个测试验证了 `remove_file_by_transfer()` 的基本功能是否正常。

## 临时解决方案

如果遇到这个问题，发送者可以手动撤回留言：

```
/leave bob 1
```

这会删除本地记录并撤销文件下载权限。
