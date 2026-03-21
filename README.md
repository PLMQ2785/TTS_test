# Qwen3-TTS Voice Clone API

사용자가 업로드한 음성을 기준으로 음성을 복제하여 TTS를 생성하는 데모 API입니다.

## 빠른 시작

### Docker Compose 권장

`flash-attn` 빌드 시간을 매번 로컬 설치에 쓰지 않도록, 아키텍처별 Docker 이미지 빌드를 지원합니다.

GPU별 권장 env 파일:

| GPU | 아키텍처 | env 파일 |
|------|---------|----------|
| RTX 3060 / 3090 | Ampere (sm86) | `.env.docker.sm86` |
| RTX 4090 | Ada (sm89) | `.env.docker.sm89` |
| RTX 5090 | Blackwell (sm120) | `.env.docker.sm120` |

최초 1회 빌드 + 실행:

```bash
docker compose --env-file .env.docker.sm86 up -d --build
```

이미지만 먼저 빌드할 때:

```bash
./build_docker.sh sm86
```

전체 아키텍처 이미지를 한 번에 빌드할 때:

```bash
./build_docker.sh all
```

중지/삭제:

```bash
docker compose --env-file .env.docker.sm86 down
```

다시 실행만 할 때:

```bash
docker compose --env-file .env.docker.sm86 up -d
```

사전 요구사항:
1. Docker Engine / Docker Compose
2. NVIDIA 드라이버
3. NVIDIA Container Toolkit

기본 설정:
1. `flash-attn`은 선택한 `TORCH_CUDA_ARCH_LIST`로 이미지 빌드 시 한 번만 컴파일
2. 모델 파일은 이미지에 넣지 않고, 현재 프로젝트 디렉터리를 컨테이너에 읽기 전용 마운트
3. 등록된 음성 데이터는 `./voices` 볼륨에 유지

필요하면 `.env.docker.sm86` 등의 값을 수정해서 `MODEL_PATH`, `PORT`, `CUDA_VISIBLE_DEVICES`를 바꿀 수 있습니다.

### 로컬 실행

Docker를 쓰지 않을 경우 기존 방식대로 실행할 수 있습니다.

```bash
# 서버 실행 (uv 설치/환경 확인 자동 처리)
./run_server.sh
```

스크립트가 자동으로:
1. `uv` 설치 여부 확인 (없으면 설치)
2. 가상환경 & 의존성 동기화 (`uv sync`)
3. 서버 포트 입력 받아 실행

## API 사용법

### Health Check

```bash
curl http://localhost:8000/health
```

### Voice Clone TTS

**기본 사용 (x-vector only 모드)**
```bash
curl -X POST http://localhost:8000/tts/voice-clone \
  -F "ref_audio=@sample_voice.wav" \
  -F "text=안녕하세요, 반갑습니다." \
  -F "language=Korean" \
  --output output.wav
```

**ICL 모드 (레퍼런스 텍스트 포함 — 더 정확한 복제)**
```bash
curl -X POST http://localhost:8000/tts/voice-clone \
  -F "ref_audio=@sample_voice.wav" \
  -F "text=안녕하세요, 반갑습니다." \
  -F "ref_text=레퍼런스 음성의 텍스트 내용" \
  -F "language=Korean" \
  --output output.wav
```

### 파라미터

| 파라미터 | 타입 | 필수 | 설명 |
|---------|------|------|------|
| `ref_audio` | File | ✅ | 레퍼런스 음성 파일 (wav/mp3/flac/ogg/m4a) |
| `text` | string | ✅ | 합성할 텍스트 |
| `ref_text` | string | ❌ | 레퍼런스 음성 텍스트 (ICL 모드 활성화) |
| `language` | string | ❌ | 언어 (기본: Auto) |

---

## Postman 테스트 가이드

### 1. Health Check

| 항목 | 값 |
|------|---|
| **Method** | `GET` |
| **URL** | `http://localhost:8000/health` |

