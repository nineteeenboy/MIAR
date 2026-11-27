FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY content_service ./content_service
COPY common ./common
CMD ["uvicorn", "content_service.app:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
