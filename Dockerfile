FROM python:3.10.5-slim-bullseye AS app
LABEL maintainer="Nick Janetakis <nick.janetakis@gmail.com>"

# `DJANGO_ENV` arg is used to make prod / dev builds:
ARG UID=1000 \
  GID=1000

ENV PYTHONPATH="." \
  PATH="${PATH}:/home/python/.local/bin" \
  PYTHONFAULTHANDLER=1 \
  PYTHONUNBUFFERED=1 \
  PYTHONHASHSEED=random \
  PYTHONDONTWRITEBYTECODE=1 \
  # pip:
  PIP_NO_CACHE_DIR=1 \
  PIP_DISABLE_PIP_VERSION_CHECK=1 \
  PIP_DEFAULT_TIMEOUT=100 \
  # dockerize:
  DOCKERIZE_VERSION=v0.6.1 \
  # tini:
  TINI_VERSION=v0.19.0 \
  # poetry:
  POETRY_VERSION=1.1.14 \
  POETRY_NO_INTERACTION=1 \
  POETRY_VIRTUALENVS_CREATE=false \
  POETRY_CACHE_DIR='/var/cache/pypoetry' \
  POETRY_HOME='/usr/local'

SHELL ["/bin/bash", "-eo", "pipefail", "-c"]

WORKDIR /app

RUN apt-get update \
  && apt-get install -y --no-install-recommends build-essential bash brotli curl git\
  && rm -rf /var/lib/apt/lists/* /usr/share/doc /usr/share/man \
  && apt-get clean \
  && useradd --create-home python

RUN curl -sSL 'https://install.python-poetry.org' | python - \
  && poetry --version


COPY --chown=python:python ./poetry.lock ./pyproject.toml ./

# RUN --mount=type=cache,target="$POETRY_CACHE_DIR" \
RUN  echo poetry version \
  # Install deps:
  && poetry run pip install -U pip \
  && poetry install --no-dev --no-interaction --no-ansi

COPY --chown=python:python . .

WORKDIR /app/src

# USER python

VOLUME /app/conf

ENTRYPOINT python /app/src/vuegraf.py /app/conf/vuegraf.json