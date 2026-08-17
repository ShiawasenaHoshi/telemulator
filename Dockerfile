FROM python:3.12-slim

WORKDIR /app
RUN pip install --no-cache-dir "fastapi>=0.110" "uvicorn[standard]>=0.27" "python-multipart>=0.0.9" "httpx>=0.27"

COPY telemulator/ ./telemulator/

EXPOSE 8081
CMD ["uvicorn", "telemulator.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8081"]
