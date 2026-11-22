FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY common ./common
COPY interaction_service ./interaction_service
CMD ["uvicorn", "interaction_service.app:app", "--host", "0.0.0.0", "--port", "8000"]
