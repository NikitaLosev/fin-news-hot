import os
from pathlib import Path
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import declarative_base

# Load env from project root .env if not already present (dev convenience)
if "DATABASE_URL" not in os.environ:
    try:
        # api/app/db.py -> repo_root/.env
        repo_root = Path(__file__).resolve().parents[2]
        env_file = repo_root / ".env"
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" not in line:
                    continue
                key, val = line.split("=", 1)
                key = key.strip()
                val = val.strip().strip('"').strip("'")
                os.environ.setdefault(key, val)
    except Exception:
        # Best effort only; if parsing fails we silently continue
        pass

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://news:news@localhost:5432/newsdb",
)
engine = create_async_engine(DATABASE_URL, echo=False, future=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
Base = declarative_base()
