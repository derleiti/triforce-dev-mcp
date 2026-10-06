ARG FLARUM_BASE_IMAGE=mondedie/flarum@sha256:adbf9c31c7c3eacea79b0265df0f53b5a278a14c15a81ca0f35c7ded2ebc4fd8
FROM ${FLARUM_BASE_IMAGE}

WORKDIR /flarum/app
COPY extensions.list /tmp/ailinux-flarum-extensions.list

RUN xargs composer require --no-interaction --no-progress < /tmp/ailinux-flarum-extensions.list \
    && composer audit --no-interaction --abandoned=report \
    && rm -f /tmp/ailinux-flarum-extensions.list
