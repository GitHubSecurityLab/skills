# Codespace for evaluating the fuzzing skills on Linux. Only a base: the
# language skills propose installing each project's toolchain on demand
# (their references/install.md).
FROM mcr.microsoft.com/devcontainers/base:ubuntu-24.04

RUN apt-get update \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      git curl ca-certificates python3 ripgrep jq unzip xxd \
 && rm -rf /var/lib/apt/lists/*
