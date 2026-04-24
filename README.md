# HERMES_CPR

**기존 Hermes gateway 배포를 위한 외부 CPR/watchdog 유틸리티**입니다.

HERMES_CPR은 독립형 챗봇도 아니고, 임의의 프로세스를 감시하는 범용 supervisor도 아닙니다. 이 도구는 `hermes-agent` 저장소 **바깥에서** 실행되며, Hermes gateway 런타임 상태를 점검한 뒤 **정말 복구가 필요한 경우에만** gateway를 다시 살리도록 설계된 작은 보조 도구입니다.

---

## 이 프로젝트가 하는 일

HERMES_CPR은 Hermes 런타임 파일을 주기적으로 점검하고, 아래 상황에서 복구를 시도합니다.

- Hermes gateway 프로세스가 사라졌을 때 → `hermes gateway start`
- gateway가 `startup_failed` 상태일 때 → `hermes gateway restart`
- gateway가 `draining` 상태에서 허용 시간 이상 멈춘 것이 여러 번 연속 확인될 때 → `hermes gateway restart`
- gateway가 `running` 상태이지만 런타임 상태가 stale이고, **모든 플랫폼 상태가 degraded인 것이 여러 번 연속 확인될 때만** → `hermes gateway restart`

이 도구는 일부러 보수적으로 동작합니다.

- **프로세스 생존 여부를 최우선 신호**로 봅니다.
- `gateway_state`가 알 수 없는 값이면 재시작 근거로 쓰지 않습니다.
- 건강한 `running` gateway는 `updated_at`만 멈췄다고 재시작하지 않습니다.
- `running` 상태에서 연결된 플랫폼이 하나라도 있으면 보호합니다.
- `running` 상태에서 active agent가 남아 있으면 보호합니다.
- stale 기반 재시작은 반드시 **반복 확인**을 거쳐야 합니다.

---

## 이 저장소가 맞는 사람

다음 조건을 모두 만족하면 이 저장소가 맞습니다.

