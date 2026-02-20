FROM python:3.10-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    android-tools-adb \
    sqlite3 \
    wget \
    && rm -rf /var/lib/apt/lists/*

COPY manager.py .
COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

CMD ["python", "manager.py"]
