FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml README.md ./
COPY harness ./harness
COPY personas ./personas
COPY schemas ./schemas
COPY examples ./examples

RUN python -m pip install --no-cache-dir .

ENTRYPOINT ["quality-harness"]
CMD ["--version"]
