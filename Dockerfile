FROM python:3.11-slim

RUN pip install poetry==2.3.2 && mkdir /src
COPY poetry.lock pyproject.toml README.md entrypoint.sh /src
COPY diff_poetry_lock /src/diff_poetry_lock
RUN python3 -m venv /src/.venv && poetry install --directory /src --without=dev

ENTRYPOINT ["/src/entrypoint.sh"]
