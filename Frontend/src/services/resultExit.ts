import type { GameSocket } from './gameSocket';

const LEAVE_TIMEOUT = 10000;
const LEAVE_ERRORS = ['game_not_finished', 'result_not_available', 'result_save_failed'];

export interface ResultExitState {
  leaving: boolean;
  message: string;
}

export class ResultExit {
  private state: ResultExitState = { leaving: false, message: '' };
  private timer: ReturnType<typeof setTimeout> | null = null;
  private readonly unsubscribe: () => void;

  private readonly socket: Pick<GameSocket, 'onEvent' | 'leaveRoom'>;
  private readonly onChange: (state: ResultExitState) => void;
  private readonly onLeft: () => void;
  private readonly timeout: number;

  constructor(socket: Pick<GameSocket, 'onEvent' | 'leaveRoom'>, onChange: (state: ResultExitState) => void, onLeft: () => void, timeout = LEAVE_TIMEOUT) {
    this.socket = socket;
    this.onChange = onChange;
    this.onLeft = onLeft;
    this.timeout = timeout;
    this.unsubscribe = socket.onEvent((event) => {
      if (!this.state.leaving) return;
      if (event.type === 'room_left' || (event.type === 'error' && event.code === 'not_in_room')) {
        this.settle('');
        this.onLeft();
      } else if (event.type === 'error' && LEAVE_ERRORS.includes(event.code as string)) {
        this.settle(typeof event.message === 'string' ? event.message : '경기 정리에 실패했습니다. 다시 시도해 주세요.');
      }
    });
  }

  getState(): ResultExitState {
    return this.state;
  }

  leave(): boolean {
    if (this.state.leaving) return false;
    if (!this.socket.leaveRoom()) {
      this.set({ leaving: false, message: '서버에 연결되어 있지 않아 경기를 정리할 수 없습니다.' });
      return false;
    }
    this.set({ leaving: true, message: '' });
    this.timer = setTimeout(() => this.settle('서버 응답이 없습니다. 다시 시도해 주세요.'), this.timeout);
    return true;
  }

  disconnected(): void {
    if (this.state.leaving) this.settle('경기 정리 중 서버 연결이 끊겼습니다. 다시 연결한 뒤 시도해 주세요.');
  }

  clearMessage(): void {
    if (this.state.message) this.set({ ...this.state, message: '' });
  }

  dispose(): void {
    this.clearTimer();
    this.unsubscribe();
  }

  private settle(message: string): void {
    this.clearTimer();
    this.set({ leaving: false, message });
  }

  private clearTimer(): void {
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
  }

  private set(state: ResultExitState): void {
    this.state = state;
    this.onChange(state);
  }
}