1. 이미 [`hermes-agent`](https://github.com/NousResearch/hermes-agent) 기반의 Hermes gateway를 운영 중이다.
2. Hermes 환경이 `gateway_state.json`, `gateway.pid` 같은 런타임 파일을 생성한다.
3. Hermes 프로세스가 스스로 되살아나는 구조 대신, **외부 복구 루프**를 두고 싶다.

반대로,

- 아무 프로세스에나 붙일 범용 watchdog이 필요하거나
- 단독 실행형 서비스가 필요하다면

이 저장소는 그 용도가 아닙니다.

---

## 사전 준비 사항

HERMES_CPR을 설치하기 전에 이미 아래가 준비되어 있어야 합니다.

- 동작하는 `hermes-agent` 체크아웃
- 유효한 Hermes profile / `HERMES_HOME`
- 다음 런타임 파일을 생성하는 Hermes gateway 배포
  - `gateway_state.json`
  - `gateway.pid`
- Hermes CLI 실행 파일이 아래 둘 중 하나에 존재하는 Python 환경
  - `repo_root/venv/bin/hermes`
  - `repo_root/.venv/bin/hermes`

현재 HERMES_CPR은 Hermes 실행 파일이 **repo-local virtualenv** 안에 있다고 가정합니다. 배포 구조가 다르면 `hermes_cpr.py` 안의 `resolve_hermes_bin()`을 환경에 맞게 수정하세요.

---

## 빠른 시작 (Quick Start)

### 1) 저장소 클론

```bash
git clone https://github.com/wilgon456/HERMES_CPR.git
cd HERMES_CPR
```

### 2) 로컬 설정 파일 생성

```bash
cp config.example.json config.json
```

그다음 `config.json`을 환경에 맞게 수정합니다.

예시:

```json
{
  "repo_root": "/opt/hermes-agent",
  "hermes_home": "/home/you/.hermes/profiles/main",
  "profile": "main",
  "stale_seconds": 600,
  "draining_stuck_seconds": 600,
  "stale_confirmations": 3,
  "log_file": "/home/you/.hermes/profiles/main/logs/hermes-cpr.log",
  "tracker_file": "/home/you/.hermes/profiles/main/logs/hermes-cpr-state.json"
}
```

### 3) 수동 1회 실행

```bash
python3 hermes_cpr.py --config config.json
```

### 4) 스케줄러 설치

아래 중 하나를 선택합니다.

- macOS → launchd
- Linux → systemd timer 또는 cron
- Windows → Task Scheduler

---

## 설정값 설명

`config.example.json`에 필요한 필드가 정리되어 있습니다.

```json
{
  "repo_root": "/Users/you/hermes-agent",
  "hermes_home": "/Users/you/.hermes/profiles/main",
  "profile": "main",
  "stale_seconds": 600,
  "draining_stuck_seconds": 600,
  "stale_confirmations": 3,
  "log_file": "/Users/you/.hermes/profiles/main/logs/hermes-cpr.log",
  "tracker_file": "/Users/you/.hermes/profiles/main/logs/hermes-cpr-state.json"
}
```

각 필드 의미:

- `repo_root`: `hermes-agent` 체크아웃 경로
- `hermes_home`: 런타임 파일이 있는 Hermes home/profile 디렉터리
- `profile`: Hermes CLI에 넘길 profile 이름
- `stale_seconds`: `updated_at`이 얼마나 오래돼야 stale 판단을 시작할지
- `draining_stuck_seconds`: `draining` 상태를 stuck으로 보기 전 grace period
- `stale_confirmations`: 재시작 가능한 stale 상태가 몇 번 연속 확인돼야 재시작할지
- `log_file`: CPR 로그 파일 경로
- `tracker_file`: stale 반복 확인 횟수 추적 파일 경로

### 권장 운영값

목표가 **죽은 Hermes는 살리고, 건강한 Hermes는 흔들지 않는 것**이라면 아래처럼 보수적으로 두는 게 안전합니다.

- `stale_seconds`: **600 이상 권장**
- `stale_confirmations`: **3 이상 권장**
- 스케줄러 주기: **1분이면 충분**

이유:

- 프로세스가 정말 죽으면 `alive=False`로 즉시 감지해서 `start`를 시도하므로, `stale_seconds`를 크게 잡아도 dead-process 복구는 크게 느려지지 않습니다.
- 반대로 stale 기준이 너무 작으면, 조용하지만 건강한 gateway를 쓸데없이 흔들 가능성이 커집니다.

설정값이 비정상인 경우에는 CPR이 지나치게 공격적으로 동작하지 않도록 기본값 또는 최소값으로 보정합니다.

---

## 플랫폼별 설치

### macOS (launchd)

1분마다 실행되는 launch agent를 설치하려면:

```bash
zsh install_macos_launchd.sh 1
```

생성되는 항목:

- `~/Library/LaunchAgents/ai.hermes.cpr.plist`

### Linux (systemd)

샘플 unit 파일이 아래 경로에 포함되어 있습니다.

- `systemd/hermes-cpr.service`
- `systemd/hermes-cpr.timer`

타이머를 활성화하기 전에 **service 파일을 먼저 수정**해야 합니다.

- `WorkingDirectory=`를 실제 clone 경로로 바꾸기
- `ExecStart=`를 실제 Python 실행 경로와 `config.json` 경로에 맞게 바꾸기

예시 설치:

```bash
mkdir -p ~/.config/systemd/user
cp systemd/hermes-cpr.service ~/.config/systemd/user/
cp systemd/hermes-cpr.timer ~/.config/systemd/user/
$EDITOR ~/.config/systemd/user/hermes-cpr.service
systemctl --user daemon-reload
systemctl --user enable --now hermes-cpr.timer
systemctl --user status hermes-cpr.timer
```

`systemctl --user` 주의사항:

- user timer는 보통 **사용자 systemd manager가 살아 있어야** 동작합니다.
- 로그아웃 상태에서도 CPR이 계속 살아 있어야 한다면, lingering을 켜거나 system-level unit으로 설치해야 합니다.
- lingering 예시:

```bash
sudo loginctl enable-linger $USER
```

cron을 선호한다면 1분 주기 엔트리도 가능합니다.

```cron
* * * * * cd /path/to/HERMES_CPR && /usr/bin/python3 hermes_cpr.py --config /path/to/HERMES_CPR/config.json
```

### Windows (Task Scheduler)

```powershell
powershell -ExecutionPolicy Bypass -File .\install_windows_task.ps1 -IntervalMinutes 1
```

이 명령은 `HermesCPR`라는 이름의 스케줄 작업을 만듭니다.

---

## 테스트

테스트 실행:

```bash
python3 -m unittest discover -s tests -q
```

선택 사항: 문법 확인

```bash
python3 -m py_compile hermes_cpr.py tests/test_hermes_cpr.py
```

---

## 안전 설계 원칙

이 도구의 핵심 목표는 아래 한 줄로 요약할 수 있습니다.

> 죽은 Hermes gateway를 살리되, 건강한 gateway를 죽이는 도구가 되지 않는다.

현재 반영된 주요 보호 장치:

- 플랫폼이 하나라도 `connected`면 stale restart 금지
- `active_agents > 0`이면 stale restart 금지
- platform telemetry가 없으면 stale restart 금지
- `draining`은 grace period 이후에도 반복 확인 전에는 재시작하지 않음
- 알 수 없는 gateway state는 stale이어도 재시작하지 않음
- stale 기반 재시작은 즉시 실행하지 않고 반복 확인 필요
- 권한 문제로 PID 확인이 제한될 때는 프로세스가 살아있는 것으로 간주해 오판 start 방지
- `gateway_state.json`의 PID가 오래됐더라도 `gateway.pid`의 살아있는 PID를 함께 확인
- CPR lock에는 owner 정보를 기록하고, 죽은 CPR 인스턴스가 남긴 stale lock만 회수

---

## 한국어 요약

이 프로젝트는 **이미 운영 중인 Hermes gateway**를 위한 외부 CPR/watchdog 도구입니다.

핵심 원칙:

- 프로세스가 진짜 죽었을 때 살린다
- 건강한 `running` gateway를 stale만으로 흔들지 않는다
- `connected` 플랫폼이 있거나 active agent가 남아 있으면 stale restart를 하지 않는다
- degraded stale 상태와 stuck draining 상태는 연속 확인 후에만 재시작한다
- 애매한 상태는 로그만 남기고 gateway를 건드리지 않는다

즉, **"죽었을 때 살리는 장치"** 에 가깝게 설계되어 있습니다.

---

## 저장소 구성

- `hermes_cpr.py` — CPR 핵심 로직
- `config.example.json` — 설정 예시 템플릿
- `install_macos_launchd.sh` — macOS launchd 설치 스크립트
- `install_windows_task.ps1` — Windows Task Scheduler 설치 스크립트
- `systemd/hermes-cpr.service` — Linux systemd service 샘플
- `systemd/hermes-cpr.timer` — Linux systemd timer 샘플
- `tests/test_hermes_cpr.py` — unit test

---

## 참고 사항

- `config.json`은 의도적으로 git ignore 대상입니다.
- 이 저장소는 기본적으로 runtime secret을 저장하지 않습니다.
- Hermes 배포 구조가 다르면 `config.json`과 `resolve_hermes_bin()`을 환경에 맞게 조정하세요.
- 이후 확장 아이디어로는 알림, 복구 횟수 제한, multi-profile orchestration 등이 있습니다.
