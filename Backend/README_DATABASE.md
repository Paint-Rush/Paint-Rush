# Windows 로컬 PostgreSQL 설정

PowerShell에서 아래 순서로 실행합니다. PostgreSQL 18의 Windows 서비스가 실행 중이어야 합니다.

## 1. Backend로 이동

```powershell
cd 'C:\Users\user\OneDrive\바탕 화면\Paint-Rush\Backend'
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## 2. DB 생성 및 연결 정보 저장

```powershell
.venv\Scripts\python.exe setup_local_db.py
```

PostgreSQL 설치 시 지정한 `postgres` 비밀번호를 입력합니다. 입력 문자는 화면에 표시되지 않습니다. 비밀번호를 채팅에 보내지 않아도 됩니다.

스크립트는 localhost:5432에 접속해 `paint_rush`가 없을 때만 생성하고, `Backend/.env`에 DATABASE_URL과 JWT_SECRET_KEY를 저장합니다. 비밀번호의 특수문자는 연결 URL에 맞게 변환합니다. 기존 JWT_SECRET_KEY는 유지합니다. `.env`는 Git 제외 대상이며 비밀정보가 들어 있으므로 공유하지 않습니다.

로컬 개발에서는 기존 예제의 postgres 계정을 사용합니다. `app/database.py`가 `.env`를 읽어 engine, SessionLocal, Base, get_db를 제공하고, `app/models.py`의 기존 User 모델로 테이블을 만듭니다.

| 컬럼 | PostgreSQL 형태 | 제약 |
| --- | --- | --- |
| id | integer, 자동 증가 | PRIMARY KEY |
| username | varchar(20) | UNIQUE, NOT NULL |
| password_hash | text | NOT NULL |

기존 DB와 테이블은 삭제하지 않습니다. 이미 있는 테이블의 구조를 자동으로 변경하지는 않습니다.

## 3. 실제 PostgreSQL 검증

```powershell
.venv\Scripts\python.exe check_db.py
```

접속한 DB, 실제 테이블 구조, 자동 ID, 저장 및 조회, 중복 username 거부, NULL 거부를 확인합니다. 검증 데이터는 마지막에 롤백합니다. 기존 사용자 데이터는 유지되며 자동 증가 ID에 빈 번호가 생길 수 있습니다.

기존 `tests/test_auth.py`는 `drop_all`로 테이블을 삭제하므로 사용 중인 paint_rush DB에 실행하지 않습니다.

## 4. 서버 및 Swagger 확인

```powershell
.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

브라우저에서 http://127.0.0.1:8000/docs 를 열어 회원가입과 로그인을 확인합니다. 회원가입한 계정은 실제 DB에 저장됩니다.

## 연결이 안 될 때

- `password authentication failed`: 설치 시 지정한 postgres 비밀번호를 확인하고 설정 스크립트를 다시 실행합니다.
- `connection refused`: Windows 서비스에서 postgresql-x64-18의 실행 상태를 확인합니다.
- `No module named app`: app 폴더가 아니라 Backend 폴더에서 실행합니다.
- 이전 PowerShell 환경변수가 있으면 `.env`보다 우선합니다. 현재 창의 오래된 설정을 지우려면 아래 명령을 실행합니다.

```powershell
Remove-Item Env:DATABASE_URL -ErrorAction SilentlyContinue
Remove-Item Env:JWT_SECRET_KEY -ErrorAction SilentlyContinue
```
