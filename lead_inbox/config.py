import os
from dataclasses import dataclass


@dataclass
class Settings:
    supabase_url: str
    service_role_key: str
    team_api_key: str
    business_name: str = "our business"
    business_context: str = ""
    llm_model: str = "claude-sonnet-5-5"

    @classmethod
    def from_env(cls) -> "Settings":
        missing = [k for k in ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "TEAM_API_KEY") if not os.environ.get(k)]
        if missing:
            raise RuntimeError(f"Missing settings: {', '.join(missing)} (see .env.example)")
        if len(os.environ["TEAM_API_KEY"]) < 24:
            raise RuntimeError("TEAM_API_KEY must be at least 24 characters.")
        return cls(
            supabase_url=os.environ["SUPABASE_URL"].rstrip("/"),
            service_role_key=os.environ["SUPABASE_SERVICE_ROLE_KEY"],
            team_api_key=os.environ["TEAM_API_KEY"],
            business_name=os.environ.get("BUSINESS_NAME", "our business"),
            business_context=os.environ.get("BUSINESS_CONTEXT", ""),
            llm_model=os.environ.get("LLM_MODEL", "claude-sonnet-5-5"),
        )
