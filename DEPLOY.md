# Deploying Pikalo Bot to DigitalOcean (PM2)

This guide outlines how to deploy the WebRTC bot to your DigitalOcean droplet using **PM2**.

## Prerequisites

- A DigitalOcean droplet with **Python 3.11+**, **Node.js**, and **PM2** installed.
- Root or sudo access to install system dependencies.

### 1. Install System Dependencies
The `aiortc` and `av` Python libraries require several system-level media libraries. Run the following on your droplet:

```bash
sudo apt-get update
sudo apt-get install -y \
    libavdevice-dev \
    libavfilter-dev \
    libavformat-dev \
    libavcodec-dev \
    libswresample-dev \
    libswscale-dev \
    libavutil-dev \
    pkg-config \
    build-essential
```

### 2. Transfer Files
Transfer your project files to a directory on your droplet (e.g., `~/pibot`):
- `bot.py`
- `requirements.txt`
- `sample.mp4`
- `ecosystem.config.js`

You can use `scp`:
```bash
scp bot.py requirements.txt sample.mp4 ecosystem.config.js user@your_droplet_ip:~/pibot/
```

### 3. Install Python Dependencies
On the droplet, navigate to the directory and install requirements:
```bash
cd ~/pibot
pip3 install -r requirements.txt
```

### 4. Start with PM2
Launch the bot using the ecosystem configuration:
```bash
pm2 start ecosystem.config.js
```

### 5. Management Commands
- **Check Status**: `pm2 status`
- **View Logs**: `pm2 logs pikalo-bot`
- **Restart**: `pm2 restart pikalo-bot`
- **Stop**: `pm2 stop pikalo-bot`

---

## Alternative: Docker Deployment
If you prefer Docker, use the `Dockerfile` and `docker-compose.yml` provided in the repository with `docker-compose up -d`.
