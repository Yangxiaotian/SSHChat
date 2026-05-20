import { app } from 'electron';
import * as fs from 'fs';
import * as path from 'path';
import { ConnectionConfig } from '../shared/protocol';

export class ConfigManager {
  private configPath: string;

  constructor() {
    const appData = app.getPath('appData');
    this.configPath = path.join(appData, 'SSHChat', 'client.json');
  }

  getConfigPath(): string {
    return this.configPath;
  }

  loadConfig(): ConnectionConfig | null {
    try {
      if (!fs.existsSync(this.configPath)) {
        return null;
      }
      const data = fs.readFileSync(this.configPath, 'utf-8');
      const config = JSON.parse(data);
      return {
        host: config.host || '',
        user: config.user || '',
        sshPort: config.ssh_port || 22,
        chatPort: config.chat_port || 12345,
      };
    } catch (err) {
      console.error('Failed to load config:', err);
      return null;
    }
  }

  saveConfig(config: ConnectionConfig): void {
    try {
      const dir = path.dirname(this.configPath);
      if (!fs.existsSync(dir)) {
        fs.mkdirSync(dir, { recursive: true });
      }
      const data = {
        host: config.host,
        user: config.user,
        ssh_port: config.sshPort,
        chat_port: config.chatPort || 12345,
      };
      fs.writeFileSync(this.configPath, JSON.stringify(data, null, 2), 'utf-8');
    } catch (err) {
      console.error('Failed to save config:', err);
      throw err;
    }
  }
}
