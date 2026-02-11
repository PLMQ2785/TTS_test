# Qwen3-TTS Voice Clone API — 코드 리뷰

## 프로젝트 구조

```
qwen3_TTS_Custom/
├── app/
│   ├── __init__.py
│   ├── main.py              ← FastAPI 앱 진입점 (모델 로딩)
│   ├── schemas.py            ← Pydantic 응답 모델
│   └── routers/
│       ├── __init__.py
│       └── tts.py            ← TTS 엔드포인트 (핵심 로직)
├── voices/                   ← 저장된 음성 프로필 (.pt + .json)
├── Qwen3-TTS-12Hz-1.7B-Base/ ← 모델 가중치
├── run_server.sh             ← 서버 실행 스크립트
├── pyproject.toml            ← 의존성 관리 (uv)
└── README.md                 ← API 사용 가이드
```

---

## app/main.py — 앱 진입점

### lifespan (모델 생명주기 관리)

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── 서버 시작 시 ──
    model = Qwen3TTSModel.from_pretrained(
        MODEL_PATH,
        device_map="cuda:1",
        dtype=torch.bfloat16,
        attn_implementation="flash_attention_2",
    )
    app.state.model = model       # 모든 라우터에서 request.app.state.model로 접근
    yield                         # ← 여기서 서버가 요청을 처리함
    # ── 서버 종료 시 ──
    app.state.model = None        # 참조 해제
    torch.cuda.empty_cache()      # GPU 메모리 반환
```

- FastAPI의 `lifespan` 패턴: `yield` 기준으로 위 = startup, 아래 = shutdown
- 모델을 **한 번만 로드**하고 모든 요청에서 공유 → GPU 메모리 효율적

### CORS 미들웨어

```python
app.add_middleware(CORSMiddleware, allow_origins=["*"], ...)
```

- 데모용이므로 모든 origin 허용
- 프로덕션 환경에서는 특정 도메인만 허용해야 함

### 라우터 등록

```python
app.include_router(tts.router)
```

- `tts.py`의 `router = APIRouter(prefix="/tts")`를 포함
- 결과: `/tts/voice-clone`, `/tts/voices`, `/tts/voices/{id}/synthesize` 등 등록

---

## app/routers/tts.py — 핵심 라우터

### 헬퍼 함수들

| 함수 | 역할 |
|------|------|
| `_ensure_voices_dir()` | `voices/` 폴더가 없으면 생성 (`mkdir(exist_ok=True)`) |
| `_save_upload_to_temp()` | 업로드된 바이트 → 임시파일 저장, 경로 반환 |
| `_validate_audio_extension()` | 파일 확장자 체크 (`.wav`, `.mp3`, `.flac` 등), 범위 밖이면 400 에러 |
| `_get_model()` | `app.state.model` 가져오기, `None`이면 503 에러 |

---

### POST /tts/voice-clone — 일회성 음성 복제

**흐름:** 클라이언트 → ref_audio 업로드 → 임시파일 저장 → 모델 추론 → wav 반환 → 임시파일 삭제

#### 모드 결정 로직

```python
use_ref_text = ref_text.strip() if ref_text else None
x_vector_only = use_ref_text is None
```

| ref_text 입력 | use_ref_text | x_vector_only | 모드 |
|-------------|-------------|---------------|------|
| 있음 | `"텍스트"` | `False` | **ICL 모드** — 임베딩 + 코드 + 텍스트 → 더 정확한 복제 |
| 없음 / 공백 | `None` | `True` | **x-vector only** — 스피커 임베딩만 사용 |

> ICL 모드에서 `ref_text`가 필수인 이유:
> 모델이 "이 음성이 이 텍스트를 말하고 있다"는 쌍(pair)을 예시로 학습하는 방식이므로,
> 텍스트 없이는 ICL이 동작할 수 없음 (라이브러리 자체에서 `ValueError` 발생)

#### 모델 호출

```python
wavs, sr = model.generate_voice_clone(
    text=text,              # 합성할 텍스트
    language=language,       # 언어 (Auto, Korean, English 등)
    ref_audio=tmp_path,      # 레퍼런스 음성 파일 경로
    ref_text=use_ref_text,   # 레퍼런스 텍스트 (ICL용)
    x_vector_only_mode=x_vector_only,
)
# wavs: List[np.ndarray] — 생성된 파형 리스트
# sr: int — 샘플레이트
```

- `generate_voice_clone`은 내부적으로 `create_voice_clone_prompt()` + `model.generate()`를 한 번에 처리

#### WAV 인코딩 & 응답

```python
buf = io.BytesIO()                    # 메모리 버퍼 생성
sf.write(buf, wavs[0], sr, format="WAV")  # numpy → WAV 바이너리
buf.seek(0)                           # 포인터를 처음으로 되돌림 (읽기 준비)

return StreamingResponse(buf, media_type="audio/wav", ...)
```

- 파일을 디스크에 저장하지 않고, 메모리에서 바로 클라이언트로 스트리밍

#### 임시파일 정리

```python
finally:
    if tmp_path:
        Path(tmp_path).unlink(missing_ok=True)  # 성공/실패 관계없이 항상 삭제
