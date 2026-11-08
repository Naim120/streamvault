FROM python:3.12-slim

WORKDIR /app

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./backend/
COPY admin-local/ ./admin-local/

EXPOSE 4000

CMD ["uvicorn", "admin-local.server:app", "--host", "127.0.0.1", "--port", "4000"]
