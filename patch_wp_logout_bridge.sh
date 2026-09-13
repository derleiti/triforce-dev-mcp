#!/usr/bin/env bash
set -Eeuo pipefail

cd /home/zombie/workspace/triforce

TARGET_REL="docker/wordpress/html/wp-content/plugins/nova-ai-frontend/services/AuthService.php"
TARGET_HOST="/home/zombie/workspace/triforce/${TARGET_REL}"
BACKUP_DIR="/home/zombie/workspace/triforce/patch_backups"
TARGET_CONTAINER="/var/www/html/wp-content/plugins/nova-ai-frontend/services/AuthService.php"
STAMP="$(date +%Y%m%d_%H%M%S)"
BACKUP_FILE="${BACKUP_DIR}/AuthService.php.bak.${STAMP}"
CONTAINER=""

mkdir -p "$BACKUP_DIR"

if [[ ! -f "$TARGET_HOST" ]]; then
  echo "[ERR] Target not found: $TARGET_HOST" >&2
  exit 1
fi

cp "$TARGET_HOST" "$BACKUP_FILE"
echo "[OK] Backup created: $BACKUP_FILE"

find_wp_container() {
  local names=(wordpress_fpm triforce-wordpress wordpress_apache)
  local name
  for name in "${names[@]}"; do
    if docker ps --format '{{.Names}}' | grep -Fxq "$name"; then
      echo "$name"
      return 0
    fi
  done
  docker ps --format '{{.Names}} {{.Image}}' | awk '/wordpress:.*php.*fpm|wordpress:/{print $1; exit}'
}

PHP_PATCH=$(cat <<'PHP'
$path = $argv[1];
$text = file_get_contents($path);
if ($text === false) {
    fwrite(STDERR, "[ERR] Could not read target file.\n");
    exit(1);
}

if (strpos($text, 'filter_logout_redirect') !== false && strpos($text, 'allow_login_redirect_host') !== false) {
    echo "[OK] Patch already present, nothing to do.\n";
    exit(0);
}

$old_block = <<<'EOT'
        // Login/register redirect
        add_action('login_form_login',    [$this, 'maybe_redirect_login']);
        add_action('login_form_register', [$this, 'maybe_redirect_register']);

        // REST
        add_action('rest_api_init', [$this, 'register_rest_routes']);
EOT;

$new_block = <<<'EOT'
        // Login/register redirect
        add_action('login_form_login',    [$this, 'maybe_redirect_login']);
        add_action('login_form_register', [$this, 'maybe_redirect_register']);

        // Central logout / redirect handling
        add_filter('logout_redirect', [$this, 'filter_logout_redirect'], 10, 3);
        add_filter('allowed_redirect_hosts', [$this, 'allow_login_redirect_host']);

        // REST
        add_action('rest_api_init', [$this, 'register_rest_routes']);
EOT;

$anchor = <<<'EOT'
    private static function clear_session_cookie(): void {
        $cookie_name = defined('NOVA_SESSION_COOKIE') ? NOVA_SESSION_COOKIE : 'nova_session';
        if (isset($_COOKIE[$cookie_name])) {
            setcookie($cookie_name, '', time() - 3600, '/', '', is_ssl(), true);
            unset($_COOKIE[$cookie_name]);
        }
    }
EOT;

$insert = <<<'EOT'

    public function allow_login_redirect_host(array $hosts): array {
        $host = wp_parse_url($this->login_page, PHP_URL_HOST);
        if ($host && !in_array($host, $hosts, true)) {
            $hosts[] = $host;
        }
        return $hosts;
    }

    public function filter_logout_redirect($redirect_to, $requested_redirect_to, $user): string {
        $target = $this->login_page . '?action=logout';

        if (!empty($requested_redirect_to)) {
            $requested_host = wp_parse_url($requested_redirect_to, PHP_URL_HOST);
            $home_host      = wp_parse_url(home_url(), PHP_URL_HOST);

            if ($requested_host && $home_host && strtolower($requested_host) === strtolower($home_host)) {
                $target .= '&redirect_back=' . rawurlencode($requested_redirect_to);
            }
        }

        return $target;
    }
EOT;

if (strpos($text, $old_block) === false) {
    fwrite(STDERR, "[ERR] Could not find init_hooks insertion point.\n");
    exit(1);
}

if (strpos($text, $anchor) === false) {
    fwrite(STDERR, "[ERR] Could not find clear_session_cookie() anchor.\n");
    exit(1);
}

$text = str_replace($old_block, $new_block, $text, $count1);
$text = str_replace($anchor, $anchor . $insert, $text, $count2);

if (($count1 + $count2) < 2) {
    fwrite(STDERR, "[ERR] Patch replacement count mismatch.\n");
    exit(1);
}

if (file_put_contents($path, $text) === false) {
    fwrite(STDERR, "[ERR] Could not write patched file.\n");
    exit(1);
}

echo "[OK] Patched $path\n";
PHP
)

run_direct_patch() {
  php -r "$PHP_PATCH" "$TARGET_HOST"
}

run_container_patch() {
  docker exec -i -u 0 "$CONTAINER" php -r "$PHP_PATCH" "$TARGET_CONTAINER"
}

run_direct_lint() {
  php -l "$TARGET_HOST"
}

run_container_lint() {
  docker exec "$CONTAINER" php -l "$TARGET_CONTAINER"
}

PATCH_MODE=""
if [[ -w "$TARGET_HOST" ]]; then
  echo "[INFO] Using direct host patch"
  run_direct_patch
  PATCH_MODE="host"
else
  CONTAINER="$(find_wp_container || true)"
  if [[ -z "$CONTAINER" ]]; then
    echo "[ERR] Could not find a running WordPress container." >&2
    exit 1
  fi
  echo "[INFO] Host file not writable, trying container patch via docker exec: $CONTAINER"
  run_container_patch
  PATCH_MODE="container"
fi

if [[ "$PATCH_MODE" == "host" ]]; then
  run_direct_lint
else
  run_container_lint
fi

echo "[OK] Done"
