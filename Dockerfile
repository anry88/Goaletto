FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
RUN groupadd --system goaletto && useradd --system --gid goaletto goaletto
COPY requirements.lock ./
RUN pip install -r requirements.lock
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
COPY migrations ./migrations
COPY alembic.ini ./
RUN pip install --no-deps .
USER goaletto
CMD ["goaletto-bot"]

FROM runtime AS with-assets
COPY --chown=goaletto:goaletto assets/portraits/whole-v2 ./assets/portraits/whole-v2
