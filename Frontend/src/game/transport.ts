import { parseSnapshot, type GameState } from './model';

export interface QueueSelection { character_id: string; deck: string[] }
export type ServerEvent = { type: 'queued'; queued_at: number }
  | { type: 'snapshot'; state: GameState }
  | { type: 'error'; message: string; fatal: boolean };

/** Proposed protocol: no matchmaking/WebSocket endpoint exists in main yet. */
export class GameSocket {
  private socket: WebSocket | null = null;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private intentional = false;
  constructor(private receive: (event: ServerEvent) => void) {}
  connect(endpoint: string, token: string, selection: QueueSelection) {
    this.close(); this.intentional = false;
    const url = new URL(endpoint, window.location.href);
    if (!['ws:', 'wss:'].includes(url.protocol) || (window.location.protocol === 'https:' && url.protocol !== 'wss:')
      || url.host !== window.location.host) throw new Error('게임 서버는 현재 서비스와 같은 주소의 WebSocket 경로를 사용해야 합니다.');
    const socket = new WebSocket(url); this.socket = socket;
    this.timer = setTimeout(() => {
      this.receive({ type: 'error', message: '매칭 서버 응답이 없습니다. 서버 연결 규격을 확인해 주세요.', fatal: true });
      this.close();
    }, 10000);
    socket.onopen = () => this.send({ type: 'authenticate', access_token: token });
    socket.onmessage = event => {
      try {
        if (typeof event.data !== 'string' || event.data.length > 2_000_000) throw new Error('서버 메시지가 너무 큽니다.');
        const data = JSON.parse(event.data);
        if (!data || typeof data !== 'object') throw new Error('서버 메시지가 올바르지 않습니다.');
        if (data.type === 'authenticated') this.send({ type: 'queue.join', ...selection });
        else if (data.type === 'queued') {
          this.clearTimer();
          this.receive({ type: 'queued', queued_at: Date.now() });
        } else if (data.type === 'snapshot') {
          const state = parseSnapshot(data.room); this.clearTimer();
          this.receive({ type: 'snapshot', state });
        } else if (data.type === 'error') {
          this.clearTimer();
          this.receive({ type: 'error', message: typeof data.message === 'string' ? data.message.slice(0, 200) : '서버에서 요청을 거부했습니다.', fatal: false });
        } else if (data.type !== 'pong') throw new Error('지원하지 않는 서버 메시지입니다.');
      } catch (error) {
        this.receive({ type: 'error', message: error instanceof Error ? error.message : '서버 데이터를 읽지 못했습니다.', fatal: true });
      }
    };
    socket.onerror = () => this.receive({ type: 'error', message: '매칭 서버에 연결하지 못했습니다. 현재 백엔드에 게임 연결 API가 있는지 확인해 주세요.', fatal: true });
    socket.onclose = () => {
      this.clearTimer();
      if (!this.intentional) this.receive({ type: 'error', message: '게임 서버 연결이 끊겼습니다. 자동으로 게임을 진행하지 않습니다.', fatal: true });
    };
  }
  send(message: object) {
    if (this.socket?.readyState !== WebSocket.OPEN) throw new Error('서버에 연결되어 있지 않습니다.');
    this.socket.send(JSON.stringify(message));
  }
  private clearTimer() { if (this.timer) clearTimeout(this.timer); this.timer = null; }
  close() {
    this.intentional = true; this.clearTimer();
    if (this.socket) { this.socket.onclose = null; this.socket.onerror = null; this.socket.onmessage = null; this.socket.onopen = null; this.socket.close(); }
    this.socket = null;
  }
}
