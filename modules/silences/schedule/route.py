from fastapi import FastAPI
from .config import Config , ALERTMANAGERS


silence_shedule =  APIRouter(
    prefix="/silence",
    tags=["Создание сайленса по шедулеру"]
)

@silence_shedule.post("")
async def create_silence(req: Config):
  url_am = ALERTMANAGERS[req.env]
  full_body = build_message(req)
  result = await send_am(url_am, full_body)
  save_hub(full_body)
