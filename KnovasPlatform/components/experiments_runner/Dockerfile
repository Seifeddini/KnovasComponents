# experiments-runner: the sandbox for user-written Python and Julia evaluators
# of the Experiments module (Experimente). Built and started only with the
# compose profile "experiments"; the isolation settings (no network, read-only
# root, no capabilities, limits) live in docker-compose.yml. See README.md.

FROM julia:1.11.9-bookworm

LABEL description="Knovas experiments-runner: sandbox for Python and Julia evaluators"

# Debian's Python (3.11 on bookworm) runs the server and the Python jobs.
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-venv ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# The evaluators' Python toolbox, every package pinned. Wheels only: there is
# no compiler in this image, and a source build would not be reproducible.
COPY requirements.txt /app/requirements.txt
RUN python3 -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir --only-binary=:all: -r /app/requirements.txt

# The evaluators' Julia packages, installed into the default environment of a
# depot that stays read-only at run time. Each job gets
# JULIA_DEPOT_PATH=<job dir>/depot:/opt/julia-depot: from the runner, so
# whatever Julia writes lands in the job's own directory and goes with it;
# there is deliberately no ENV JULIA_DEPOT_PATH with a shared writable entry.
# Precompiled with default flags, which the harness runs with too: other flags
# would make every job compile the packages again.
#
# JULIA_CPU_TARGET: the default compiles for this machine's CPU. The image is
# normally built where it runs (docker compose up --build). For an image that
# must run on other CPUs, build with e.g.
#   --build-arg JULIA_CPU_TARGET="generic;sandybridge,-xsaveopt,clone_all;haswell,-rdrnd,base(1)"
ARG JULIA_CPU_TARGET=native
RUN JULIA_DEPOT_PATH=/opt/julia-depot julia --startup-file=no -e 'using Pkg; Pkg.add(["JSON3", "Distributions", "HypothesisTests", "StatsBase", "DataFrames"]); Pkg.precompile()' \
    && chmod -R a+rX /opt/julia-depot

# uid 10101, used by no other image of the stack. It must stay unique on the
# host: runner.py gives every job RLIMIT_NPROC 128, and the kernel checks it
# against all processes and threads of that uid on the whole host, not of one
# container. With a shared uid (Knovas Connector runs as 10001) another
# container's load would make jobs fail to fork or start threads.
# docker-compose.yml (user, socket volume) and the CI check use the same
# number. The socket directory is a volume shared with docbridge-web (compose
# mounts a tmpfs volume owned by this uid there).
RUN groupadd --gid 10101 runner \
    && useradd --uid 10101 --gid 10101 --no-create-home --home-dir /nonexistent \
       --shell /usr/sbin/nologin runner \
    && mkdir -p /run/experiments-runner \
    && chown 10101:10101 /run/experiments-runner

COPY runner.py harness.py harness.jl /app/

USER 10101
# No docker-entrypoint.sh from the base image: the runner is PID 1 itself, so
# it reaps what jobs leave behind and receives docker stop's SIGTERM.
ENTRYPOINT []
CMD ["/opt/venv/bin/python3", "-I", "/app/runner.py"]
