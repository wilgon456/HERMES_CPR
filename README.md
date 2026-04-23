# HERMES_CPR

죽은 Hermes 게이트웨이를 다시 살리는 외부 복구 에이전트입니다.

이 저장소는 `hermes-agent` 바깥에서 주기적으로 상태를 점검하고, 아래 상황에서 자동 복구를 시도합니다.

- 게이트웨이 프로세스가 죽었을 때: `hermes gateway start`
- 게이트웨이가 `startup_failed` 상태일 때: `hermes gateway restart`
- 게이트웨이가 `draining` 상태로 너무 오래 멈춰 있을 때: `hermes gateway restart`
- `running` 상태인데 플랫폼 연결이 전부 깨진 채 stale 상태가 **연속 확인**될 때만: `hermes gateway restart`

## 왜 이렇게 바꿨나

기존 CPR은 `gateway_state.json`의 `updated_at`이 오래됐다는 이유만으로 재시작을 걸 수 있었습니다.
그런데 Hermes 게이트웨이는 정상 동작 중에도 상태 파일을 heartbeat처럼 자주 갱신하지 않을 수 있어서,
`alive=True` + `gateway_state=running`인데도 조용하다는 이유만으로 재시작 루프가 생길 수 있었습니다.

이제 CPR은 아래 원칙으로 동작합니다.

- **프로세스 생존**이 최우선 신호입니다.
- `running` 상태에서는 **플랫폼 상태가 실제로 망가졌는지** 함께 봅니다.
- **active agent가 남아 있으면** stale restart를 하지 않습니다.
- stale만으로는 바로 재시작하지 않고, **연속 N회 확인**된 경우에만 재시작합니다.
- 플랫폼 telemetry가 없거나, 하나라도 `connected`면 stale restart를 하지 않습니다.

즉 CPR은 다시 "죽었을 때 살리는 장치"에 가깝게 동작합니다.

## 동작 방식

입력:

- `HERMES_HOME/gateway_state.json`
- `HERMES_HOME/gateway.pid` 또는 상태 파일의 PID
- 로컬 `hermes` CLI

판정:

- PID가 살아 있는지 확인
- `gateway_state`
- `updated_at`
- `platforms.*.state`
- stale 연속 확인 횟수

복구:

- 죽어 있으면 `start`
- 살아 있지만 `startup_failed` / `draining stuck`이면 `restart`
- `running` + stale여도 바로 재시작하지 않고, **모든 플랫폼이 degraded인 상태가 연속 확인될 때만** `restart`

## 설정

`config.example.json`을 `config.json`으로 복사해서 사용합니다.

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

설명:

- `stale_seconds`: stale로 간주하기 시작하는 기준 시간
- `draining_stuck_seconds`: `draining` 고착 판정 시간
- `stale_confirmations`: stale + degraded 상태가 몇 번 연속 확인돼야 재시작할지
- `tracker_file`: 연속 stale 확인 횟수를 저장하는 파일

## 권장 운영값

Hermes를 **살리는 것**이 목적이라면, 아래처럼 보수적으로 두는 편이 안전합니다.

- `stale_seconds`: **600 이상 권장**
- `stale_confirmations`: **3 이상 권장**
- launchd/Task Scheduler 주기: **1분 유지 가능**

이유:

- 프로세스가 정말 죽으면 `alive=False` 판정으로 즉시 `start`가 걸리므로, `stale_seconds`를 크게 잡아도 복구는 느려지지 않습니다.
- 반대로 stale 기준이 너무 작으면, 일시적인 상태 파일 정체나 플랫폼 흔들림 때문에 CPR이 건강한 게이트웨이를 건드릴 가능성이 커집니다.
- 현재 패치는 `running + connected`, `running + active_agents>0`, `running + no telemetry`를 보호하지만, 운영 기본값도 보수적으로 두는 편이 전체 시스템 안정성에 맞습니다.

## 1회 실행

```bash
python3 hermes_cpr.py --config config.json
```

윈도우:

```powershell
py -3 .\hermes_cpr.py --config .\config.json
```

## 테스트

```bash
python3 -m unittest discover -s tests -q
```

## macOS launchd 등록

1분마다 돌리려면:

```bash
zsh install_macos_launchd.sh 1
```

이 스크립트는 다음을 만듭니다.

- `~/Library/LaunchAgents/ai.hermes.cpr.plist`
- 로그 파일

## 윈도우 작업 스케줄러 등록

1분마다 돌리려면:

```powershell
powershell -ExecutionPolicy Bypass -File .\install_windows_task.ps1 -IntervalMinutes 1
```

이 스크립트는 `HermesCPR`라는 예약 작업을 만들고, 매분 `hermes_cpr.py`를 실행합니다.

## 비고

- 이 도구는 Hermes 내부에서 자가 복구하지 않고, 외부에서 CPR만 담당합니다.
- 재시작 반복을 줄이기 위해 lock 파일과 stale tracker 파일을 사용합니다.
- macOS는 `launchd`, Windows는 Task Scheduler로 등록해서 자동 실행합니다.
- 필요하면 이후 단계에서 Discord 알림, 복구 횟수 제한, 다중 프로필 지원도 붙일 수 있습니다.
