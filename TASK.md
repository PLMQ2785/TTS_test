현재 프로젝트 폴더에 uv를 사용하여 qwen3-tts 환경 준비가 완료되었다.

목표 : qwen3-tts voice clone을 사용하여
사용자가 업로드한 음성을 기준으로 음성을 복제한 tts 데모 API 만들기

Qwen3-TTS Github Page에 샘플 예시로 만들어진 것은
import torch
import soundfile as sf
from qwen_tts import Qwen3TTSModel

model = Qwen3TTSModel.from_pretrained(
    "Qwen/Qwen3-TTS-12Hz-1.7B-Base",
    device_map="cuda:0",
    dtype=torch.bfloat16,
    attn_implementation="flash_attention_2",
)

ref_audio = "https://qianwen-res.oss-cn-beijing.aliyuncs.com/Qwen3-TTS-Repo/clone.wav"
ref_text  = "Okay. Yeah. I resent you. I love you. I respect you. But you know what? You blew it! And thanks to you."

wavs, sr = model.generate_voice_clone(
    text="I am solving the equation: x = [-b ± √(b²-4ac)] / 2a? Nobody can — it's a disaster (◍•͈⌔•͈◍), very sad!",
    language="English",
    ref_audio=ref_audio,
    ref_text=ref_text,
)
sf.write("output_voice_clone.wav", wavs[0], sr)
이것임.

FASTAPI를 사용할 것이며, 패키지 관리는 uv를 사용할 것.

API는 음성파일을 입력받아, 음성을 복제한 tts를 반환하는 구조로 설계할 것.