FROM python:3.12-slim
WORKDIR /app
ARG ENGINE_GIT_SHA
LABEL org.opencontainers.image.revision=${ENGINE_GIT_SHA}
ENV ENGINE_GIT_SHA=${ENGINE_GIT_SHA}
ARG PIP_INDEX_URL=https://pypi.org/simple
COPY pyproject.toml ./
COPY investment_engine ./investment_engine
COPY fixtures ./fixtures
RUN pip install --no-cache-dir --index-url "${PIP_INDEX_URL}" '.[api,market-data,fundamentals]'
RUN mkdir -p /data && chown nobody:nogroup /data
ENV ENGINE_DATA_DIR=/data
USER nobody
EXPOSE 8018
CMD ["uvicorn", "investment_engine.api:app", "--host", "0.0.0.0", "--port", "8018"]
