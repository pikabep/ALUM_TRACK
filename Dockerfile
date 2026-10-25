# Companion app + agent on a server (e.g. Render). Browser runs headless; there is no desktop here,
# so desktop_* tools are unavailable. The real Tatkal booking runs on the laptop, not here.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 HARNESS_HEADLESS=1 HOST=0.0.0.0 NO_BROWSER=1
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
 && python -m playwright install --with-deps chromium

COPY . .
RUN cp -n data/passenger.example.txt data/passenger.txt || true

CMD ["python", "-m", "harness", "app", "--no-open"]
