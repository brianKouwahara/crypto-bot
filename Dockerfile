# ---- Base image ----
FROM python:3.11-slim

# Evite les prompts
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Paquets système utiles (certificats, tzdata, build minimal)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates tzdata \
 && rm -rf /var/lib/apt/lists/*

# ---- Dossier de travail ----
WORKDIR /app

# Copie uniquement les deps d'abord (cache build)
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Copie du code
COPY . /app

# Répertoire persistant pour l'état / heartbeat (monté via Render Disk)
# Si le disque est monté sur /data, ces fichiers y pointeront par défaut
ENV STATE_FILE=/data/state.json \
    HEARTBEAT_FILE=/data/heartbeat.txt

# Démarrage du bot
CMD ["python", "bot.py"]
