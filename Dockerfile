# ---------- Frontend build ----------
FROM node:20-bookworm-slim AS frontend-builder

WORKDIR /frontend

COPY frontend/package*.json ./
RUN npm ci

COPY frontend/ ./
RUN npm run build


# ---------- Runtime ----------
FROM python:3.11-slim

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
ENV HERMES_HOME=/app/hermes-home
ENV PATH="/usr/local/bin:/root/.local/bin:${PATH}"

RUN apt-get update && apt-get install -y --no-install-recommends \
    bash \
    curl \
    git \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Hermes Agent 설치
RUN curl -fsSL https://hermes-agent.nousresearch.com/install.sh \
    | bash -s -- --skip-browser

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . /app

# React production build
COPY --from=frontend-builder /frontend/dist /app/frontend/dist

# Hermes production config + frozen ContextPack Skill
RUN mkdir -p /app/hermes-home/skills/context-pack

COPY deploy/hermes/config.yaml /app/hermes-home/config.yaml
COPY deploy/hermes/skills/context-pack/ /app/hermes-home/skills/context-pack/
RUN chmod +x /app/deploy/hermes/start.sh

EXPOSE 8080

CMD ["/app/deploy/hermes/start.sh"]
