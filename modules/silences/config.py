import os
from dotenv import load_dotenv
from pydantic import BaseModel

load_dotenv()

ALERTMANAGERS = {}
for key, value in os.environ.items():
    if key.lower().startswith("alert_"):
        new_key = key.removeprefix("alert_").lower()
        ALERTMANAGERS[new_key] = value

if not ALERTMANAGERS:
    raise RuntimeError("не задано ни одного alert_* в окружении")

class Config(BaseModel):
    env: str
    windows: dict
    created_by: str
    comment: str
