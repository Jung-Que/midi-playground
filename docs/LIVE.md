# 라이브 모드 (YouTube Shorts 세로 라이브)

## 새 PC(노트북)에서 준비

1. 받기
   ```
   git clone https://github.com/Jung-Que/midi-playground.git
   cd midi-playground
   git checkout live-mode
   py -3.11 -m venv .venv
   .venv\Scripts\python.exe -m pip install -r requirements.txt
   ```
2. **곡 팩 복사**: 원래 컴퓨터의 `songs-local` 폴더(마림바 5곡 zip)를 같은 위치에 복사합니다.
   개인 편곡이라 git(공개 저장소)에는 올라가지 않습니다.
3. **OBS 설정**: OBS를 한 번 켰다 끈 뒤(설정 폴더 생성) 실행합니다. 그래픽카드에 맞는 인코더를 고릅니다.
   ```
   .venv\Scripts\python.exe tools\obs_live_setup.py
   ```
4. OBS를 켜고 **설정 → 방송**에 스트림 키를 넣습니다.
5. `run_live.bat` 실행.

- 화면 모양(다크모던, 음표 스퀘어, 속도 500, 페그 색칠, 테두리 등)은 `live.json`의 `game_settings`에 들어 있어서
  어느 PC에서나 같게 나옵니다. 라이브 중에만 적용되고 `settings.json`에는 저장되지 않습니다.
- 창 높이는 1280px(OBS 720×1280에 1:1), 화면이 작으면 모니터 높이의 90%로 자동으로 맞춥니다.
- 모니터 주사율이 60의 배수(60/120/180Hz)면 화면 갱신에 맞춰 프레임을 냅니다. 144Hz 등은 정확한 1/60초 간격만 맞춥니다.

## 실행

```
run_live.bat
run_live.bat --category marimba --video <영상ID>    (이번 실행만 덮어쓰기)
run_live.bat --chat file                            (방송 없이 테스트)
```

ESC로 종료합니다. 모든 설정은 프로젝트 폴더의 `live.json` 한 곳에 있습니다.

## live.json 주요 항목

| 항목 | 지금 값 | 설명 |
|---|---|---|
| `category` | `marimba` | 재생할 카테고리. `""`이면 전체, `"a,b"`이면 둘 다 |
| `video_id` | (비어 있음) | 방송 영상 ID 또는 주소. 방송을 만든 뒤 넣기 |
| `chat_source` | `auto` | `auto`(pytchat → chat-downloader), `file`(테스트), `none` |
| `window_height` | `1280` | 창 크기 720×1280. 게임은 1080×1920으로 그리고 창에 맞춰 줄임 |
| `queue_size` | `3` | 대기열 칸 수 |
| `window_every_seconds` / `window_open_seconds` | `60` / `20` | 60초마다 20초 동안 명령어를 받음 |
| `captions`, `texts`, `labels` | | 화면 글자 전부 |
| `help_messages` | | `!help` 답장 문구 (아직 채팅으로 보내지는 않음, 로그에만 남김) |

## 곡 추가 (방송 중에도 됨)

- `songs-local` 폴더에 팩(zip)을 넣으면 5초 안에 곡 목록에 들어갑니다. 재시작할 필요가 없습니다.
- 팩을 다시 만들면 바뀐 제목이 반영되고, 팩을 지우면 그 곡은 더 이상 나오지 않습니다.
- 카테고리가 맞아야 들어갑니다. `tools/midi_split_pack.py`로 만든 팩은 자동으로 `marimba`입니다.
- 다른 팩은 `python tools/set_category.py songs-local/곡.zip marimba`로 넣습니다. 확인은 `--list`로 합니다.

## OBS 설정 (만들어 둠)

OBS에 라이브 전용 프로필 `MidiPlayground Live`와 장면 모음 `MidiPlayground Live`가 있습니다. OBS를 켜면 이 둘로 열립니다.
기존 `제목 없음` 프로필과 장면 모음은 그대로 있고, **프로파일 / 장면 모음** 메뉴에서 언제든 바꿀 수 있습니다.

