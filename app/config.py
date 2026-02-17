from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    openai_api_key: str = ""
    elevenlabs_api_key: str = ""
    sarvam_api_key: str = ""
    firecrawl_api_key: str = ""
    ball_delay_seconds: float = 20.0

    # Commentator personality — controls LLM prompt style
    # Options: default, hype_man, storyteller, analyst, entertainer, freestyle
    commentator_personality: str = "default"

    # Legacy fallbacks — only used if languages.json voice_id is empty
    elevenlabs_voice_id: str = "wo6udizrrtpIxWGp2qJk"
    sarvam_speaker: str = "shubh"
    openai_tts_voice: str = "ash"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


settings = Settings()
