FROM python:3.13-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN pip install --no-cache-dir numpy==2.3.5 opencv-python-headless==4.13.0.92 Flask==3.1.2 gunicorn
COPY server.py ./
COPY code/*.py ./code/
COPY web/ ./web/
EXPOSE 8080
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "1", "--threads", "4", "--timeout", "120", "server:app"]
