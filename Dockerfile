FROM python:3.11-slim@sha256:0b23cfb7425d065008b778022a17b1551c82f8b4866ee5a7a200084b7e2eafbf

RUN pip install poetry==2.3.2 && mkdir /src
COPY poetry.lock pyproject.toml README.md entrypoint.sh /src
COPY diff_poetry_lock /src/diff_poetry_lock
RUN python3 -m venv /src/.venv && poetry install --directory /src --without=dev

ENTRYPOINT ["/src/entrypoint.sh"]
