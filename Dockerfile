FROM python:3.15-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY fonts ./fonts
COPY teletextarr ./teletextarr

RUN useradd --system --uid 1000 teletextarr
USER teletextarr

EXPOSE 8098
ENV TELETEXTARR_CONFIG=/config/config.yaml
HEALTHCHECK --interval=60s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8098/health', timeout=4)"
CMD ["python", "-u", "-m", "teletextarr"]
