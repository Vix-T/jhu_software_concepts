FROM python:3.11-slim
# chromedriver must match the major version of the host's Chrome (the browser itself
# runs on the host; the worker only attaches to it). Exact versions are listed at
# https://googlechromelabs.github.io/chrome-for-testing/ ; docker-compose.yml passes this.
ARG CHROMEDRIVER_VERSION=154.0.8037.92
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# chromedriver for linux64, fetched once at build time, plus only the shared
# libraries it links against (glib, nss/nspr, xcb, dbus; checked with ldd).
RUN apt-get update \
    && apt-get install -y --no-install-recommends libglib2.0-0t64 libnss3 libxcb1 libdbus-1-3 \
    && rm -rf /var/lib/apt/lists/* \
    && python -c "import io, urllib.request, zipfile; zipfile.ZipFile(io.BytesIO(urllib.request.urlopen('https://storage.googleapis.com/chrome-for-testing-public/${CHROMEDRIVER_VERSION}/linux64/chromedriver-linux64.zip').read())).extract('chromedriver-linux64/chromedriver', '/tmp')" \
    && install -m 0755 /tmp/chromedriver-linux64/chromedriver /usr/local/bin/chromedriver \
    && rm -rf /tmp/chromedriver-linux64 \
    && mkdir -p /home/worker/.cache/selenium \
    && chown -R 1000 /home/worker \
    && chromedriver --version
# Selenium uses this driver (CHROMEDRIVER_PATH) and never downloads one (SE_OFFLINE);
# uid 1000 gets a writable home and Selenium cache.
ENV CHROMEDRIVER_PATH=/usr/local/bin/chromedriver \
    SE_OFFLINE=true \
    HOME=/home/worker \
    SE_CACHE_PATH=/home/worker/.cache/selenium
COPY . .
USER 1000
CMD ["python", "consumer.py"]
