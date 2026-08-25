# Library Hot Reload for Federation

## 功能说明

当在联邦环境中向 `/library` 目录添加或删除书籍时，现在会自动同步到其他联邦节点，无需重启服务。

## 工作原理

1. 服务器启动时，会启动一个后台监控线程
2. 该线程定期检查 library 目录的变化（默认每5秒）
3. 检测到变化时，自动将新的 library catalog 同步到所有联邦节点
4. 服务器关闭时，监控线程会优雅地停止

## 配置

可以通过环境变量配置检查间隔：

```bash
export SSHCHAT_LIBRARY_WATCH_SECONDS=3  # 每3秒检查一次
```

默认值为 5 秒，最小值为 1 秒。

## 使用示例

### 场景1: 添加新书

1. 在节点A的 library 目录添加新书：
   ```bash
   cp my_book.pdf /opt/sshchat/library/
   ```

2. 等待最多5秒（或你配置的间隔时间）

3. 在节点B执行 `/library` 命令，应该能看到新增的书籍

### 场景2: 删除书籍

1. 从节点A的 library 目录删除书籍：
   ```bash
   rm /opt/sshchat/library/old_book.pdf
   ```

2. 等待最多5秒

3. 在节点B执行 `/library` 命令，应该看不到被删除的书籍了

## 日志输出

启动时会看到：
```
federation: library watch started (interval=5.0s)
```

检测到变化时会看到：
```
federation: library changed (+1 -0), syncing catalog
```

停止时会看到：
```
federation: library watch stopped
```

## 注意事项

- 只有支持的文件格式（.txt, .md, .pdf, .epub）会被监控
- 目录的 mtime 变化也会触发同步
- 监控线程作为 daemon 线程运行，不会阻止程序退出
- 如果 federation 未启用，监控线程仍会运行但不会执行同步操作

## 测试

运行测试脚本验证功能：

```bash
python3 test_library_hot_reload.py
```

测试会验证：
- 文件添加检测
- 文件删除检测
- 目录 mtime 变化检测
