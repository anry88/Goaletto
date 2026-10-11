FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
RUN groupadd --system goaletto && useradd --system --gid goaletto goaletto
COPY requirements.lock ./
RUN pip install -r requirements.lock
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
COPY migrations ./migrations
COPY alembic.ini ./
COPY assets/portraits ./assets/portraits
RUN pip install --no-deps . && chown -R goaletto:goaletto /app
USER goaletto
CMD ["goaletto-bot"]
