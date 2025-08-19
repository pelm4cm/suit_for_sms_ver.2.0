FROM python:3.10-slim
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache -r requirements.txt

COPY ./app ./app

ENV API_KEY="change-me"
ENV DATABASE_URL="sqlite+aiosqlite:////app/app/sms_database.db"

CMD ["gunicorn", "-w", "1", "-k", "uvicorn.workers.UvicornWorker", "app.main:app", "--bind", "0.0.0.0:8000"]
