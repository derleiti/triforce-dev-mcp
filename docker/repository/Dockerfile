FROM debian:stable-slim

# Enable i386 architecture for Wine/Steam
RUN dpkg --add-architecture i386 && \
    apt-get update && apt-get install -y --no-install-recommends \
        apt-mirror \
        cron \
        wget \
        curl \
        gnupg \
        apt-utils \
        dpkg-dev \
        ca-certificates \
        xz-utils \
        gzip \
        pv \
        progress \
        procps && \
    rm -rf /var/lib/apt/lists/*

# Debian's classic apt-mirror only requests compressed Packages indexes.
# Some valid APT repositories (for example Mozilla and Google Artifact Registry)
# publish only the uncompressed Packages file. Teach apt-mirror to request that
# file as well so process_index() can consume those repositories normally.
RUN sed -i '/binary-.*Packages\.gz/ i\            add_url_to_download( $url . $_ . "/binary-" . $arch . "/Packages" );' /usr/bin/apt-mirror && \
    sed -i '/\/$distribution\/Packages\.gz/ i\        add_url_to_download( $uri . "/$distribution/Packages" );' /usr/bin/apt-mirror

# Create directories
RUN mkdir -p \
    /var/spool/apt-mirror/mirror \
    /var/spool/apt-mirror/var \
    /repo \
    /usr/share/keyrings \
    /var/log/ailinux

# Copy mirror configuration
COPY mirror.list /etc/apt/mirror.list

# Copy scripts
COPY update-mirror.sh /usr/local/bin/update-mirror.sh
COPY generate-index.sh /repo/generate-index.sh
COPY fix-packages-compression.sh /usr/local/bin/fix-packages-compression.sh

RUN chmod +x \
    /usr/local/bin/update-mirror.sh \
    /repo/generate-index.sh \
    /usr/local/bin/fix-packages-compression.sh

# Cronjob for automatic updates (3 AM daily)
RUN echo "0 3 * * * /usr/local/bin/update-mirror.sh >> /var/log/ailinux/cron.log 2>&1" > /etc/cron.d/aptmirror && \
    chmod 0644 /etc/cron.d/aptmirror && \
    crontab /etc/cron.d/aptmirror

# Entrypoint script to copy keyrings at startup
RUN echo '#!/bin/bash\n\
if [ -d /keyrings ] && [ "$(ls -A /keyrings 2>/dev/null)" ]; then\n\
    cp -f /keyrings/*.gpg /usr/share/keyrings/ 2>/dev/null || true\n\
    chmod 644 /usr/share/keyrings/*.gpg 2>/dev/null || true\n\
    echo "[apt-mirror] Loaded $(ls /usr/share/keyrings/*.gpg 2>/dev/null | wc -l) GPG keys"\n\
fi\n\
exec "$@"' > /entrypoint.sh && chmod +x /entrypoint.sh

EXPOSE 80

ENTRYPOINT ["/entrypoint.sh"]
CMD ["cron", "-f"]
