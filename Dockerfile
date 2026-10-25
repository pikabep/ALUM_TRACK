# Companion app + agent on a server (Hugging Face Spaces, Render, Railway...). The browser runs headless and
# there is no desktop here, so desktop_* tools are unavailable. The real Tatkal booking runs on the laptop.
FROM python:3.12-slim

# PORT 7860 is what Hugging Face Spaces expects; Render/Railway override PORT at runtime.
ENV PYTHONUNBUFFERED=1 HARNESS_HEADLESS=1 HOST=0.0.0.0 PORT=7860 NO_BROWSER=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright HOME=/home/user
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
 && python -m playwright install --with-deps chromium \
 && chmod -R a+rx /ms-playwright

COPY . .
RUN cp -n data/passenger.example.txt data/passenger.txt || true \
 && useradd -m -u 1000 user 2>/dev/null || true \
 && mkdir -p runs state /home/user && chown -R 1000:1000 /app /home/user
# Spaces run the container as uid 1000; it must own the folders it writes (runs/, state/, browser profile).
USER 1000

EXPOSE 7860
CMD ["python", "-m", "harness", "app", "--no-open"]
