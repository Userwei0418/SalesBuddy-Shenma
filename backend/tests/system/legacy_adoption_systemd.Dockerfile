ARG BASE_IMAGE=salesbuddy-local-ci:ubuntu24-python312-node22-pg16
FROM ${BASE_IMAGE}
ENV DEBIAN_FRONTEND=noninteractive container=docker
RUN apt-get update -qq \
    && apt-get install -y --no-install-recommends systemd systemd-sysv dbus \
    && rm -rf /var/lib/apt/lists/* \
    && systemctl mask systemd-remount-fs.service getty.target console-getty.service \
       systemd-logind.service systemd-udevd.service postgresql.service \
    && systemctl set-default multi-user.target
STOPSIGNAL SIGRTMIN+3
ENTRYPOINT ["/sbin/init"]
