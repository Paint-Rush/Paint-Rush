# Paint Rush Frontend

React + TypeScript 로그인 화면입니다.

## 서버 없이 열기

Frontend/index.html을 더블클릭하면 로그인 화면이 열립니다. React 코드와 CSS가 파일에 포함되어 있어 서버나 인터넷 연결이 필요하지 않습니다.

## 수정 및 빌드

src/App.tsx와 src/styles.css를 수정한 뒤 다음 명령으로 index.html을 갱신하세요.

```sh
npm install
npm run build
```

index.html은 자동 생성 파일입니다. 원본은 src/에서 수정하세요. dist/index.html에도 같은 결과가 생성됩니다.

선택적으로 npm run dev를 실행하면 자동 갱신용 개발 서버가 /dev.html을 엽니다. 파일을 직접 열 때는 필요하지 않습니다.

아이디·비밀번호 필수 입력, 비밀번호 마스킹, Enter 제출을 지원합니다. 실제 인증이나 비밀번호 저장은 수행하지 않습니다.
