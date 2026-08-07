# 安全文件传输功能说明

## ✅ 已完成功能

### 核心功能

1. **一次性上传 URL + 密钥**
   - ✅ 发送者收到系统私发的上传 URL 和 6 位随机密钥
   - ✅ 上传前必须输入密钥验证
   - ✅ 上传成功后 URL 立即作废

2. **一次性下载 URL + 密钥**
   - ✅ 每个接收者收到独立的下载 URL 和密钥
   - ✅ 房间内每个成员的 URL 和密钥都不同
   - ✅ 下载成功后 URL 立即作废

3. **HTTPS 自动配置**
   - ✅ 支持 Let's Encrypt 自动申请证书
   - ✅ 支持自签名证书作为备选
   - ✅ 域名和端口可在部署时指定

## 📁 实现细节

### 新增文件

#### 1. `file_sharing.py` (362 行)
核心传输管理模块：
- `FileTransfer` 数据类
- `FileTransferStore` 管理类
- 令牌/密钥生成
- 上传/下载验证
- 状态持久化
- 自动清理过期传输

#### 2. `file_http_server.py` (321 行)
HTTP/HTTPS 服务器：
- 上传端点：`POST /upload/<token>?key=<key>`
- 下载端点：`GET /download/<token>?key=<key>`
- HTTPS 支持（Let's Encrypt / 自签名）
- CORS 支持
- 文件大小验证

#### 3. `test_file_sharing_simple.py` (170 行)
完整的测试套件：
- 10 个测试用例
- 覆盖所有核心功能
- 所有测试通过 ✅

### 修改文件

#### 1. `server.py` (+110 行)
- 导入文件传输模块
- 新增 `/sendfile` 命令
- 通知接收者文件已就绪
- 启动时初始化 HTTP 服务器
- 定期清理过期传输
- 优雅关闭时停止 HTTP 服务器

#### 2. `deploy.sh` (+18 行)
- 新增命令行选项：
  - `--file-domain DOMAIN` - 域名（用于 Let's Encrypt）
  - `--file-port N` - HTTP/HTTPS 端口
  - `--no-file-https` - 禁用 HTTPS
  - `--no-file-transfer` - 禁用文件传输
- 自动配置 `sshchat.env`
- 复制新模块文件
- 设置正确的文件权限

## 🎮 使用方法

### 命令格式

#### 发送给用户
```bash
/sendfile <昵称> <文件名>
/sendfile alice document.pdf
```

#### 发送到房间
```bash
/sendfile #<房间> <文件名>
/sendfile #dev screenshot.png
```

### 发送者收到

```
========== 文件上传信息 ==========
文件名: document.pdf
接收者: alice

上传 URL (一次性):
https://your-domain.com:8443/upload/UoGX...?key=MUJVB2

上传密钥: MUJVB2

说明:
1. 访问上传 URL
2. 输入密钥并上传文件
3. 上传成功后此 URL 立即作废
4. 接收者将收到各自的下载 URL 和密钥
=====================================
```

### 接收者收到

```
========== 收到新文件 ==========
发件人: bob
文件名: document.pdf
大小: 245.3 KB
来自房间: #dev (可选)

下载 URL (一次性):
https://your-domain.com:8443/download/ffh6...?key=R9S3PJ

下载密钥: R9S3PJ

说明:
1. 访问下载 URL
2. 输入密钥下载文件
3. 下载成功后此 URL 立即作废
4. 每个接收者的 URL 和密钥都不同
================================
```

## 🚀 部署配置

### 基本部署

```bash
# 使用域名 + Let's Encrypt
sudo ./deploy.sh --file-domain files.example.com --file-port 8443

# 使用自签名证书（无域名）
sudo ./deploy.sh --file-port 8443

# 使用 HTTP（不推荐）
sudo ./deploy.sh --no-file-https --file-port 8080
```

### 环境变量

在 `sshchat.env` 中：

```bash
# 文件传输配置
SSHCHAT_FILE_TRANSFER_ENABLED=1
SSHCHAT_FILE_HTTP_PORT=8443
SSHCHAT_FILE_USE_HTTPS=1
SSHCHAT_FILE_DOMAIN=files.example.com

# 可选配置
SSHCHAT_FILE_STORAGE_DIR=/var/lib/sshchat/files
SSHCHAT_MAX_FILE_SIZE=104857600  # 100MB
```

### 防火墙配置

确保开放文件传输端口：

```bash
# 允许文件传输端口
sudo ufw allow 8443/tcp

# Let's Encrypt 验证需要 80 端口
sudo ufw allow 80/tcp
```

## 🔒 安全特性

