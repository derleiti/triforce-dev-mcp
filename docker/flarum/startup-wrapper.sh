#!/usr/bin/env sh
set -eu

install -m 0644 /etc/flarum/extensions.list /flarum/app/extensions/list
exec /usr/local/bin/startup
