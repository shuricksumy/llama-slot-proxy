FROM python:3.15-slim@sha256:f786b2e19cf9bb54e96bfcd4c1b40c4c56336c22e60607ffd4b080ae5b5dee83

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .

EXPOSE 8090

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8090/_health')" || exit 1

CMD ["python", "app.py"]
