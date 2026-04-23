# HERMES_CPR

죽은 Hermes 게이트웨이를 다시 살리는 외부 복구 에이전트입니다.

이 저장소는 `hermes-agent` 바깥에서 주기적으로 상태를 점검하고, 아래 상황에서 자동 복구를 시도합니다.

- 게이트웨이 프로세스가 죽었을 때: `hermes gateway start`
- 게이트웨이가 `startup_failed` 상태일 때: `hermes gateway restart`
- 게이트웨이가 `draining` 상태로 너무 오래 멈춰 있을 때: `hermes gateway restart`
- 상태 파일(`gateway_state.json`)이 너무 오래 갱신되지 않았을 때: `hermes gateway restart`

## 동작 방식

입력:

- `HERMES_HOME/gateway_state.json`
- `HERMES_HOME/gateway.pid` 또는 상태 파일의 PID
- 로컬 `hermes` CLI

판정:

- PID가 살아 있는지 확인
- `gateway_state`
- `updated_at`
- `restart_requested`

복구:

- 죽어 있으면 `start`
- 살아 있지만 비정상 상태면 `restart`

## 설정

`config.example.json`을 `config.json`으로 복사해서 사용합니다.

```json
{
  "repo_root": "/Users/you/hermes-agent",
  "hermes_home": "/Users/you/.hermes/profiles/main",
  "profile": "main",
  "stale_seconds": 300,
  "draining_stuck_seconds": 600,
  "log_file": "/Users/you/.hermes/profiles/main/logs/hermes-cpr.log"
}
```

## 1회 실행

```bash
python3 hermes_cpr.py --config config.json
```

윈도우:

```powershell
py -3 .\hermes_cpr.py --config .\config.json
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
- 재시작 반복을 줄이기 위해 lock 파일을 사용합니다.
- macOS는 `launchd`, Windows는 Task Scheduler로 등록해서 자동 실행합니다.
- 필요하면 이후 단계에서 Discord 알림, 복구 횟수 제한, 다중 프로필 지원도 붙일 수 있습니다.
