FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update && apt-get install -y \
    curl \
    git \
    ffmpeg \
    psmisc \
    pciutils \
    lshw \
    zstd \
    python3 \
    python3-pip \
    python3-venv \
    && rm -rf /var/cache/apt/archives/* /var/lib/apt/lists/*

# Install Node.js 22 LTS
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y nodejs \
    && npm install -g omniroute --prefer-offline --no-audit --silent --no-fund \
    && npm cache clean --force

# Copy project files
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY . /app

EXPOSE 20128

CMD ["python3", "run.py"]
