# One image, two processes. The API and the worker share the same code and the
# same migrations; which one runs is a command, not a build.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app

WORKDIR /app

COPY runtime/requirements.txt runtime/requirements.txt
RUN pip install --no-cache-dir -r runtime/requirements.txt

COPY zolts/ zolts/
COPY runtime/ runtime/
COPY scripts/ scripts/

# Data the runtime reads at run time, not just at test time. Without these the
# image starts, answers /health with "ok", and seeds a tenant with no programs:
# the quickstart reported success and the product was empty.
COPY examples/schema/ examples/schema/
COPY examples/programs/ examples/programs/
COPY examples/signals/ examples/signals/
COPY blueprints/ blueprints/
COPY design/ design/

# Nothing here runs as root. A connector compromise should not also be a
# container compromise.
RUN useradd --create-home --uid 10001 zolts && chown -R zolts:zolts /app
USER zolts

EXPOSE 8000
CMD ["python3", "-m", "runtime.cli", "serve", "--host", "0.0.0.0", "--port", "8000"]
