import os
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("API_KEY")
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///sms_database.db")

if not API_KEY:
    # На dev окружении позволим работать с дефолтным ключом
    API_KEY = "dev-secret-key"