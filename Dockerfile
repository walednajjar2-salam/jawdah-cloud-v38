FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    JAWDAH_HOST=0.0.0.0 \
    JAWDAH_DATA_DIR=/app/data
WORKDIR /app
COPY . /app
RUN mkdir -p /app/data
EXPOSE 8765
CMD ["python", "server.py"]