→ Send 클릭 → `{"status":"ok","model_loaded":true}` 응답 확인

---

### 2. Voice Clone TTS (기본 — x-vector only 모드)

| 항목 | 값 |
|------|---|
| **Method** | `POST` |
| **URL** | `http://localhost:8000/tts/voice-clone` |

**Body 설정:**
1. `Body` 탭 선택
2. `form-data` 선택
3. 아래 Key-Value 추가:

| Key | Type | Value |
|-----|------|-------|
| `ref_audio` | **File** | 레퍼런스 음성 파일 선택 (wav/mp3/flac) |
| `text` | Text | `안녕하세요, 반갑습니다.` |
| `language` | Text | `Korean` |

> ⚠️ `ref_audio`의 Type을 반드시 **File**로 변경해야 합니다 (Key 옆 드롭다운)

→ Send 클릭 → wav 바이너리 응답
→ 응답의 `Save Response` → `Save to a file`로 wav 파일 저장 후 재생

---

### 3. Voice Clone TTS (ICL 모드 — 더 정확한 복제)

위와 동일하되, `ref_text` 필드를 추가합니다:

| Key | Type | Value |
|-----|------|-------|
| `ref_audio` | **File** | 레퍼런스 음성 파일 선택 |
| `text` | Text | `안녕하세요, 반갑습니다.` |
| `ref_text` | Text | `레퍼런스 음성에서 말하는 텍스트 내용` |
| `language` | Text | `Korean` |

> 💡 `ref_text`에 레퍼런스 음성의 실제 대사를 입력하면 ICL(In-Context Learning) 모드가 활성화되어 더 정확한 음성 복제가 됩니다.

---

### 4. 음성 프로필 등록 (Voice Register)

한 번 등록하면 이후 ref_audio 재업로드 없이 voice_id만으로 TTS 가능

| 항목 | 값 |
|------|---|
| **Method** | `POST` |
| **URL** | `http://localhost:8000/tts/voices` |

**Body → form-data:**

| Key | Type | Value |
|-----|------|-------|
| `ref_audio` | **File** | 레퍼런스 음성 파일 선택 |
| `name` | Text | `내 음성` (프로필 이름) |
| `ref_text` | Text | `레퍼런스 음성 대사` (선택) |

→ 응답 예시:
```json
{
  "voice_id": "a1b2c3d4e5f6",
  "name": "내 음성",
  "created_at": "2026-02-10T04:00:00Z",
  "icl_mode": true
}
```
> 📌 `voice_id`를 메모해 두세요 — 이후 TTS 요청에 사용됩니다.

---

### 5. 등록된 음성 목록 조회

| 항목 | 값 |
|------|---|
| **Method** | `GET` |
| **URL** | `http://localhost:8000/tts/voices` |

→ Body 없이 Send

---

### 6. 등록된 음성으로 TTS 생성

| 항목 | 값 |
|------|---|
| **Method** | `POST` |
| **URL** | `http://localhost:8000/tts/voices/{voice_id}/synthesize` |

> ⚠️ URL의 `{voice_id}`를 실제 등록된 voice_id로 교체하세요 (예: `a1b2c3d4e5f6`)

**Body → form-data:**

| Key | Type | Value |
|-----|------|-------|
| `text` | Text | `안녕하세요, 반갑습니다.` |
| `language` | Text | `Korean` |

→ Send 클릭 → wav 바이너리 응답
→ `Save Response` → `Save to a file`로 저장

---

### 7. 등록된 음성 삭제

| 항목 | 값 |
|------|---|
| **Method** | `DELETE` |
| **URL** | `http://localhost:8000/tts/voices/{voice_id}` |

→ Body 없이 Send

---

### Swagger UI

서버 실행 후 `http://localhost:{PORT}/docs`에서도 API 문서 확인 및 테스트 가능
