# Qwen3-TTS API — Postman 테스트 가이드

> **Base URL**: `http://<서버IP>:<PORT>` (예: `http://210.182.182.205:8010`)

---

## 0. Health Check

| 항목 | 값 |
|---|---|
| Method | `GET` |
| URL | `{{base_url}}/health` |

**응답 예시:**
```json
{
  "status": "ok",
  "model_loaded": true,
  "current_model": "base",
  "queue_pending": 0
}
```

---

## 1. Voice Clone (직접 음성 복제)

> 참조 오디오를 업로드하여 그 음성으로 TTS 생성

| 항목 | 값 |
|---|---|
| Method | `POST` |
| URL | `{{base_url}}/tts/voice-clone` |
| Body | `form-data` |

| Key | Type | 필수 | 값 예시 |
|---|---|---|---|
| `ref_audio` | File | ✅ | 참조 음성 파일 (wav/mp3/flac) |
| `text` | Text | ✅ | `안녕하세요, 음성 복제 테스트입니다.` |
| `ref_text` | Text | ❌ | 참조 음성의 대본 (있으면 품질 향상) |
| `language` | Text | ❌ | `Korean` (기본: `Auto`) |

**응답**: `audio/wav` 파일 다운로드

---

## 2. Voice 등록 (재사용을 위한 음성 프로필 저장)

> 한번 등록하면 voice_id로 반복 사용 가능

| 항목 | 값 |
|---|---|
| Method | `POST` |
| URL | `{{base_url}}/tts/voices` |
| Body | `form-data` |

| Key | Type | 필수 | 값 예시 |
|---|---|---|---|
| `ref_audio` | File | ✅ | 참조 음성 파일 |
| `name` | Text | ✅ | `내 목소리` |
| `ref_text` | Text | ❌ | 참조 음성의 대본 |

**응답 예시:**
```json
{
  "voice_id": "a1b2c3d4e5f6",
  "name": "내 목소리",
  "created_at": "2026-02-11T05:00:00Z",
  "icl_mode": true
}
```

---

## 3. 등록된 Voice 목록 조회

| 항목 | 값 |
|---|---|
| Method | `GET` |
| URL | `{{base_url}}/tts/voices` |

**응답 예시:**
```json
{
  "voices": [
    { "voice_id": "a1b2c3d4e5f6", "name": "내 목소리", "created_at": "...", "icl_mode": true }
  ],
  "total": 1
}
```

---

## 4. 등록된 Voice로 TTS 합성

| 항목 | 값 |
|---|---|
| Method | `POST` |
| URL | `{{base_url}}/tts/voices/{voice_id}/synthesize` |
| Body | `form-data` |

| Key | Type | 필수 | 값 예시 |
|---|---|---|---|
| `text` | Text | ✅ | `테스트 음성입니다.` |
| `language` | Text | ❌ | `Korean` (기본: `Auto`) |

**응답**: `audio/wav` 파일 다운로드

---

## 5. Voice 삭제

| 항목 | 값 |
|---|---|
| Method | `DELETE` |
| URL | `{{base_url}}/tts/voices/{voice_id}` |

---

## 6. Voice Design (자연어로 음성 설계)

> instruct에 원하는 음색/스타일을 자연어로 기술

| 항목 | 값 |
|---|---|
| Method | `POST` |
| URL | `{{base_url}}/tts/voice-design` |
| Body | `form-data` |

| Key | Type | 필수 | 값 예시 |
|---|---|---|---|
| `text` | Text | ✅ | `안녕하세요, 음성 디자인 테스트입니다.` |
| `instruct` | Text | ✅ | `밝고 활기찬 젊은 여성 목소리, 빠르게 말하기` |
| `language` | Text | ❌ | `Korean` (기본: `Auto`) |

**instruct 예시:**
- `낮은 톤의 차분한 남성 목소리, 느리게 말하기`
- `Excited young female with high pitch, speaking fast`
- `体现撒娇稚嫩的萝莉女声，音调偏高`

**응답**: `audio/wav` 파일 다운로드

> ⚠ **첫 호출 시 VoiceDesign 모델 로딩으로 수십 초 소요될 수 있음**

---

## 7. Voice Design + Clone (설계 → 등록)

> 음성을 설계하고, 재사용 가능한 voice profile로 자동 등록

| 항목 | 값 |
|---|---|
| Method | `POST` |
| URL | `{{base_url}}/tts/voice-design-clone` |
| Body | `form-data` |

| Key | Type | 필수 | 값 예시 |
|---|---|---|---|
| `name` | Text | ✅ | `활기찬 여성` |
| `ref_text` | Text | ✅ | `안녕하세요, 저는 활기찬 목소리의 아나운서입니다.` |
| `instruct` | Text | ✅ | `밝고 활기찬 젊은 여성 목소리` |
| `language` | Text | ❌ | `Korean` (기본: `Auto`) |

**응답 예시:**
```json
{
  "voice_id": "f7e8d9c0b1a2",
  "name": "활기찬 여성",
  "created_at": "2026-02-11T05:00:00Z",
  "icl_mode": true
}
```

> 등록된 `voice_id`를 **#4 (Synthesize)** 에서 재사용 가능!

---

## 8. Custom Voice (프리셋 스피커 + 스타일 제어)

> 9명의 프리셋 스피커 중 선택, instruct로 스타일 조정

| 항목 | 값 |
|---|---|
| Method | `POST` |
| URL | `{{base_url}}/tts/custom-voice` |
| Body | `form-data` |

| Key | Type | 필수 | 값 예시 |
|---|---|---|---|
| `text` | Text | ✅ | `오늘 정말 좋은 하루입니다!` |
| `speaker` | Text | ✅ | `Sohee` |
| `language` | Text | ❌ | `Korean` (기본: `Auto`) |
| `instruct` | Text | ❌ | `빠르고 신나게 말하기` |

**instruct 예시 (선택):**
- `빠르게 말하기`
- `슬프고 낮은 톤으로`
- `Very excited and energetic`
- `Speak slowly and softly with a warm tone`
- (비워두면 기본 스타일)

**응답**: `audio/wav` 파일 다운로드

> ⚠ **첫 호출 시 CustomVoice 모델 로딩으로 수십 초 소요될 수 있음**

---

## 9. 스피커 목록 조회

| 항목 | 값 |
|---|---|
| Method | `GET` |
| URL | `{{base_url}}/tts/speakers` |

**응답 예시:**
```json
{
  "speakers": [
    { "name": "Vivian", "description": "Bright, slightly edgy young female voice.", "native_language": "Chinese" },
    { "name": "Sohee", "description": "Warm Korean female voice with rich emotion.", "native_language": "Korean" }
  ],
  "total": 9
}
```

---

## 추천 테스트 순서

```
1. GET  /health                          ← 서버 상태 확인
2. GET  /tts/speakers                    ← 프리셋 스피커 확인
3. POST /tts/custom-voice               ← Sohee + instruct 테스트
4. POST /tts/voice-design               ← 자연어 음성 설계 테스트
5. POST /tts/voice-design-clone         ← 설계 → 등록
6. GET  /tts/voices                      ← 등록된 음성 확인
7. POST /tts/voices/{voice_id}/synthesize ← 등록 음성으로 TTS
```

> 💡 모델 교체가 발생하는 요청(Base↔CustomVoice↔VoiceDesign)은 첫 호출 시 로딩 시간이 추가됩니다. 같은 모델 타입의 연속 요청은 빠르게 처리됩니다.
