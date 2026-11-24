FROM python:3.15-slim@sha256:2c879838405817bd51e53c75ab78f613f446c448070c2d70e2e99639705bd409

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .

EXPOSE 8090

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8090/_health')" || exit 1

CMD ["python", "app.py"]