```

---

### POST /tts/voices — 음성 프로필 등록

**voice-clone과의 차이:** 음성을 **생성하지 않고**, 프롬프트 정보(임베딩+코드)만 **추출하여 저장**

#### 프롬프트 추출

```python
prompt_items = model.create_voice_clone_prompt(
    ref_audio=tmp_path,
    ref_text=use_ref_text,
    x_vector_only_mode=x_vector_only,
)
```

- `generate_voice_clone()`과 달리 음성 생성 없이 **임베딩 추출만** 수행
- 빠르고 가벼운 연산

#### 저장 구조 (.pt + .json)

```python
# 텐서 데이터 → .pt (PyTorch 직렬화)
save_data = {
    "ref_code": prompt_item.ref_code,            # 레퍼런스 음성 코드 (Tensor 또는 None)
    "ref_spk_embedding": prompt_item.ref_spk_embedding,  # 스피커 임베딩 벡터 (Tensor)
    "x_vector_only_mode": ...,                   # bool
    "icl_mode": ...,                             # bool
    "ref_text": ...,                             # str or None
}
torch.save(save_data, voices/{voice_id}.pt)

# 메타데이터 → .json (가벼운 정보만)
meta = {"voice_id": ..., "name": ..., "created_at": ..., "icl_mode": ...}
```

> **왜 .pt와 .json을 분리했는가?**
> - `.pt` 파일: 텐서 데이터라 크고 무거움 (수 MB)
> - `.json` 파일: 문자열 데이터만 (수 bytes)
> - 목록 조회(`GET /tts/voices`) 시 `.json`만 읽으면 되므로 빠름

---

### GET /tts/voices — 저장된 음성 목록

```python
for meta_file in sorted(VOICES_DIR.glob("*.json")):  # .json만 순회
    meta = json.loads(meta_file.read_text())
    voices.append(VoiceInfo(...))
```

- `.pt` 파일은 건드리지 않고 `.json`만 읽어 목록 반환

---

### DELETE /tts/voices/{voice_id} — 음성 삭제

```python
pt_path.unlink(missing_ok=True)    # .pt 삭제
json_path.unlink(missing_ok=True)  # .json 삭제
```

- `missing_ok=True`: 이미 삭제된 경우 에러 없이 넘어감

---

### POST /tts/voices/{voice_id}/synthesize — 저장된 음성으로 TTS

**voice-clone과의 핵심 차이:**

| | voice-clone | synthesize |
|-|------------|-----------|
| 입력 | `ref_audio` (파일) | `voice_id` (문자열) |
| 모델 호출 | `ref_audio=파일경로` | `voice_clone_prompt=[저장된 객체]` |
| 임베딩 추출 | **매번 수행** | **건너뜀 (이미 추출됨)** |
| 속도 | 느림 | **빠름** |

#### 프롬프트 로드 & 복원

```python
# .pt 파일 로드, GPU 디바이스로 텐서 매핑
data = torch.load(pt_path, map_location=model.device, weights_only=False)

# 딕셔너리 → VoiceClonePromptItem 객체로 복원
prompt_item = VoiceClonePromptItem(
    ref_code=data["ref_code"],
    ref_spk_embedding=data["ref_spk_embedding"],
    ...
)
```

- `map_location=model.device`: 저장 시 GPU:0에 있었어도 로드 시 현재 모델 디바이스로 자동 이동
- `weights_only=False`: 텐서 외에 bool, str 데이터도 포함되어 있으므로 필요

#### 모델 호출

```python
wavs, sr = model.generate_voice_clone(
    text=text,
    language=language,
    voice_clone_prompt=[prompt_item],  # ← ref_audio 대신 이미 추출된 프롬프트 전달
)
```

---

## 공통 에러 처리 패턴

모든 엔드포인트에서 동일한 try/except 구조를 사용:

```python
try:
    ...                          # 비즈니스 로직
except HTTPException:
    raise                        # 의도적 HTTP 에러 (400, 404 등) → 그대로 전파
except Exception as e:
    logger.exception(...)        # 예상 못한 에러 → 스택트레이스 로그
    raise HTTPException(500, ...) from e  # 500으로 래핑하여 반환
finally:
    ...                          # 임시파일 정리 (항상 실행)
```

---

## app/schemas.py — 응답 모델

```python
class HealthResponse(BaseModel):     # GET /health 응답
    status: str
    model_loaded: bool

class VoiceInfo(BaseModel):          # 음성 프로필 정보
    voice_id: str
    name: str
    created_at: datetime
    icl_mode: bool

class VoiceListResponse(BaseModel):  # GET /tts/voices 응답
    voices: list[VoiceInfo]
    total: int
```

---

## run_server.sh — 서버 실행 스크립트

```
1. uv 설치 확인 → 없으면 자동 설치
2. .venv 확인 → uv sync로 의존성 동기화
3. 포트 번호 입력 (기본: 8000)
4. uv run uvicorn app.main:app --host 0.0.0.0 --port {PORT}
```

---

## 전체 데이터 흐름 요약

```
[일회성 복제]
  ref_audio 업로드 → 임시저장 → create_prompt + generate → wav 반환 → 임시파일 삭제

[음성 등록 + 재사용]
  1. 등록: ref_audio 업로드 → create_prompt → .pt 저장 → voice_id 반환
  2. TTS:  voice_id → .pt 로드 → generate(voice_clone_prompt=...) → wav 반환
```
