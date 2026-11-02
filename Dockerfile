# Kıvılcım: one container that serves the site and keeps the data fresh.
#
#   docker build -t kivilcim .
#   docker run -p 8000:8000 -e FIRMS_MAP_KEY=... -v kivilcim-data:/data kivilcim
#
# The database lives in the /data volume, so it survives restarts and upgrades.
FROM python:3.12-slim

LABEL org.opencontainers.image.title="Kıvılcım" \
      org.opencontainers.image.description="Wildfire detections from NASA FIRMS satellite data (not an official warning system)" \
      org.opencontainers.image.source="https://github.com/beyuphan/kivilcim" \
      org.opencontainers.image.licenses="MIT"

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install .

# Run as a normal user, with a place of its own for the database.
RUN useradd --system --uid 10001 --create-home kivilcim \
    && mkdir -p /data \
    && chown kivilcim:kivilcim /data
USER kivilcim

ENV DATABASE_URL=sqlite:////data/kivilcim.db
VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request, sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)"

CMD ["python", "-m", "kivilcim", "--host", "0.0.0.0"]
