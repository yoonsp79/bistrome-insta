# 비스트로미 인스타 승인 봇

@bistromechangwon 게시물을 텔레그램으로 승인받아 정해진 시각에 자동으로 올립니다.

## 흐름
1. 게시 **24시간 전**: 텔레그램으로 사진과 캡션 미리보기, [✅ 올리기] [⏸ 보류] 버튼
2. 게시 **2시간 전**까지 결정이 없으면 한 번 더 알림
3. **올리기**를 누른 게시물만 예정 시각에 게시 → "게시 완료" 알림과 링크
   - 예정 시각이 지난 뒤 승인하면 다음 실행(10분 이내)에 바로 올라갑니다.
   - 버튼을 누른 뒤 반영까지 최대 10분 정도 걸립니다.

텔레그램 명령: `/list` 예정 목록, `/next` 다음 게시물 미리보기 받기

## 파일
- `posts.json` 게시 일정 (게시 시각, 사진, 캡션)
- `img/` 게시용 사진 (1080×1350). 인스타가 가져갈 수 있도록 공개 저장소에 둡니다.
- `bot.py` 봇 본체, `.github/workflows/bot.yml` 10분마다 실행
- `state.json` 진행 상태 (봇이 자동으로 기록)

## 다음 일정 추가
`img/`에 사진을 넣고 `posts.json`의 `posts` 목록에 항목을 추가해서 push하면 됩니다.

## 시크릿 (Settings → Secrets and variables → Actions)
`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `IG_ACCESS_TOKEN`, `TOKEN_KEY`