| 항목 | 값 |
|---|---|
| 해상도 | 캔버스·출력 720×1280, 60fps |
| 인코더 | AMD 하드웨어 H.264, 영상 4500kbps / 소리 160kbps (게임의 CPU를 쓰지 않음) |
| 영상 소스 `Game window` | 윈도우 캡처(WGC), `Midi Playground` 창, 커서 안 보임 |
| 소리 소스 `Game audio` | 응용 프로그램 오디오 캡처, 같은 창의 소리만 |
| 데스크톱 오디오 / 마이크 | 음소거 (다른 프로그램 소리가 방송에 안 나감) |
| 방송 서비스 | YouTube - RTMPS (스트림 키는 비어 있음) |

- 스트림 키는 **설정 → 방송**에서 넣거나 **계정 연결**로 YouTube에 로그인하면 됩니다.
- **게임 창은 주 모니터(2560×1440)에 두세요.** 창(높이 1319px)이 보조 모니터(1080px)로 가면 화면 밖으로 나간 부분이 방송에 흰 띠로 나옵니다.
  게임은 켤 때 주 모니터 오른쪽에 창 전체가 보이게 뜹니다. 위치는 `live.json`의 `window_pos: [x, y]`로 바꿀 수 있습니다.

## YouTube 채팅 연동 테스트

1. YouTube Studio → **만들기 → 라이브 스트리밍 시작 → 스트림**. 공개 범위는 테스트용으로 **일부 공개**.
   (비공개 방송은 채팅을 밖에서 읽을 수 없어서 안 됩니다.)
2. 스트림 키를 OBS **설정 → 방송**에 넣고(또는 계정 연결) **방송 시작**.
3. 방송 주소를 복사해 채팅이 읽히는지 먼저 확인합니다. 게임 없이 채팅만 출력합니다.
   ```
   .venv\Scripts\python.exe tools\chat_probe.py <방송 주소 또는 영상 ID>
   ```
   `[status] pytchat: connected`가 나오고, 채팅에 쓴 글이 줄마다 찍히면 연결된 것입니다. Ctrl+C로 끕니다.
4. 게임을 방송 주소로 켭니다. (`live.json`의 `video_id`에 넣어 두면 `run_live.bat`만 실행해도 됩니다.)
   ```
   run_live.bat --video <방송 주소 또는 영상 ID>
   ```
5. 채팅에 명령어를 씁니다. 게임 콘솔 창에 결과가 한 줄씩 나옵니다.
   ```
   [chat] pytchat: connected
   [chat] 닉네임: !song looping  ->  OK
   [chat] 닉네임: !color autumn  ->  OK
   [chat] 닉네임: !song looping  ->  ignored: input closed (opens in 32s)
   [live] input closed - queue: Looping the Rooms (닉네임), Brainrot, Karakuri Pierrot - theme: autumn
   ```
   - 명령은 입력 시간(60초마다 20초) 안에만 받습니다. 닫힌 동안 쓴 명령은 `input closed`로 나옵니다.
   - 신청곡은 입력 시간이 닫힐 때 대기열 빈 자리에 들어가고, 테마는 그때 표가 가장 많은 것으로 바뀝니다.
   - 다음 곡(Next)은 이미 맵을 만들고 있어서 바뀌지 않습니다. 신청곡은 그다음부터 나옵니다.
   - YouTube 채팅은 몇 초 늦게 들어옵니다.

참고: `pytchat`만 동작합니다. `chat-downloader`는 YouTube 변경으로 지금은 읽지 못합니다(자동 모드에서 실패하면 건너뜀).

## 방송 없이 테스트

`run_live.bat --chat file`로 켠 뒤, `live_test_chat.txt`에 한 줄씩 추가하면 채팅처럼 들어갑니다.

```
viewer1: !song looping
viewer2: !color autumn
viewer3: !help
```
