# syntax=docker/dockerfile:1
#
# The matching pipeline, so that nothing but Docker is needed to run it.
# Built and invoked through the `pipeline` service in docker-compose.yml:
#
#   ./pipeline.sh check
#   ./pipeline.sh match --name SBP --description "Systolic blood pressure"
#
FROM python:3.12-slim

# 1 installs Stage 5's embedding stack (CPU torch + sentence-transformers),
# which is most of the image size. 0 leaves it out — the pipeline then behaves
# exactly as it does on a host without torch: Stage 5 is skipped and its 20%
# weight is redistributed across the stages that did run.
ARG WITH_EMBEDDINGS=1

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HF_HOME=/cache/huggingface

WORKDIR /app

# Dependencies first, so an edit to the pipeline source does not reinstall
# torch. The CPU wheel index is used deliberately: the default torch wheel
# pulls ~2.5GB of CUDA libraries this pipeline never touches. Installing
# requirements-embeddings.txt afterwards finds torch already satisfied.
COPY requirements.txt requirements-embeddings.txt ./
RUN pip install -r requirements.txt \
 && if [ "$WITH_EMBEDDINGS" = "1" ]; then \
        pip install --index-url https://download.pytorch.org/whl/cpu torch \
     && pip install -r requirements-embeddings.txt ; \
    fi

COPY pipeline/ ./pipeline/
COPY benchmarks/ ./benchmarks/
COPY tests/ ./tests/
COPY docker-entrypoint.sh /usr/local/bin/entrypoint

# uid 1000 matches the usual single-user Linux host, so CSVs written into the
# bind-mounted ./data and ./benchmarks come back owned by the person who ran
# the container rather than by root.
RUN chmod +x /usr/local/bin/entrypoint \
 && (groupadd --gid 1000 app || true) \
 && (useradd --uid 1000 --gid 1000 --create-home app || true) \
 && mkdir -p /cache/huggingface /app/data \
 && chown -R 1000:1000 /cache /app
USER 1000:1000

ENTRYPOINT ["entrypoint"]
CMD ["--help"]
