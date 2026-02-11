FROM apache/age:latest

USER root

# 1. Install build tools and PG18 headers
RUN apt-get update && apt-get install -y \
    build-essential \
    git \
    postgresql-server-dev-18 \
    && rm -rf /var/lib/apt/lists/*

# 2. Build pgvector specifically for the PG18 paths
RUN git clone --branch v0.8.0 https://github.com/pgvector/pgvector.git \
    && cd pgvector \
    && make PG_CONFIG=/usr/lib/postgresql/18/bin/pg_config \
    && make PG_CONFIG=/usr/lib/postgresql/18/bin/pg_config install \
    && cd .. \
    && rm -rf pgvector

USER postgres