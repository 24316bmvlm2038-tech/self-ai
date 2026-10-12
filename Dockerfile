FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SELFAI_HOST=0.0.0.0 \
    SELFAI_DATA_DIR=/data

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY selfai ./selfai
RUN useradd --create-home selfai && mkdir -p /data && chown selfai /data
USER selfai

VOLUME /data
EXPOSE 8000
CMD ["python", "-m", "selfai"]
