# Fixtura as a container image: one image, one process, no state.
#
# Fixtura is unusually simple to containerize — there is no database, no secrets
# or environment variables to supply, and every export is built in memory and
# streamed back (see server.py), so nothing is ever written to disk. That means
# no volumes and no docker-compose: this single image is the whole application,
# front end included, since the app serves static/ itself.
#
# Build and run it locally:
#     docker build -t fixtura .
#     docker run --rm -p 8000:8000 fixtura
# then open http://127.0.0.1:8000
#
# A "slim" base is the right trade here: it is a few hundred MB smaller than the
# full python image, and none of the dependencies (Faker, fpdf2, pypdf,
# reportlab) need a compiler — they all ship prebuilt wheels. Alpine would be
# smaller still, but it uses musl instead of glibc, so those same wheels stop
# matching and pip falls back to building from source.
FROM python:3.12-slim

# PYTHONDONTWRITEBYTECODE: skip .pyc files; the image is read-only in practice
#   and they would just be dead weight in a layer.
# PYTHONUNBUFFERED: send stdout/stderr straight out instead of buffering, so
#   `docker logs` shows output as it happens rather than in delayed chunks.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install dependencies BEFORE copying the code that changes often.
#
# Docker builds in layers and reuses a cached layer until one of its inputs
# changes. Because pip runs here — above the COPY of server.py and static/ —
# editing the app rebuilds only the last two steps instead of reinstalling
# FastAPI and reportlab every time. testgen/ and README.md have to come along
# now because pyproject.toml declares the package and its readme, and pip reads
# both while installing. ".[web]" pulls in the optional web extra (FastAPI +
# uvicorn), which is what serving Fixtura needs on top of the core engine.
COPY pyproject.toml README.md ./
COPY testgen/ ./testgen/
RUN pip install --no-cache-dir ".[web]"

# The frequently-edited parts, copied last so they sit in the cheapest layers.
COPY server.py ./
COPY static/ ./static/

# Run as an unprivileged user. Containers default to root, which means a bug
# reachable over HTTP would be a bug running as root. This user owns nothing and
# can install nothing.
RUN useradd --create-home --uid 10001 fixtura
USER fixtura

# Documents the port for humans and tooling; it publishes nothing by itself
# (that is what `docker run -p` does).
EXPOSE 8000

# Two things must differ from how the server runs on your laptop:
#
#   --host 0.0.0.0   server.py binds 127.0.0.1 when run directly, which inside a
#                    container means "reachable from this container only" — port
#                    forwarding could never reach it. 0.0.0.0 accepts traffic
#                    from outside the container.
#   --port ${PORT:-8000}
#                    most deploy platforms assign a port and pass it in as $PORT;
#                    falling back to 8000 keeps local runs unchanged.
#
# Both live here rather than in server.py, so running the app directly still
# behaves exactly as it always has.
#
# `exec` matters: without it the shell stays PID 1 and swallows the SIGTERM sent
# on shutdown, so the container would ignore `docker stop` until it was killed
# after a ~10s timeout. With exec, uvicorn *becomes* PID 1 and exits cleanly.
#
# For deploy tooling: poll GET /health — it returns {"status":"ok"} and does no
# work. To run the test suite against this image (tests/ is excluded from the
# build context on purpose), mount it in:
#   docker run --rm -v "$PWD/tests:/app/tests" fixtura \
#     sh -c "pip install --user --quiet pytest && python -m pytest -q"
CMD ["sh", "-c", "exec uvicorn server:app --host 0.0.0.0 --port ${PORT:-8000}"]
