import { Client, ClientChannel, ConnectConfig } from 'ssh2';
import * as net from 'net';
import * as fs from 'fs';
import * as path from 'path';
import { ConnectionConfig } from '../shared/protocol';

export type SSHStatusCallback = (status: string) => void;
export type SSHErrorCallback = (error: string) => void;

export class SSHManager {
  private ssh: Client | null = null;
  private stream: ClientChannel | null = null;
  private tcpSocket: net.Socket | null = null;
  private onData: ((data: Buffer) => void) | null = null;
  private onEnd: (() => void) | null = null;

  async connect(
    config: ConnectionConfig,
    nickname: string,
    onStatus: SSHStatusCallback,
    onError: SSHErrorCallback,
    onDataCallback: (data: Buffer) => void,
    onEndCallback: () => void,
  ): Promise<void> {
    this.onData = onDataCallback;
    this.onEnd = onEndCallback;

    return new Promise((resolve, reject) => {
      onStatus('connecting');

      this.ssh = new Client();

      const connectConfig: ConnectConfig = {
        host: config.host,
        port: config.sshPort,
        username: config.user,
        readyTimeout: 20000,
        keepaliveInterval: 10000,
      };

      // Try SSH agent first, then key files
      const keyPath = this.findSSHKey();
      if (keyPath) {
        try {
          connectConfig.privateKey = fs.readFileSync(keyPath);
        } catch (err) {
          console.warn('Failed to read SSH key:', err);
        }
      }

      this.ssh.on('ready', () => {
        onStatus('ssh-connected');

        const chatPort = config.chatPort || 12345;
        this.ssh!.forwardOut('127.0.0.1', 0, '127.0.0.1', chatPort, (err, stream) => {
          if (err) {
            onError(`Port forwarding failed: ${err.message}`);
            reject(err);
            return;
          }

          this.stream = stream;
          onStatus('connected');

          // Send nickname as first line (server handshake)
          stream.write(nickname + '\n');

          // Handle data from server
          let buffer = '';
          stream.on('data', (data: Buffer) => {
            buffer += data.toString('utf-8');
            const lines = buffer.split('\n');
            buffer = lines.pop() || '';

            for (const line of lines) {
              if (line.trim()) {
                onDataCallback(Buffer.from(line, 'utf-8'));
              }
            }
          });

          stream.on('end', () => {
            onEndCallback();
          });

          stream.on('error', (err: Error) => {
            onError(`Stream error: ${err.message}`);
          });

          resolve();
        });
      });

      this.ssh.on('error', (err: Error) => {
        onError(`SSH error: ${err.message}`);
        reject(err);
      });

      this.ssh.on('end', () => {
        onEndCallback();
      });

      this.ssh.on('close', () => {
        onEndCallback();
      });

      this.ssh.connect(connectConfig);
    });
  }

  private findSSHKey(): string | null {
    const homeDir = process.env.HOME || process.env.USERPROFILE || '';
    const sshDir = path.join(homeDir, '.ssh');

    const keyFiles = ['id_ed25519', 'id_rsa', 'id_ecdsa', 'id_dsa'];

    for (const keyFile of keyFiles) {
      const keyPath = path.join(sshDir, keyFile);
      if (fs.existsSync(keyPath)) {
        return keyPath;
      }
    }

    return null;
  }

  send(data: string): boolean {
    if (this.stream && !this.stream.destroyed) {
      this.stream.write(data);
      return true;
    }
    return false;
  }

  disconnect(): void {
    if (this.stream) {
      this.stream.close();
      this.stream = null;
    }
    if (this.ssh) {
      this.ssh.end();
      this.ssh = null;
    }
  }

  isConnected(): boolean {
    return this.ssh !== null && this.stream !== null && !this.stream.destroyed;
  }
}
