import asyncio
from fastapi import (
    FastAPI, Request, Depends, HTTPException, status, Header, WebSocket, WebSocketDisconnect
)
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from sqlalchemy.future import select
from sqlalchemy.orm import Session
from contextlib import asynccontextmanager
from typing import List

from . import models, schemas, config
from .database import engine, AsyncSessionLocal

# --- Менеджер WebSocket соединений ---
class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)

    async def broadcast_html(self, html: str):
        # Отправляем HTML всем подключенным клиентам
        for connection in self.active_connections:
            await connection.send_text(html)

manager = ConnectionManager()

# --- Жизненный цикл приложения ---
async def create_tables():
    async with engine.begin() as conn:
        await conn.run_sync(models.Base.metadata.create_all)

@asynccontextmanager
async def lifespan(app: FastAPI):
    await create_tables()
    print("Приложение запущено, база данных готова.")
    yield
    print("Приложение остановлено.")

# --- Создание и настройка FastAPI ---
app = FastAPI(lifespan=lifespan)

# Подключаем статические файлы (CSS, JS)
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# Настраиваем шаблонизатор
templates = Jinja2Templates(directory="app/templates")

# Зависимость для получения сессии БД
async def get_db():
    async with AsyncSessionLocal() as session:
        yield session

# --- WebSocket Эндпоинт ---
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            # Просто держим соединение открытым
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)
        print("Клиент отключился")

# --- API Эндпоинты ---
async def verify_api_key(x_api_key: str = Header()):
    if x_api_key != config.API_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API Key"
        )

@app.post("/api/sms", status_code=status.HTTP_201_CREATED, dependencies=[Depends(verify_api_key)])
async def create_sms(sms: schemas.SMSCreate, db: Session = Depends(get_db)):
    # 1. Сохраняем SMS в базу
    db_sms = models.SMS(sender=sms.sender, text=sms.text)
    db.add(db_sms)
    await db.commit()
    await db.refresh(db_sms)

    # 2. Формируем HTML-строку для новой записи в таблице
    new_sms_html = (
        f"<tr>"
        f"<td class='timestamp'>{db_sms.received_at.strftime('%Y-%m-%d %H:%M:%S')}</td>"
        f"<td class='sender'>{db_sms.sender}</td>"
        f"<td class='text'>{db_sms.text}</td>"
        f"</tr>"
    )

    # 3. Отправляем этот HTML всем подключенным клиентам
    await manager.broadcast_html(new_sms_html)

    return {"status": "ok", "sms_id": db_sms.id}

# --- Веб-интерфейс ---
@app.get("/", response_class=HTMLResponse)
async def read_sms_list(request: Request, db: Session = Depends(get_db)):
    result = await db.execute(select(models.SMS).order_by(models.SMS.received_at.desc()).limit(100))
    sms_messages = result.scalars().all()
    return templates.TemplateResponse(
        "index.html", {"request": request, "sms_messages": sms_messages}
    )
