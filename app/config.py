from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    openai_api_key: str = ""
    elevenlabs_api_key: str = ""
    elevenlabs_voice_id: str = "JBFqnCBsd6RMkjVDRZzb"  # "George" — energetic male voice
    sarvam_api_key: str = ""
    sarvam_speaker: str = "shubh"  # Sarvam Bulbul v3 default male voice
    openai_tts_voice: str = "ash"  # OpenAI TTS voice (ash, alloy, coral, echo, fable, onyx, nova, sage, shimmer)
    tts_provider: str = "elevenlabs"  # "elevenlabs", "sarvam", "openai"
    ball_delay_seconds: float = 20.0

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


settings = Settings()
