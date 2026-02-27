FROM python:3.11-slim

WORKDIR /app


RUN apt-get update && apt-get install -y \
    android-tools-adb \
    sqlite3 \
    wget \
    git \
    libglib2.0-0 \
    libgthread-2.0-0 \
    libxcb-xinerama0 \
    libxcb-icccm4 \
    libxcb-image0 \
    libxcb-keysyms1 \
    libxcb-randr0 \
    libxcb-render-util0 \
    libxcb-shape0 \
    libxcb-xfixes0 \
    libxcb-xkb1 \
    libxkbcommon-x11-0 \
    && rm -rf /var/lib/apt/lists/*


COPY manager.py .
COPY requirements.txt .

RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

CMD ["python", "manager.py"]