# Use a lightweight Python image
FROM python:3.11-slim

# Install system dependencies for aiortc and av
RUN apt-get update && apt-get install -y \
    libavdevice-dev \
    libavfilter-dev \
    libavformat-dev \
    libavcodec-dev \
    libswresample-dev \
    libswscale-dev \
    libavutil-dev \
    pkg-config \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the bot script and sample video
COPY bot.py .
COPY sample.mp4 .

# Run the bot
CMD ["python", "bot.py"]