### 1. 一次性使用保护
- 每个 URL 只能使用一次
- 使用后立即失效
- 防止链接泄露

### 2. 密钥验证
- 6 位随机密钥（A-Z, 0-9）
- 访问 URL 时必须提供正确密钥
- 密钥通过私聊传递

### 3. 隔离设计
- 房间内每个成员获得独立凭证
- 无法通过一个凭证访问其他人的下载
- 追踪每个下载的使用状态

### 4. 自动过期
- 上传 URL：60 分钟过期
- 下载 URL：24 小时过期
- 过期文件自动清理（每小时检查）

### 5. HTTPS 加密
- 优先使用 Let's Encrypt 证书
- 备选自签名证书
- 传输过程全程加密

## 📊 测试报告

所有测试用例通过：

```
1. Testing token/key generation...        ✓
2. Creating upload session...             ✓
3. Validating recipients...               ✓
4. Validating upload...                   ✓
5. Testing wrong key rejection...         ✓
6. Marking upload complete...             ✓
7. Validating download...                 ✓
8. Marking download complete...           ✓
9. Testing download URL reuse prevention..✓
10. Testing room transfer...              ✓
```

运行测试：
```bash
cd /workspace
python3 test_file_sharing_simple.py
```

## 📝 技术细节

### 架构设计

```
Client (SSH)
    ↓ /sendfile command
Server (server.py)
    ↓ create transfer session
FileTransferStore (file_sharing.py)
    ↓ generate tokens & keys
    ↓ save to JSON
HTTP Server (file_http_server.py)
    ↓ POST /upload/<token>?key=<key>
    ↓ GET /download/<token>?key=<key>
File Storage (/tmp/sshchat_files)
```

### 数据流

1. **发起传输**
   ```
   用户 → /sendfile → Server → FileTransferStore
   ↓
   生成 upload_token, upload_key
   生成 download_tokens[user], download_keys[user]
   ↓
   返回上传 URL + 密钥给发送者
   ```

2. **文件上传**
   ```
   发送者 → HTTP POST /upload/<token>?key=<key>
   ↓
   验证 token + key
   ↓
   保存文件到存储目录
   ↓
   标记 upload_used = True
   ↓
   触发回调通知接收者
   ```

3. **文件下载**
   ```
   接收者 → HTTP GET /download/<token>?key=<key>
   ↓
   验证 token + key
   ↓
   检查 upload_used = True
   ↓
   发送文件
   ↓
   标记 download_used[token] = True
   ```

### 持久化

数据存储在 `file_transfers.json`：

```json
{
  "transfers": {
    "transfer_id_1": {
      "transfer_id": "...",
      "sender": "alice",
      "filename": "document.pdf",
      "upload_token": "...",
      "upload_key": "ABC123",
      "upload_used": true,
      "download_tokens": {
        "bob": "...",
        "charlie": "..."
      },
      "download_keys": {
        "bob": "XYZ789",
        "charlie": "DEF456"
      },
      "download_used": {
        "token1": true,
        "token2": false
      },
      "file_path": "/tmp/sshchat_files/...",
      "created_at": 1234567890.0
    }
  }
}
```

## 🐛 已解决的问题

### 死锁问题
**问题**: 使用 `threading.Lock` 时，`validate_upload` 方法中调用 `get_transfer_by_token` 导致死锁。

**解决**: 改用 `threading.RLock`（可重入锁），允许同一线程多次获取锁。

### 回调阻塞问题
**问题**: `mark_upload_complete` 中同步调用回调可能阻塞。

**解决**: 在单独线程中执行回调，避免阻塞主线程。

## 🎯 使用场景

### 1. 团队协作
```bash
# 分享会议记录
/sendfile #meeting notes.pdf

# 每个成员收到独立下载链接
```

### 2. 私密分享
```bash
# 发送合同给特定用户
/sendfile john contract.pdf

# 只有 john 能下载
```

### 3. 临时文件
```bash
# 分享截图
/sendfile #dev screenshot.png

# 下载后自动清理
```

## 📚 相关文档

- [Pull Request #8](https://github.com/Yangxiaotian/SSHChat/pull/8)
- 测试文件: `test_file_sharing_simple.py`
- 部署脚本: `deploy.sh`

## 🔄 未来优化

- 文件传输进度显示
- 批量文件传输
- 传输历史记录
- 文件预览功能
- 传输速度限制
- Web UI 界面

---

**实现者**: Cursor Cloud Agent  
**完成时间**: 2026-08-07  
**分支**: `cursor/secure-file-sharing-5284`  
**提交数**: 2  
**测试状态**: ✅ 全部通过
