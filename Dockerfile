# ---------- Stage 1: Frontend (Tailwind CSS etc.) ----------
FROM node:18-alpine AS frontend-builder

WORKDIR /app/theme
COPY ./app/theme/package*.json ./
RUN npm install

COPY ./app/theme ./
RUN npm run build && npm cache clean --force


# ---------- Stage 2: Backend (Python + Django + Gunicorn etc.) ----------
FROM python:3.9-alpine3.13
LABEL maintainer="chloekoo-ky"

ENV PYTHONUNBUFFERED=1 \
    PATH="/py/bin:$PATH"

# Requirements
COPY ./requirements.txt /tmp/requirements.txt
COPY ./requirements.dev.txt /tmp/requirements.dev.txt
COPY ./app /app
WORKDIR /app
# Replace frontend files with built assets
COPY --from=frontend-builder /app/theme/static ./theme/static

ARG DEV=false

# System deps & virtualenv
RUN python -m venv /py && \
    pip install --upgrade pip && \
    apk add --update --no-cache \
        nodejs npm \
        postgresql-client jpeg-dev && \
    apk add --update --no-cache --virtual .tmp-build-deps \
        build-base postgresql-dev musl-dev zlib zlib-dev && \
    pip install -r /tmp/requirements.txt && \
    if [ $DEV = "true" ]; \
        then pip install -r /tmp/requirements.dev.txt ; \
    fi && \
    rm -rf /tmp

WORKDIR /app
RUN apk del .tmp-build-deps && \
    adduser \
        --disabled-password \
        --no-create-home \
        django-user && \
    mkdir -p /vol/web/media && \
    mkdir -p /vol/web/static && \
    chown -R django-user:django-user /vol && \
    chmod -R 755 /vol



USER django-user
EXPOSE 8000
