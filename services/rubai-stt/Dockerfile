# syntax=docker/dockerfile:1

# The Uzbek speech service: whisper.cpp, a pinned model, and a small guard.
#
# Every input is pinned by content, not by name. A tag moves and a file is
# replaced; a commit and a sha256 do not. If either changes upstream, this build
# fails loudly instead of quietly shipping different weights.
#
#   whisper.cpp   v1.9.4                    MIT
#   model         azimxxm/rubaistt-v2-medium-ggml @ a8498d5  Apache-2.0
#                 └── converted from islomov/rubaistt_v2_medium  Apache-2.0
#
# The weights are baked into the image rather than fetched at boot. 514 MiB
# downloaded on every restart is a slow start, a network dependency at the
# worst moment, and a checksum nobody checks; paying it once at build time
# costs a large image and nothing else.

# --- whisper.cpp ----------------------------------------------------------- #
FROM debian:bookworm-slim AS whisper

ARG WHISPER_REF=v1.9.4

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential cmake git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src
RUN git clone --depth 1 --branch "${WHISPER_REF}" \
        https://github.com/ggml-org/whisper.cpp.git . \
    && git rev-parse HEAD > /src/WHISPER_COMMIT

# Static and without OpenMP: the runtime image then needs no libwhisper, no
# libggml and no libgomp — just the one binary that gets copied out.
#
# GGML_NATIVE=OFF matters more than it looks. ggml defaults to -march=native,
# which tunes the binary for whatever CPU compiled it — and an image is built
# on one machine and run on another. A build host with AVX-512 would produce a
# binary that dies with SIGILL on a runtime host without it, and no test run on
# the build machine would ever see it. AVX2 is named explicitly instead: every
# x86-64 server CPU since 2013 has it, and without it whisper medium is
# noticeably slower.
ARG TARGETARCH
RUN EXTRA=""; \
    if [ "${TARGETARCH:-amd64}" = "amd64" ]; then \
      EXTRA="-DGGML_AVX=ON -DGGML_AVX2=ON -DGGML_FMA=ON -DGGML_F16C=ON"; \
    fi; \
    cmake -B build \
        -DCMAKE_BUILD_TYPE=Release \
        -DBUILD_SHARED_LIBS=OFF \
        -DGGML_NATIVE=OFF \
        -DGGML_OPENMP=OFF \
        -DWHISPER_BUILD_TESTS=OFF \
        -DWHISPER_BUILD_EXAMPLES=ON \
        $EXTRA \
    && cmake --build build --config Release -j "$(nproc)" --target whisper-server \
    && strip build/bin/whisper-server


# --- the weights ----------------------------------------------------------- #
FROM debian:bookworm-slim AS model

ARG MODEL_REPO=azimxxm/rubaistt-v2-medium-ggml
ARG MODEL_COMMIT=a8498d5fd8d0b35e26a6b74521cf194c37c6a050
ARG MODEL_FILE=ggml-rubaistt-medium-q5_0.bin
ARG MODEL_SHA256=3740210b611c13c5bad257d6207dbf4572b7843043191bab39ccdf47c1734e9c

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /model
RUN curl -fsSL --retry 3 --retry-delay 5 \
        "https://huggingface.co/${MODEL_REPO}/resolve/${MODEL_COMMIT}/${MODEL_FILE}" \
        -o model.bin \
    && echo "${MODEL_SHA256}  model.bin" | sha256sum -c - \
    # A GGML file starts with the magic 0x67676d6c, written little-endian —
    # so the first four bytes read as "lmgg". An HTML error page saved under
    # the right name passes every check but this one.
    && test "$(head -c4 model.bin)" = "lmgg" \
    && echo "model verified: $(stat -c%s model.bin) bytes"


# --- runtime --------------------------------------------------------------- #
FROM python:3.11-slim AS runtime

# ffmpeg is not optional: whisper-server reads WAV only, and a browser records
# Opus in WebM. The conversion has to happen somewhere, and here it happens
# under this service's own limits rather than inside the model server.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --uid 10001 --create-home rubai

WORKDIR /app

COPY --from=whisper /src/build/bin/whisper-server /app/bin/whisper-server
COPY --from=whisper /src/WHISPER_COMMIT /app/WHISPER_COMMIT
COPY --from=model /model/model.bin /app/models/model.bin

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .

RUN chown -R rubai:rubai /app
USER rubai

ENV WHISPER_BIN=/app/bin/whisper-server \
    WHISPER_MODEL=/app/models/model.bin \
    WHISPER_PORT=8081 \
    STT_LANGUAGE=uz \
    PORT=8080

EXPOSE 8080

# uvicorn is started from app.py so the model server's lifecycle belongs to the
# process that answers requests: if the model cannot load, nothing serves.
CMD ["python", "app.py"]
