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
  private transportMode: 'tunnel' | 'shell' | null = null;
  private tcpSocket: net.Socket | null = null;
  private onData: ((data: Buffer) => void) | null = null;
  private onEnd: (() => void) | null = null;
  private readonly ansiCsiRe = /\x1b\[[\d;?]*[A-Za-z]/g;
  private readonly ansiCsiExtRe = /[\u001b\u009b]\[[0-?]*[ -/]*[@-~]/g;
  private readonly oscRe = /\x1b\][^\x07]*(?:\x07|\x1b\\)/g;
  private readonly oscLooseRe = /[\u001b\u009b]\][^\u0007]*(?:\u0007|[\u001b\u009b]\\)/g;
  private readonly otherEscRe = /\x1b[\][()#%][\d"A-Za-z]*/g;
  private readonly mangledCsiRe = /\?\[[\d;?]*[A-Za-z]/g;
  private readonly csiFragmentRe = /\[(?:\??\d{1,4}(?:;\d{1,4})*)[ABCDHJKfhlmnpqrstsu]/g;
  private readonly ctrlRe = /[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]/g;

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
            // Fallback for forced-command / no-port-forwarding servers:
            // use interactive shell channel (same behavior as legacy GUI client).
            this.ssh!.shell((shellErr, shellStream) => {
              if (shellErr) {
                onError(`Port forwarding failed: ${err.message}; shell fallback failed: ${shellErr.message}`);
                reject(shellErr);
                return;
              }

              this.stream = shellStream;
              this.transportMode = 'shell';
              onStatus('connected');

              this.bindStream(shellStream, onDataCallback, onEndCallback, onError);
              resolve();
            });
            return;
          }

          this.stream = stream;
          this.transportMode = 'tunnel';
          onStatus('connected');

          // Send nickname as first line (server handshake)
          stream.write(nickname + '\n');
          this.bindStream(stream, onDataCallback, onEndCallback, onError);

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

  private bindStream(
    stream: ClientChannel,
    onDataCallback: (data: Buffer) => void,
    onEndCallback: () => void,
    onError: SSHErrorCallback,
  ): void {
    let buffer = '';
    stream.on('data', (data: Buffer) => {
      buffer += data.toString('utf-8');
      const lines = buffer.split('\n');
      buffer = lines.pop() || '';

      for (const line of lines) {
        const cleaned = this.cleanLine(line);
        if (this.shouldSkipLine(cleaned)) {
          continue;
        }
        if (cleaned.trim()) {
          onDataCallback(Buffer.from(cleaned, 'utf-8'));
        }
      }
    });

    stream.on('end', () => {
      onEndCallback();
    });

    stream.on('error', (err: Error) => {
      onError(`Stream error: ${err.message}`);
    });
  }

  private cleanLine(raw: string): string {
    let s = raw
      .replace(/\r\n/g, '\n')
      .replace(/\r/g, '')
      .replace(this.oscRe, '')
      .replace(this.oscLooseRe, '')
      .replace(this.ansiCsiRe, '')
      .replace(this.ansiCsiExtRe, '')
      .replace(this.mangledCsiRe, '')
      .replace(this.csiFragmentRe, '')
      .replace(this.otherEscRe, '')
      .replace(this.ctrlRe, '');
    // Remove any left-over ESC bytes rendered as glyphs.
    s = s.replace(/\u001b/g, '');
    return s.trimEnd();
  }

  private shouldSkipLine(line: string): boolean {
    const t = line.trim();
    if (!t) return true;
    if (t === '>' || t.startsWith('> ')) return true;
    if (t.startsWith('WARNING: your terminal doesn\'t support cursor position requests')) return true;
    return false;
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
      if (this.transportMode === 'shell') {
        // Shell mode expects raw command text (legacy SSH GUI behavior),
        // not protocol-wrapped "[nick] ..." payload.
        const normalized = data.replace(/^\[[^\]]+\]\s*/, '');
        this.stream.write(normalized);
      } else {
        this.stream.write(data);
      }
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
    this.transportMode = null;
  }

  isConnected(): boolean {
    return this.ssh !== null && this.stream !== null && !this.stream.destroyed;
  }
}
