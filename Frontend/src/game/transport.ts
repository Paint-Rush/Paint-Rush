import { getCard, getCardCost, type GameState } from './model';
import { needsCardTarget, resolveCardAction } from './cardActions';
import { integer, object, parseHand, parsePhase, readRoomState } from './serverState';

export interface QueueSelection { character_id: string; deck?: string[]; cardCosts?: Record<string, number> }
export type ServerEvent = { type: 'queued'; queued_at: number }
  | { type: 'snapshot'; state: GameState }
  | { type: 'left' }
  | { type: 'error'; message: string; fatal: boolean };

/** Latest main /ws/game protocol; full snapshots remain authoritative. */
export class GameSocket {
  private socket: WebSocket | null = null;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private poll: ReturnType<typeof setInterval> | null = null;
  private state: GameState | null = null;
  private selfId = 0;
  private joined = false;
  private requestedAt = 0;
  private intentional = false;
  constructor(private receive: (event: ServerEvent) => void) {}

  connect(endpoint: string, token: string, selection: QueueSelection) {
    this.close(); this.intentional = false; this.state = null; this.joined = false; this.selfId = 0;
    const url = new URL(endpoint, window.location.href);
    if (!['ws:', 'wss:'].includes(url.protocol) || url.host !== window.location.host
      || (window.location.protocol === 'https:' && url.protocol !== 'wss:')) throw new Error('게임 서버는 현재 서비스와 같은 주소의 WebSocket 경로를 사용해야 합니다.');
    const socket = new WebSocket(url); this.socket = socket;
    this.timer = setTimeout(() => { this.fail('게임 서버 응답이 없습니다.'); this.close(); }, 10000);
    socket.onopen = () => this.write({ type: 'auth', token });
    socket.onmessage = event => {
      try {
        if (typeof event.data !== 'string' || event.data.length > 2_000_000) throw new Error('서버 메시지 크기가 올바르지 않습니다.');
        const data = object(JSON.parse(event.data));
        switch (data.type) {
          case 'auth_success':
            this.selfId = integer(data.user_id, '내 계정 ID', Number.MAX_SAFE_INTEGER, 1);
            // Probe for an existing room before joining a new queue (reconnect support).
            this.requestState();
            this.poll = setInterval(() => {
              if (!this.state) return;
              if (this.requestedAt && Date.now() - this.requestedAt > 4000) {
                this.requestedAt = 0; this.fail('서버 상태 확인이 지연되어 카드 사용을 중지했습니다.');
              }
              this.requestState();
            }, 1000);
            break;
          case 'queue_waiting': this.clearTimer(); this.receive({ type: 'queued', queued_at: Date.now() }); break;
          case 'queue_cancelled': this.state = null; this.receive({ type: 'left' }); break;
          case 'match_found': this.requestedAt = 0; this.requestState(); break;
          case 'room_state': {
            const state = readRoomState(data, this.selfId, this.state, Date.now(), selection.cardCosts);
            this.state = state; this.requestedAt = 0; this.clearTimer(); this.emit(); break;
          }
          case 'game_tick':
            if (this.state) {
              const phase = parsePhase(data.status), seconds = integer(data.remaining_seconds, '타이머', phase === 'ready' ? 10 : 300);
              this.state = { ...this.state, phase, receivedAt: Date.now(),
                ...(phase === 'ready' ? { readyMs: seconds * 1000 } : { remainingMs: seconds * 1000 }) };
              this.emit();
            } else this.requestState();
            break;
          case 'cost_updated':
            if (this.state) {
              const maxCost = integer(data.max_cost, '최대 코스트', 10, 10), cost = integer(data.cost, '코스트', maxCost);
              this.state = { ...this.state, maxCost, players: this.state.players.map(p => p.id === this.selfId ? { ...p, cost } : p) };
              this.emit();
            }
            break;
          case 'hand_updated':
            if (this.state) {
              const hand = parseHand(data.hand), cost = integer(data.cost, '코스트', 10);
              this.state = { ...this.state, inputReady: false, players: this.state.players.map(p => p.id === this.selfId ? { ...p, hand, cost } : p) };
              this.emit(); this.requestState();
            }
            break;
          case 'game_finished':
            if (this.state) { this.state = { ...this.state, phase: 'finished', remainingMs: 0, inputReady: false }; this.emit(); }
            this.requestState(); break;
          case 'game_result': this.readResult(data); break;
          case 'room_left': this.state = null; this.receive({ type: 'left' }); break;
          case 'session_replaced': this.fail('다른 창에서 로그인하여 현재 연결이 종료됐습니다.'); this.close(); break;
          case 'error':
            if (data.code === 'not_in_room' && !this.state && !this.joined) {
              this.requestedAt = 0; this.joined = true;
              // The server loads the persisted deck; the client saves it before connecting.
              this.write({ type: 'queue_join', character_id: selection.character_id });
            } else if (!this.state || data.code === 'not_in_room' || data.code === 'auth_failed') {
              this.fail(typeof data.message === 'string' ? data.message.slice(0, 200) : '매칭 요청을 처리하지 못했습니다.'); this.close();
            } else {
              this.clearTimer(); this.receive({ type: 'error', message: typeof data.message === 'string' ? data.message.slice(0, 200) : '서버에서 요청을 거부했습니다.', fatal: data.code === 'auth_failed' });
              this.requestState();
            }
            break;
          case 'card_used': case 'move_requested': case 'attack_requested': case 'player_moved': case 'tiles_updated':
          case 'game_ready': case 'game_started': case 'deathmatch_started':
          case 'player_disconnected': case 'player_reconnected': case 'ultimate_activated':
            // Requested does not mean executed: never move/paint locally on these notifications.
            // A notification invalidates an earlier in-flight snapshot request.
            this.requestedAt = 0; this.requestState(); break;
          default: throw new Error('지원하지 않는 서버 메시지입니다.');
        }
      } catch (error) {
        this.fail(error instanceof Error ? error.message : '서버 데이터를 읽지 못했습니다.');
        // Keep the last validated state. Retry via the next scheduled full state request.
      }
    };
    socket.onerror = () => this.fail('게임 서버에 연결하지 못했습니다.');
    socket.onclose = () => {
      this.stopTimers();
      if (!this.intentional) this.fail('서버 연결이 끊겼습니다. 재접속 후 상태를 확인해 주세요.');
    };
  }

