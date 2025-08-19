import asyncio
import json
from fastapi import (
    FastAPI, Request, Depends, HTTPException, status, Header, WebSocket, WebSocketDisconnect
)
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from contextlib import asynccontextmanager
from typing import List
from datetime import datetime, timedelta # Добавляем импорты для работы со временем
from sqlalchemy import delete # Добавляем импорт delete для удаления записей
import pytz # Импортируем pytz

from . import models, schemas, config
from .database import engine, AsyncSessionLocal
# from fastapi.security import HTTPBasic, HTTPBasicCredentials # Удаляем импорт
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

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
        # Отправляем HTML всем подключенным клиентам, удаляя мёртвые соединения
        disconnected: List[WebSocket] = []
        for connection in list(self.active_connections):
            try:
                await connection.send_text(html)
            except Exception:
                disconnected.append(connection)
        for ws in disconnected:
            try:
                self.disconnect(ws)
            except Exception:
                pass

manager = ConnectionManager()

# --- Фоновая задача для очистки старых SMS ---
async def cleanup_old_sms():
    while True:
        try:
            async with AsyncSessionLocal() as session:
                five_minutes_ago = datetime.utcnow() - timedelta(minutes=5)
                # Удаляем записи, которые старше 5 минут
                result = await session.execute(
                    delete(models.SMS).where(models.SMS.received_at < five_minutes_ago)
                )
                await session.commit()
                deleted_count = result.rowcount
                if deleted_count > 0:
                    print(f"Удалено {deleted_count} старых SMS.")
        except Exception as e:
            print(f"Ошибка при удалении старых SMS: {e}")
        await asyncio.sleep(60) # Проверяем и удаляем каждые 60 секунд

# --- Жизненный цикл приложения ---
async def create_tables():
    async with engine.begin() as conn:
        await conn.run_sync(models.Base.metadata.create_all)

@asynccontextmanager
async def lifespan(app: FastAPI):
    await create_tables()
    print("Приложение запущено, база данных готова.")
    # Запускаем фоновую задачу по очистке SMS
    cleanup_task = asyncio.create_task(cleanup_old_sms())
    try:
        yield
    finally:
        print("Приложение остановлено. Ожидание завершения фоновых задач.")
        cleanup_task.cancel() # Запрашиваем отмену задачи
        try:
            await cleanup_task # Ждем завершения отмены
        except asyncio.CancelledError:
            print("Задача очистки старых SMS отменена.")
        print("Фоновые задачи завершены.")

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
async def create_sms(sms: schemas.SMSCreate, db: AsyncSession = Depends(get_db)):
    # 1. Сохраняем SMS в базу
    db_sms = models.SMS(sender=sms.sender, text=sms.text)
    db.add(db_sms)
    await db.commit()
    await db.refresh(db_sms)

    # 2. Готовим безопасный JSON для фронтенда
    ekb_tz = pytz.timezone('Europe/Ekaterinburg') # Определяем часовой пояс
    # Преобразуем UTC время в заданный часовой пояс
    localized_time = db_sms.received_at.astimezone(ekb_tz)

    payload = {
        "received_at": localized_time.strftime('%Y-%m-%d %H:%M'),
        "sender": db_sms.sender,
        "text": db_sms.text,
    }

    # 3. Отправляем JSON всем подключенным клиентам
    await manager.broadcast_html(json.dumps(payload))

    return {"status": "ok", "sms_id": db_sms.id}

# --- Веб-интерфейс ---
@app.get("/", response_class=HTMLResponse)
async def read_sms_list(request: Request, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(models.SMS).order_by(models.SMS.received_at.desc()).limit(100))
    sms_messages = result.scalars().all()

    # Преобразуем время для каждого SMS в нужный часовой пояс перед передачей в шаблон
    ekb_tz = pytz.timezone('Europe/Ekaterinburg')
    for sms in sms_messages:
        # Убедимся, что время в UTC, если оно не tz-aware, иначе astimezone может сработать некорректно
        if sms.received_at.tzinfo is None or sms.received_at.tzinfo.utcoffset(sms.received_at) is None:
            # Если время 'naive' (без tzinfo), предполагаем, что оно в UTC
            sms.received_at = pytz.utc.localize(sms.received_at) # Делаем его tz-aware (UTC)
        sms.received_at = sms.received_at.astimezone(ekb_tz) # Преобразуем в Екатеринбург

    return templates.TemplateResponse(
        "index.html", {"request": request, "sms_messages": sms_messages}
    )

# --- Healthcheck ---
@app.get("/health")
async def healthcheck():
    return {"status": "ok"}

# --- Middleware для HTTPS-схемы ---
class ForceHTTPSMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.headers.get("X-Forwarded-Proto") == "https":
            request.scope["scheme"] = "https"
        response = await call_next(request)
        return response
app.add_middleware(ForceHTTPSMiddleware)
