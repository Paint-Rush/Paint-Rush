import { useRef, useState, type FormEvent } from 'react';
import { submitAuthForm, type AuthToken } from '../services/auth';
import '../styles/auth.css';

export default function AuthPage({ onLogin, onBack }: { onLogin: (token: AuthToken) => void; onBack: () => void }) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [passwordConfirm, setPasswordConfirm] = useState('');
  const [message, setMessage] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [isRegister, setIsRegister] = useState(false);
  const isSubmitting = useRef(false);

  function changePage() {
    setIsRegister(!isRegister);
    setUsername('');
    setPassword('');
    setPasswordConfirm('');
    setMessage('');
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (isSubmitting.current) return;
    if (!username.trim()) {
      setMessage('아이디를 입력해 주세요.');
      event.currentTarget.querySelector<HTMLInputElement>('#username')?.focus();
      return;
    }

    if (window.location.protocol === 'file:') {
      setMessage(`화면 미리보기입니다. 실제 ${isRegister ? '회원가입' : '로그인'}은 서비스 주소에서 이용해 주세요.`);
      return;
    }
    isSubmitting.current = true;
    setIsLoading(true);
    setMessage('');
    try {
      const result = await submitAuthForm(isRegister ? 'register' : 'login', { username, password, passwordConfirm });
      if (result.status === 'registered') {
        setUsername(result.username);
        setIsRegister(false);
        setMessage('회원가입이 완료되었습니다. 비밀번호를 입력해 로그인해 주세요.');
        setPassword('');
        setPasswordConfirm('');
      } else if (result.status === 'logged-in') {
        setPassword('');
        onLogin(result.token);
      } else {
        setMessage(result.message);
      }
    } finally {
      isSubmitting.current = false;
      setIsLoading(false);
    }
  }

  return (
    <main className="auth-page">
      <img className="auth-background" src="storyboard/start.png" alt="" width={1289} height={724} />
      <button className="auth-back" type="button" disabled={isLoading} onClick={onBack}>← 처음으로</button>
      <section className={isRegister ? 'auth-panel auth-panel-register' : 'auth-panel'} aria-labelledby="auth-title">
        <h1 id="auth-title" className="auth-title" aria-label={isRegister ? 'SIGN UP' : 'SIGN IN'}>
          <span aria-hidden="true">SIGN</span>
          <span aria-hidden="true">{isRegister ? 'UP' : 'IN'}</span>
        </h1>
        <form className="auth-form" onSubmit={handleSubmit} onChange={() => setMessage('')} aria-busy={isLoading}>
          <label className="auth-label" htmlFor="username">아이디</label>
          <input
            className="auth-input"
            id="username"
            name="username"
            autoComplete="username"
            autoCapitalize="none"
            spellCheck={false}
            required
            pattern="[A-Za-z0-9_]{3,20}"
            minLength={3}
            maxLength={20}
            title="영문·숫자·밑줄 3~20자"
            disabled={isLoading}
            value={username}
            onChange={(event) => setUsername(event.target.value)}
          />
          <label className="auth-label" htmlFor="password">비밀번호</label>
          <input
            className="auth-input"
            id="password"
            name="password"
            type="password"
            autoComplete={isRegister ? 'new-password' : 'current-password'}
            required
            minLength={8}
            maxLength={32}
            disabled={isLoading}
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />
          {isRegister && (
            <>
              <label className="auth-label" htmlFor="password-confirm">비밀번호 확인</label>
              <input
                className="auth-input"
                id="password-confirm"
                name="password-confirm"
                type="password"
                autoComplete="new-password"
                required
                minLength={8}
                maxLength={32}
                disabled={isLoading}
                value={passwordConfirm}
                onChange={(event) => setPasswordConfirm(event.target.value)}
              />
            </>
          )}
          <button className="auth-submit" type="submit" disabled={isLoading}>
            {isLoading ? (isRegister ? '가입 중…' : '로그인 중…') : (isRegister ? '회원가입' : '로그인')}
          </button>
          <p className="auth-switch">
            {isRegister ? '계정이 있으신가요?' : '계정이 없으신가요?'}
            <button className="auth-switch-button" type="button" disabled={isLoading} onClick={changePage}>
              {isRegister ? '로그인' : '회원가입'}
            </button>
          </p>
          <p className="auth-message" role="status">{message}</p>
        </form>
      </section>
    </main>
  );
}
