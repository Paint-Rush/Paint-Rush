export interface AuthToken {
  access_token: string;
  token_type: 'bearer';
}

async function submitCredentials(
  action: 'login' | 'register',
  username: string,
  password: string,
  request: typeof fetch,
): Promise<unknown> {
  if (!/^[A-Za-z0-9_]{3,20}$/.test(username) || password.length < 8 || password.length > 32) {
    throw new Error('아이디는 영문·숫자·밑줄 3~20자, 비밀번호는 8~32자로 입력해 주세요.');
  }

  let response: Response;
  try {
    response = await request(`/api/v1/auth/${action}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password }),
      signal: AbortSignal.timeout(10000),
    });
  } catch {
    throw new Error('서버에 연결할 수 없습니다. 잠시 후 다시 시도해 주세요.');
  }

  if (response.status === 401) throw new Error('아이디 또는 비밀번호가 올바르지 않습니다.');
  if (response.status === 409) throw new Error('이미 사용 중인 아이디입니다. 다른 아이디를 입력해 주세요.');
  if (response.status === 422) throw new Error('아이디 또는 비밀번호의 입력 형식을 확인해 주세요.');
  if (!response.ok) throw new Error('요청 처리 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.');

  try {
    return await response.json();
  } catch {
    throw new Error('서버 응답을 확인할 수 없습니다.');
  }
}

export async function login(username: string, password: string, request: typeof fetch = fetch): Promise<AuthToken> {
  const token = await submitCredentials('login', username, password, request);
  if (!token || typeof token !== 'object' || !('access_token' in token)
    || typeof token.access_token !== 'string' || !token.access_token
    || !('token_type' in token) || token.token_type !== 'bearer') {
    throw new Error('서버 응답을 확인할 수 없습니다.');
  }
  return { access_token: token.access_token, token_type: token.token_type };
}

export async function register(username: string, password: string, request: typeof fetch = fetch): Promise<{ id: number; username: string }> {
  const user = await submitCredentials('register', username, password, request);
  if (!user || typeof user !== 'object' || !('id' in user) || typeof user.id !== 'number'
    || !Number.isInteger(user.id) || user.id <= 0
    || !('username' in user) || typeof user.username !== 'string' || !user.username) {
    throw new Error('서버 응답을 확인할 수 없습니다.');
  }
  return { id: user.id, username: user.username };
}
