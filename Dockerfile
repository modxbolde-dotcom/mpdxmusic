# ════════════════════════════════════════════════════════════
#   Telegram Music Bot — Docker image (used by Render)
# ════════════════════════════════════════════════════════════

FROM nikolaik/python-nodejs:python3.11-nodejs18

# System dependencies (ffmpeg is required for audio streaming)
RUN apt-get update -y && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends \
        ffmpeg \
        git \
        wget \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

COPY . /app/
WORKDIR /app/

# Python dependencies
RUN pip3 install --no-cache-dir --upgrade pip \
    && pip3 install --no-cache-dir --upgrade --requirement requirements.txt

# Installs Deno + the bgutil PO-token provider. Without it yt-dlp has no
# proof-of-origin provider and cloud IPs hit YouTube's
# "Sign in to confirm you're not a bot" wall.
RUN chmod +x bin/post_compile && bin/post_compile /app

# Start the bot
CMD ["python3", "-m", "melody"]