  send(message: object) {
    const data = object(message);
    if (data.type === 'card_use') {
      const index = integer(data.hand_index, '손패 인덱스', 2);
      if (!this.state?.inputReady || !['playing', 'deathmatch'].includes(this.state.phase)) throw new Error('서버 상태 확인 후 카드를 사용할 수 있습니다.');
      const self = this.state.players.find(player => player.id === this.selfId);
      const card = getCard(self?.hand[index] ?? '');
      const cost = card ? getCardCost(this.state, card.id) : null;
      if (!self || !card || !self.alive || !self.connected || cost === null || self.cost < cost) throw new Error('현재 손패·코스트로 사용할 수 없는 카드입니다.');
      const target = needsCardTarget(card.id) ? {
        target_x: integer(data.target_x, '이동 목적지 x', this.state.map.width - 1),
        target_y: integer(data.target_y, '이동 목적지 y', this.state.map.height - 1),
      } : {};
      resolveCardAction(card.id, self.position, this.state.map, needsCardTarget(card.id) ? { x: target.target_x!, y: target.target_y! } : undefined);
      this.write({ type: 'card_use', hand_index: index, ...target });
      this.state = { ...this.state, inputReady: false }; this.emit(); this.requestState();
    } else if (data.type === 'queue_cancel' || data.type === 'room_leave') this.write({ type: data.type });
    else throw new Error('지원하지 않는 클라이언트 요청입니다.');
  }
  private requestState() {
    if (this.socket?.readyState !== WebSocket.OPEN || this.requestedAt) return;
    this.requestedAt = Date.now(); this.write({ type: 'room_state' });
  }
  private write(message: object) {
    if (this.socket?.readyState !== WebSocket.OPEN) throw new Error('서버에 연결되어 있지 않습니다.');
    this.socket.send(JSON.stringify(message));
  }
  private readResult(data: Record<string, unknown>) {
    if (!this.state || data.room_id !== this.state.roomId || !Array.isArray(data.results) || data.results.length !== this.state.players.length) throw new Error('결과의 참가자가 일치하지 않습니다.');
    const winner = integer(data.winner_id, '우승자 ID', Number.MAX_SAFE_INTEGER, 1);
    const seen = new Set<number>();
    const players = this.state.players.map(player => {
      const matches = (data.results as unknown[]).map(object).filter(row => row.user_id === player.id);
      if (matches.length !== 1) throw new Error('결과의 계정 ID가 일치하지 않습니다.');
      const row = matches[0], rank = integer(row.rank, '순위', this.state!.players.length, 1);
      if (seen.has(rank) || row.is_winner !== (player.id === winner) || (rank === 1) !== (player.id === winner)) throw new Error('결과 순위가 올바르지 않습니다.');
      seen.add(rank);
      return { ...player, rank, deaths: integer(row.deaths, '사망'), tiles: integer(row.painted_tiles, '영역 수', 400), painted: integer(row.total_painted, '누적 채색'), kills: integer(row.kills, '처치'), cardsUsed: integer(row.cards_used, '사용 카드'), costUsed: integer(row.cost_used, '사용 코스트') };
    });
    if (!players.some(p => p.id === winner)) throw new Error('참가자가 아닌 우승자입니다.');
    this.state = { ...this.state, phase: 'finished', remainingMs: 0, players, winnerId: winner, inputReady: false }; this.emit();
  }
  private emit() { if (this.state) this.receive({ type: 'snapshot', state: this.state }); }
  private fail(message: string) { if (this.state) this.state = { ...this.state, inputReady: false }; this.receive({ type: 'error', message, fatal: true }); }
  private clearTimer() { if (this.timer) clearTimeout(this.timer); this.timer = null; }
  private stopTimers() { this.clearTimer(); if (this.poll) clearInterval(this.poll); this.poll = null; this.requestedAt = 0; }
  close() {
    this.intentional = true; this.stopTimers();
    if (this.socket) { this.socket.onclose = null; this.socket.onerror = null; this.socket.onmessage = null; this.socket.onopen = null; this.socket.close(); }
    this.socket = null;
  }
}
