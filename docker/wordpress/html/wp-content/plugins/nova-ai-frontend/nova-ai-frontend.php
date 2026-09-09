<?php
/**
 * Plugin Name: Nova AI Frontend
 * Description: AILinux AI Playground & Downloads — Chat, Vision, Media Generation + Admin Dashboard
 * Version: 6.6.0
 * Author: zombie@ailinux
 * Text Domain: nova-ai-frontend
 */

defined('ABSPATH') || exit;

// FIX 2026-04-11: Credentials aus wp-config.php laden (Fallbacks für Abwärtskompatibilität)
if ( ! defined( 'NOVA_AI_BACKEND' ) )       define('NOVA_AI_BACKEND',      'http://172.18.0.1:9000');
if ( ! defined( 'NOVA_AI_LOCAL_BACKEND' ) )  define('NOVA_AI_LOCAL_BACKEND', 'http://localhost:9000');
if ( ! defined( 'NOVA_AI_INTERNAL_KEY' ) )   define('NOVA_AI_INTERNAL_KEY',  getenv('NOVA_AI_INTERNAL_KEY') ?: '');
define('NOVA_AI_VERSION', '6.6.0');
define('NOVA_AI_PLUGIN_URL',  plugin_dir_url(__FILE__));
define('NOVA_AI_PLUGIN_DIR',  plugin_dir_path(__FILE__));

// Lemon Squeezy runtime config lives in the versioned bootstrap so fresh
// deployments do not depend on a locally generated, gitignored config file.
if (!function_exists('nova_env_bool')) {
    function nova_env_bool(string $key, bool $default = false): bool {
        $value = getenv($key);
        if ($value === false || $value === '') {
            return $default;
        }
        return filter_var($value, FILTER_VALIDATE_BOOLEAN, FILTER_NULL_ON_FAILURE) ?? $default;
    }
}
if (!defined('NOVA_LS_API_KEY'))               define('NOVA_LS_API_KEY', getenv('NOVA_LS_API_KEY') ?: getenv('LEMONSQUEEZY_API_KEY') ?: '');
if (!defined('NOVA_LS_STORE_ID'))              define('NOVA_LS_STORE_ID', getenv('NOVA_LS_STORE_ID') ?: '');
if (!defined('NOVA_LS_VARIANT_ID'))            define('NOVA_LS_VARIANT_ID', getenv('NOVA_LS_VARIANT_ID') ?: '');
if (!defined('NOVA_LS_WEBHOOK_SECRET'))        define('NOVA_LS_WEBHOOK_SECRET', getenv('NOVA_LS_WEBHOOK_SECRET') ?: getenv('LEMONSQUEEZY_WEBHOOK_SECRET') ?: '');
if (!defined('NOVA_LS_TEST_MODE'))             define('NOVA_LS_TEST_MODE', nova_env_bool('NOVA_LS_TEST_MODE', true));
if (!defined('NOVA_LS_ALLOW_TEST_CHECKOUT'))   define('NOVA_LS_ALLOW_TEST_CHECKOUT', nova_env_bool('NOVA_LS_ALLOW_TEST_CHECKOUT', false));
if (!defined('NOVA_LS_WEBHOOK_DIRECT'))        define('NOVA_LS_WEBHOOK_DIRECT', nova_env_bool('NOVA_LS_WEBHOOK_DIRECT', true));
if (!defined('NOVA_PAYMENT_PROVIDER'))         define('NOVA_PAYMENT_PROVIDER', getenv('NOVA_PAYMENT_PROVIDER') ?: 'lemonsqueezy');
if (!defined('NOVA_LS_PRODUCT_ENTITLEMENTS'))  define('NOVA_LS_PRODUCT_ENTITLEMENTS', getenv('NOVA_LS_PRODUCT_ENTITLEMENTS') ?: getenv('LEMONSQUEEZY_PRODUCT_ENTITLEMENTS') ?: '');

// 2026-04-24: PSR-4 Autoloader + Plugin bootstrap
require_once NOVA_AI_PLUGIN_DIR . 'includes/autoloader.php';
if (is_file(NOVA_AI_PLUGIN_DIR . 'config/lemonsqueezy.php')) { require_once NOVA_AI_PLUGIN_DIR . 'config/lemonsqueezy.php'; }
// 2026-06-01: Minimal shop bootstrap fix.
// Restores [ailinux_shop] and shop checkout REST/AJAX hooks without starting the full Core\Plugin stack.
if (class_exists('\\NovAI\\Core\\ShopShortcode')) {
    \NovAI\Core\ShopShortcode::register();
}

add_action('init', function () {
    if (!shortcode_exists('ailinux_shop')) {
        add_shortcode('ailinux_shop', function ($atts) {
            $atts = shortcode_atts([
                'layout'    => 'grid',
                'highlight' => '',
                'columns'   => 3,
            ], $atts, 'ailinux_shop');

            ob_start();
            include NOVA_AI_PLUGIN_DIR . 'templates/shop.php';
            return ob_get_clean();
        });
    }
});

function nova_get_docker_gateway_ip(): string {
    $routes = @file('/proc/net/route');
    if (!$routes) {
        return '';
    }

    foreach ($routes as $index => $line) {
        if ($index === 0) {
            continue;
        }

        $parts = preg_split('/\s+/', trim($line));
        if (count($parts) < 3 || $parts[1] !== '00000000') {
            continue;
        }

        $hex = $parts[2];
        if (strlen($hex) !== 8) {
            continue;
        }

        $bytes = array_map('hexdec', str_split($hex, 2));
        return implode('.', array_reverse($bytes));
    }

    return '';
}

function nova_is_local_backend_host(string $host): bool {
    return in_array($host, ['localhost', '127.0.0.1', '172.18.0.1', 'host.docker.internal'], true);
}

function nova_normalize_backend_url(?string $url, ?string $default = null): string {
    $url = trim((string)($url ?? ''));
    if ($url === '') {
        $url = trim((string)($default ?? ''));
    }
    if ($url === '') {
        return '';
    }

    $parsed = wp_parse_url($url);
    if (!$parsed || empty($parsed['host'])) {
        return $url;
    }

    $scheme = $parsed['scheme'] ?? 'http';
    $host = $parsed['host'];
    $port = isset($parsed['port']) ? (int)$parsed['port'] : null;
    $path = isset($parsed['path']) ? rtrim($parsed['path'], '/') : '';
    $query = isset($parsed['query']) ? '?' . $parsed['query'] : '';
    $fragment = isset($parsed['fragment']) ? '#' . $parsed['fragment'] : '';

    if ($scheme === 'https' && nova_is_local_backend_host($host)) {
        $scheme = 'http';
    }

    if (file_exists('/.dockerenv')) {
        if (in_array($host, ['localhost', '127.0.0.1'], true)) {
            $resolved = gethostbyname('host.docker.internal');
            if ($resolved && $resolved !== 'host.docker.internal') {
                $host = $resolved;
            } else {
                $gateway = nova_get_docker_gateway_ip();
                if ($gateway) {
                    $host = $gateway;
                }
            }
        } elseif ($host === 'host.docker.internal') {
            $resolved = gethostbyname($host);
            if ($resolved && $resolved !== $host) {
                $host = $resolved;
            } else {
                $gateway = nova_get_docker_gateway_ip();
                if ($gateway) {
                    $host = $gateway;
                }
            }
        }
    }

    $port_suffix = $port !== null ? ':' . $port : '';
    return $scheme . '://' . $host . $port_suffix . $path . $query . $fragment;
}

function nova_get_backend_setting(string $key, string $fallback = ''): string {
    if (function_exists('ailinux_triforce_setting')) {
        $shared = ailinux_triforce_setting($key, '');
        if ($shared !== '') { return nova_normalize_backend_url($shared, $fallback); }
    }
    $settings = get_option('nova_ai_settings', []);
    $value = $settings[$key] ?? '';
    return nova_normalize_backend_url($value, $fallback);
}

function nova_get_backend_base(): string {
    if (function_exists('ailinux_triforce_api_base')) {
        return nova_normalize_backend_url(ailinux_triforce_api_base(true), NOVA_AI_BACKEND);
    }
    $settings = get_option('nova_ai_settings', []);
    $internal = $settings['api_endpoint_internal'] ?? '';
    $primary = $settings['api_endpoint'] ?? '';
    return nova_normalize_backend_url($internal ?: $primary, NOVA_AI_BACKEND);
}

function nova_get_display_backend_base(): string {
    return nova_get_backend_setting('api_endpoint', NOVA_AI_LOCAL_BACKEND);
}

function nova_get_mcp_base(): string {
    if (function_exists('ailinux_triforce_mcp_base')) {
        return nova_normalize_backend_url(ailinux_triforce_mcp_base(), NOVA_AI_LOCAL_BACKEND);
    }
    $settings = get_option('nova_ai_settings', []);
    $mcp = $settings['mcp_endpoint'] ?? '';
    return nova_normalize_backend_url($mcp, NOVA_AI_LOCAL_BACKEND);
}

function nova_maybe_upgrade_legacy_backend_settings(): void {
    $settings = get_option('nova_ai_settings', []);
    if (!is_array($settings) || !$settings) {
        return;
    }

    $updated = $settings;
    foreach (['api_endpoint' => NOVA_AI_LOCAL_BACKEND, 'api_endpoint_internal' => NOVA_AI_BACKEND, 'mcp_endpoint' => NOVA_AI_LOCAL_BACKEND] as $key => $fallback) {
        if (!array_key_exists($key, $updated)) {
            continue;
        }
        $normalized = nova_normalize_backend_url($updated[$key], $fallback);
        if ($normalized !== '' && $normalized !== $updated[$key]) {
            $updated[$key] = $normalized;
        }
    }

    if ($updated !== $settings) {
        update_option('nova_ai_settings', $updated);
    }
}

// Account und Shop nie cachen — beide enthalten Loginstatus und REST-Nonce.
add_action('send_headers', function() {
    $request_uri = $_SERVER['REQUEST_URI'] ?? '';
    $dynamic_page = is_page('account') || is_page('ailinux-shop')
        || strpos($request_uri, '/account') !== false
        || strpos($request_uri, '/ailinux-shop') !== false;

    if ($dynamic_page) {
        header('Cache-Control: no-store, no-cache, must-revalidate, max-age=0');
        header('Pragma: no-cache');
        header('Surrogate-Control: no-store');
        if (!defined('DONOTCACHEPAGE')) {
            define('DONOTCACHEPAGE', true);
        }
    }
}, 1);

/**
 * Persist the shop exclusion in Super Page Cache without replacing existing rules.
 * The version option keeps this cross-plugin compatibility write strictly one-shot.
 */
function nova_ensure_shop_page_cache_exclusion(): void {
    if (get_option('nova_shop_cache_policy_version') === '1') {
        return;
    }
    if (!class_exists('\SPC\Services\Settings_Store') || !class_exists('\SPC\Constants')) {
        return;
    }

    try {
        $store = \SPC\Services\Settings_Store::get_instance();
        $urls = array_values(array_filter(array_map('trim', (array) $store->get(\SPC\Constants::SETTING_EXCLUDED_URLS, []))));
        foreach (['/ailinux-shop/', '/ailinux-shop*'] as $pattern) {
            if (!in_array($pattern, $urls, true)) {
                $urls[] = $pattern;
            }
        }
        $store->set(\SPC\Constants::SETTING_EXCLUDED_URLS, $urls)->save();

        if (class_exists('\SPC\Loader')) {
            $fallback = \SPC\Loader::get()->fallback_cache();
            $fallback->fallback_cache_save_config();
            $fallback->fallback_cache_purge_urls([home_url('/ailinux-shop/'), home_url('/ailinux-shop')]);
        }
        update_option('nova_shop_cache_policy_version', '1', false);
    } catch (\Throwable $error) {
        error_log('[nova] shop cache exclusion failed: ' . $error->getMessage());
    }
}

add_action('plugins_loaded', 'nova_maybe_upgrade_legacy_backend_settings', 1);
add_action('admin_init', 'nova_ensure_shop_page_cache_exclusion', 50);

/* ── FIX: Cloudflare Rocket Loader / WP Page Cache – exclude nova-ai.js ──── */
add_filter('script_loader_tag', function (string $tag, string $handle): string {
    if ($handle === 'nova-ai-frontend') {
        // Rocket Loader: data-cfasync=false prevents deferred execution in wrong scope
        $tag = str_replace(' src=', ' data-cfasync="false" src=', $tag);
    }
    return $tag;
}, 10, 2);

/* ── REST API Proxy ─────────────────────────────────────────────────────────── */
add_action('rest_api_init', function () {
    // Bypass WP cookie auth for nova-ai endpoints.
    // rest_cookie_check_errors fires even with permission_callback=__return_true
    // and rejects requests when browser has a WP cookie but no valid nonce.
    // Fix: WP's rest_cookie_check_errors runs at priority 100.
    // It fires after our priority-5 filter and still returns 403 for logged-in
    // users without a valid nonce. We override it AFTER at priority 200.
    add_filter('rest_authentication_errors', function($result) {
        // Multi-source check: original URI, rewritten path, rest_route param
        $uri   = isset($_SERVER['REQUEST_URI'])   ? $_SERVER['REQUEST_URI']   : '';
        $qs    = isset($_SERVER['QUERY_STRING'])  ? $_SERVER['QUERY_STRING']  : '';
        $route = isset($_SERVER['PATH_INFO'])     ? $_SERVER['PATH_INFO']     : '';
        $rr    = isset($_GET['rest_route'])       ? $_GET['rest_route']       : '';
        // FIX 2026-04-11: Präziserer Check — nur /wp-json/nova-ai/v1/ Namespace
        $is_nova = (
            preg_match('#/wp-json/nova-ai/v1/#', $uri) ||
            (strpos($rr, '/nova-ai/v1/') === 0) ||
            (strpos($route, '/nova-ai/v1/') === 0)
        );
        if ($is_nova) {
            error_log('[nova-ai] auth bypass fired, uri=' . $uri . ' result_type=' . (is_wp_error($result) ? 'WP_Error:' . $result->get_error_code() : gettype($result)));
            return null;
        }
        return $result;
    }, 200); // priority 200 = AFTER WP's cookie check at priority 100
    $ns = 'nova-ai/v1';
    // Frontend routes
    register_rest_route($ns, '/health',  ['methods'=>'GET',  'callback'=>'nova_proxy_health',  'permission_callback'=>'__return_true']);
    register_rest_route($ns, '/models',  ['methods'=>'GET',  'callback'=>'nova_proxy_models',  'permission_callback'=>'__return_true']);
    register_rest_route($ns, '/chat',    ['methods'=>'POST', 'callback'=>'nova_proxy_chat',    'permission_callback'=>'__return_true']);
    register_rest_route($ns, '/vision',  ['methods'=>'POST', 'callback'=>'nova_proxy_vision',  'permission_callback'=>'__return_true']);
    register_rest_route($ns, '/vision-upload', ['methods'=>'POST', 'callback'=>'nova_proxy_vision_upload', 'permission_callback'=>'__return_true']);
    register_rest_route($ns, '/media/image', ['methods'=>'POST','callback'=>'nova_proxy_image','permission_callback'=>'__return_true']);
    register_rest_route($ns, '/media/video', ['methods'=>'POST','callback'=>'nova_proxy_video','permission_callback'=>'__return_true']);
    register_rest_route($ns, '/media/video/status/(?P<job_id>[a-zA-Z0-9_\-]+)', ['methods'=>'GET','callback'=>'nova_proxy_video_status','permission_callback'=>'__return_true']);
    register_rest_route($ns, '/nonce',   ['methods'=>'GET', 'permission_callback'=>'__return_true', 'callback'=>function(){
        // Always return a nonce - guests get anonymous nonce (user_id=0), logged-in users get personal nonce
        return new WP_REST_Response(['ok'=>true,'nonce'=>wp_create_nonce('wp_rest'),'guest'=>!is_user_logged_in()],200,
            ['Cache-Control'=>'no-store,no-cache,max-age=0','Pragma'=>'no-cache']);
    }]);
    register_rest_route($ns, '/article-chat', ['methods'=>'POST', 'callback'=>'nova_proxy_article_chat', 'permission_callback'=>'__return_true']);
    register_rest_route($ns, '/auth/status', ['methods'=>'GET', 'callback'=>'nova_auth_status', 'permission_callback'=>'__return_true']);
    register_rest_route($ns, '/account', ['methods'=>'GET', 'callback'=>'nova_proxy_account', 'permission_callback'=>'__return_true']);
    // Auth routes delegated to AuthService.php (register_rest_routes)
    // NOTE: /subscription, /subscription/cancel, /purchases delegated to AccountSuiteService (avoids duplicate registration)

    // Admin routes – require manage_options capability
    $admin_perm = function() { return current_user_can('manage_options'); };
    register_rest_route($ns, '/admin/status',       ['methods'=>'GET',  'callback'=>'nova_admin_status',       'permission_callback'=>$admin_perm]);
    register_rest_route($ns, '/admin/logs',         ['methods'=>'GET',  'callback'=>'nova_admin_logs',         'permission_callback'=>$admin_perm]);
    register_rest_route($ns, '/admin/agents',       ['methods'=>'GET',  'callback'=>'nova_admin_agents',       'permission_callback'=>$admin_perm]);
    register_rest_route($ns, '/admin/agents/(?P<agent_id>[a-z0-9_\-]+)/(?P<action>start|stop|restart)', ['methods'=>'POST','callback'=>'nova_admin_agent_action','permission_callback'=>$admin_perm]);
    register_rest_route($ns, '/admin/mcp/tools',    ['methods'=>'GET',  'callback'=>'nova_admin_mcp_tools',    'permission_callback'=>$admin_perm]);
    register_rest_route($ns, '/admin/mcp/call',     ['methods'=>'POST', 'callback'=>'nova_admin_mcp_call',     'permission_callback'=>$admin_perm]);
    register_rest_route($ns, '/admin/crawler',      ['methods'=>'GET',  'callback'=>'nova_admin_crawler_get',  'permission_callback'=>$admin_perm]);
    register_rest_route($ns, '/admin/crawler',      ['methods'=>'POST', 'callback'=>'nova_admin_crawler_set',  'permission_callback'=>$admin_perm]);
    register_rest_route($ns, '/admin/vault/keys',   ['methods'=>'GET',  'callback'=>'nova_admin_vault_keys',   'permission_callback'=>$admin_perm]);
    register_rest_route($ns, '/admin/vault/set',    ['methods'=>'POST', 'callback'=>'nova_admin_vault_set',    'permission_callback'=>$admin_perm]);
    register_rest_route($ns, '/admin/settings',     ['methods'=>'GET',  'callback'=>'nova_admin_settings_get', 'permission_callback'=>$admin_perm]);
    register_rest_route($ns, '/admin/settings',     ['methods'=>'POST', 'callback'=>'nova_admin_settings_set', 'permission_callback'=>$admin_perm]);
    register_rest_route($ns, '/admin/restart',      ['methods'=>'POST', 'callback'=>'nova_admin_restart',      'permission_callback'=>$admin_perm]);
    register_rest_route($ns, '/admin/bootstrap',    ['methods'=>'POST', 'callback'=>'nova_admin_bootstrap',    'permission_callback'=>$admin_perm]);
});

/* ── Core proxy helper ──────────────────────────────────────────────────────── */
function nova_proxy(string $path, string $method='GET', ?array $body=null): WP_REST_Response {
    $args = ['method'=>$method,'timeout'=>90,'headers'=>['Content-Type'=>'application/json','X-Internal-Key'=>NOVA_AI_INTERNAL_KEY]];
    if ($body !== null) $args['body'] = wp_json_encode($body);
    $response = wp_remote_request(nova_get_backend_base().$path, $args);
    if (is_wp_error($response)) return new WP_REST_Response(['error'=>$response->get_error_message()], 502);
    $code = wp_remote_retrieve_response_code($response);
    $data = json_decode(wp_remote_retrieve_body($response), true) ?? ['raw'=>wp_remote_retrieve_body($response)];
    if ($code >= 400 && !isset($data['message'])) $data['message'] = $data['detail'] ?? $data['error'] ?? "Backend HTTP $code";
    return new WP_REST_Response($data, $code);
}

/* ── Frontend proxy callbacks ───────────────────────────────────────────────── */
function nova_proxy_auth(string $path, string $method='GET', ?array $body=null): WP_REST_Response {
    $base = nova_get_backend_base();
    $url  = rtrim($base, '/') . $path;
    $args = ['method'=>$method, 'timeout'=>15, 'headers'=>[
        'Content-Type'=>'application/json',
        // FIX 2026-04-11: Credentials aus wp-config.php / Environment
            'Authorization'=>'Basic '.base64_encode(
                (function_exists('ailinux_triforce_setting') ? ailinux_triforce_setting('mcp_user', '') : (defined('NOVA_MCP_USER') ? NOVA_MCP_USER : (getenv('MCP_OAUTH_USER') ?: '')))
                .':'.
                (function_exists('ailinux_triforce_setting') ? ailinux_triforce_setting('mcp_pass', '') : (defined('NOVA_MCP_PASS') ? NOVA_MCP_PASS : (getenv('MCP_OAUTH_PASS') ?: '')))
            ),
    ]];
    if ($body !== null) $args['body'] = json_encode($body);
    $resp = wp_remote_request($url, $args);
    if (is_wp_error($resp)) return new WP_REST_Response(['error'=>$resp->get_error_message()], 502);
    $code = wp_remote_retrieve_response_code($resp);
    $data = json_decode(wp_remote_retrieve_body($resp), true) ?? [];
    return new WP_REST_Response($data, $code);
}

function nova_proxy_health(WP_REST_Request $r): WP_REST_Response  { return nova_proxy('/health'); }
function nova_proxy_models(WP_REST_Request $r): WP_REST_Response {
    $settings     = get_option('nova_ai_settings', []);
    $endpoint     = $settings['api_endpoint'] ?? 'https://api.ailinux.me';
    $internal_key = $settings['internal_key']  ?? '';

    // FIX 2026-04-24: /v1/frontend/dashboard/models entfernt — nutze /v1/models/all.
    // Response-Shape: {data: [...], total: N} statt {models: [...]} — wird unten normalisiert.
    $resp = wp_remote_get($endpoint . '/v1/models/all', [
        'timeout' => 15,
        'headers' => [
            'Accept'          => 'application/json',
            'X-Internal-Key'  => $internal_key,
        ],
    ]);

    if (is_wp_error($resp) || wp_remote_retrieve_response_code($resp) !== 200) {
        // Fallback: client/models ohne Auth (57 Free-Modelle)
        $resp2 = wp_remote_get($endpoint . '/v1/client/models', ['timeout' => 10]);
        if (!is_wp_error($resp2)) {
            $body2 = json_decode(wp_remote_retrieve_body($resp2), true);
            $raw   = $body2['models'] ?? [];
            $models = [];
            foreach ($raw as $m) {
                if (is_string($m)) {
                    $p = explode('/', $m, 2);
                    $models[] = ['id'=>$m,'name'=>count($p)>1?$p[1]:$m,'provider'=>count($p)>1?$p[0]:'other','chat'=>true];
                } else { $models[] = $m; }
            }
            return new WP_REST_Response(['models' => $models, 'tier' => 'free', 'fallback' => true], 200);
        }
        return new WP_REST_Response(['models' => []], 502);
    }

    $body = json_decode(wp_remote_retrieve_body($resp), true);
    // FIX 2026-04-24: /v1/models/all liefert {data: [...], total: N} — mappen auf {models, count}
    $raw = $body['data'] ?? ($body['models'] ?? []);
    $models = [];
    foreach ($raw as $m) {
        if (is_string($m)) {
            $p = explode('/', $m, 2);
            $models[] = ['id'=>$m, 'name'=>count($p)>1?$p[1]:$m, 'provider'=>count($p)>1?$p[0]:'other', 'chat'=>true];
            continue;
        }
        $id       = $m['id'] ?? '';
        $parts    = explode('/', $id, 2);
        $caps     = $m['capabilities'] ?? [];
        $entry = [
            'id'       => $id,
            'name'     => $m['name'] ?? (count($parts)>1 ? $parts[1] : $id),
            'provider' => $m['provider'] ?? (count($parts)>1 ? $parts[0] : 'other'),
            'chat'     => in_array('chat', $caps, true) || ($m['chat'] ?? false) === true,
            'vision'   => in_array('vision', $caps, true) || in_array('multimodal', $caps, true),
            'media_image'  => in_array('image_gen', $caps, true),
            'media_video'  => in_array('video_gen', $caps, true),
            'audio'        => in_array('audio', $caps, true),
            'ocr'          => in_array('ocr', $caps, true),
            'embedding'    => in_array('embedding', $caps, true),
            'code'         => in_array('code', $caps, true),
            'reasoning'    => in_array('reasoning', $caps, true),
            'image_gen'    => in_array('image_gen', $caps, true),
            'video_gen'    => in_array('video_gen', $caps, true),
            'capabilities' => $caps,
        ];
        $entry['categories'] = [];
        foreach (['chat','vision','media_image','media_video','audio','ocr','embedding','code','reasoning'] as $cat) {
            if (!empty($entry[$cat])) {
                $entry['categories'][] = $cat;
            }
        }
        $models[] = $entry;
    }
    return new WP_REST_Response(['models' => $models, 'count' => count($models)], 200);
}
function nova_proxy_chat(WP_REST_Request $r): WP_REST_Response    { return nova_proxy('/v1/chat',  'POST', $r->get_json_params()); }
function nova_proxy_vision(WP_REST_Request $r): WP_REST_Response  {
    $params = $r->get_json_params();
    // Normalize: JS might send 'query' or 'prompt'
    if (!empty($params['query']) && empty($params['prompt'])) {
        $params['prompt'] = $params['query'];
    }
    return nova_proxy('/v1/images/analyze','POST', $params);
}

function nova_proxy_vision_upload(WP_REST_Request $r): WP_REST_Response {
    // Forward multipart directly to backend /v1/images/analyze/upload —
    // no base64 detour. Datei wird nur fuer die Dauer der Anfrage im
    // Speicher gehalten, keine dauerhafte Speicherung.
    $files = $r->get_file_params();
    $body  = $r->get_body_params();
    $model  = sanitize_text_field($body['model']  ?? '');
    $prompt = sanitize_textarea_field($body['prompt'] ?? 'Describe this image in detail.');

    if (empty($files['image_file']) || empty($files['image_file']['tmp_name'])) {
        return new WP_REST_Response(['ok'=>false,'error'=>'No file received'], 400);
    }
    $tmp_path = $files['image_file']['tmp_name'];
    $orig_name = $files['image_file']['name'] ?: 'upload.jpg';

    // Use finfo magic-bytes; browser-declared MIME can be wrong
    $finfo_obj = finfo_open(FILEINFO_MIME_TYPE);
    $detected_mime = finfo_file($finfo_obj, $tmp_path);
    finfo_close($finfo_obj);
    $mime = ($detected_mime && strpos($detected_mime, 'image/') === 0)
            ? $detected_mime
            : ($files['image_file']['type'] ?: 'image/jpeg');

    $raw_bytes = file_get_contents($tmp_path);
    if ($raw_bytes === false) {
        return new WP_REST_Response(['ok'=>false,'error'=>'The file could not be read'], 500);
    }

    // Build multipart body manually (wp_remote_request supports raw body
    // with Content-Type: multipart/form-data; boundary=...)
    $boundary = 'NovaVisionBoundary' . wp_generate_uuid4();
    $crlf = "\r\n";
    $multipart = '';
    foreach ([['model', $model], ['prompt', $prompt]] as $pair) {
        $multipart .= '--' . $boundary . $crlf;
        $multipart .= 'Content-Disposition: form-data; name="' . $pair[0] . '"' . $crlf . $crlf;
        $multipart .= $pair[1] . $crlf;
    }
    $multipart .= '--' . $boundary . $crlf;
    $multipart .= 'Content-Disposition: form-data; name="image_file"; filename="' . $orig_name . '"' . $crlf;
    $multipart .= 'Content-Type: ' . $mime . $crlf . $crlf;
    $multipart .= $raw_bytes . $crlf;
    $multipart .= '--' . $boundary . '--' . $crlf;

    $url = nova_get_backend_base() . '/v1/images/analyze/upload';
    $resp = wp_remote_request($url, [
        'method'  => 'POST',
        'timeout' => 120,
        'headers' => [
            'Content-Type'    => 'multipart/form-data; boundary=' . $boundary,
            'X-Internal-Key'  => NOVA_AI_INTERNAL_KEY,
        ],
        'body' => $multipart,
    ]);
    if (is_wp_error($resp)) {
        return new WP_REST_Response(['ok'=>false,'error'=>$resp->get_error_message()], 502);
    }
    $code = wp_remote_retrieve_response_code($resp);
    $data = json_decode(wp_remote_retrieve_body($resp), true) ?? ['raw'=>wp_remote_retrieve_body($resp)];
    if ($code >= 400 && !isset($data['message'])) {
        $data['message'] = $data['detail'] ?? $data['error'] ?? "Backend HTTP $code";
    }
    // Wrap text -> {ok:true, mode:'vision', raw:{...}} for frontend compat
    if ($code < 400 && isset($data['text'])) {
        $data = ['ok' => true, 'mode' => 'vision', 'raw' => $data];
    }
    return new WP_REST_Response($data, $code);
}
function nova_proxy_account(WP_REST_Request $r): WP_REST_Response {
    // Returns account info: WP login status + tier/subscription from user_meta
    $user = wp_get_current_user();
    if (!$user || !$user->ID) {
        return new WP_REST_Response([
            'ok'         => true,
            'logged_in'  => false,
            'login_url'  => 'https://ailinux.me/account',
            'register_url' => 'https://ailinux.me/account',
        ], 200);
    }
    $raw_tier = get_user_meta($user->ID, 'nova_tier', true) ?: 'free';
    $tier     = nova_normalize_tier($raw_tier);
    $email    = $user->user_email;
    $sub_id   = get_user_meta($user->ID, 'nova_payment_subscription_id', true) ?: '';
    $entitls  = (array)(get_user_meta($user->ID, 'nova_entitlements', true) ?: []);
    $client_id = get_user_meta($user->ID, 'nova_client_id', true) ?: '';

    // Get available downloads from backend
    $downloads = [];
    $dl_resp = nova_proxy('/health', 'GET');
    if ($dl_resp instanceof WP_REST_Response) {
        $dl_data = $dl_resp->get_data();
        $downloads = $dl_data['files'] ?? [];
    }

    return new WP_REST_Response([
        'ok'          => true,
        'logged_in'   => true,
        'email'       => $email,
        'display_name'=> $user->display_name,
        'tier'        => $tier,
        'subscription_id' => $sub_id,
        'entitlements'=> $entitls,
        'client_id'   => $client_id,
        'downloads'   => $downloads,
        'account_url' => 'https://ailinux.me/account',
        'shop_url'    => 'https://ailinux.me/shop',
    ], 200);
}

/* ── Auth callbacks (login.ailinux.me sync) ─────────────────────────────────── */
function nova_auth_status(): WP_REST_Response {
    $user = wp_get_current_user();
    if (!$user || !$user->ID) {
        return new WP_REST_Response(['wp_logged_in'=>false,'login_url'=>'https://ailinux.me/account'], 200);
    }
    return new WP_REST_Response([
        'wp_logged_in' => true,
        'user'         => [
            'id'        => $user->ID,
            'email'     => $user->user_email,
            'name'      => $user->display_name,
            'tier'      => get_user_meta($user->ID, 'nova_tier', true) ?: 'free',
            'client_id' => get_user_meta($user->ID, 'nova_client_id', true) ?: '',
        ],
    ], 200);
}

function nova_auth_sync(WP_REST_Request $r): WP_REST_Response {
    $email     = sanitize_email($r->get_param('email'));
    $token     = sanitize_text_field($r->get_param('token'));
    $tier      = sanitize_text_field($r->get_param('tier') ?? 'free');
    $client_id = sanitize_text_field($r->get_param('client_id') ?? '');
    $name      = sanitize_text_field($r->get_param('name') ?? '');

    if (!$email || !$token) {
        return new WP_REST_Response(['success'=>false,'error'=>'email and token required'], 400);
    }

    // Verify token via backend
    $base = nova_get_backend_base();
    $vr = wp_remote_get(rtrim($base, '/') . '/v1/auth/verify', [
        'headers' => ['Authorization' => 'Bearer ' . $token],
        'timeout' => 8,
    ]);
    if (is_wp_error($vr) || wp_remote_retrieve_response_code($vr) < 200 || wp_remote_retrieve_response_code($vr) >= 300) {
        // Soft: allow sync even if verify fails (offline mode)
        error_log('[nova-ai] auth/sync: token verify failed — soft allow');
    } else {
        $vd = json_decode(wp_remote_retrieve_body($vr), true);
        if (!empty($vd['tier']))      $tier      = sanitize_text_field($vd['tier']);
        if (!empty($vd['client_id'])) $client_id = sanitize_text_field($vd['client_id']);
        if (!empty($vd['name']))      $name      = sanitize_text_field($vd['name']);
    }

    // Find or create WP user
    $user_id = email_exists($email);
    if (!$user_id) {
        $base_name = sanitize_user(strstr($email, '@', true)) ?: 'nova';
        $uname = $base_name; $i = 1;
        while (username_exists($uname)) $uname = $base_name . $i++;
        $user_id = wp_create_user($uname, wp_generate_password(), $email);
        if (is_wp_error($user_id)) return new WP_REST_Response(['success'=>false,'error'=>$user_id->get_error_message()], 500);
        if ($name) wp_update_user(['ID'=>$user_id, 'display_name'=>$name]);
    }

    // Normalize tier
    $tier_map = ['pro'=>'paid','unlimited'=>'paid','premium'=>'paid','enterprise'=>'enterprise','admin'=>'enterprise'];
    $tier_n   = $tier_map[strtolower($tier)] ?? ($tier === 'free' ? 'free' : 'paid');

    update_user_meta($user_id, 'nova_tier', $tier_n);
    update_user_meta($user_id, 'nova_session_token', $token);
    if ($client_id) update_user_meta($user_id, 'nova_client_id', $client_id);
    update_user_meta($user_id, 'nova_ailinux_email', $email);

    // Log user in + set auth cookie
    wp_set_current_user($user_id);
    wp_set_auth_cookie($user_id, true);

    $can_admin = current_user_can('manage_options');
    return new WP_REST_Response(['success'=>true,'can_admin'=>$can_admin,'tier'=>$tier_n,'user_id'=>$user_id], 200);
}

function nova_auth_logout(): WP_REST_Response {
    // FIX 2026-03-11 v2: The rest_authentication_errors bypass (priority 200) also skips
    // cookie auth for /auth/logout, so is_user_logged_in() can return false here even when
    // the user has a valid WP auth cookie. We manually parse + set the user from the cookie
    // so wp_logout() properly destroys the session token in the DB.
    if ( ! is_user_logged_in() ) {
        $uid = wp_validate_auth_cookie( '', 'auth' );
        if ( $uid ) {
            wp_set_current_user( $uid );
        }
    }
    if ( is_user_logged_in() ) {
        $uid = get_current_user_id();
        delete_user_meta( $uid, 'nova_session_token' );
        delete_user_meta( $uid, 'nova_wp_last_sync' );
    }
    // Clear nova_session cookie
    if ( isset( $_COOKIE['nova_session'] ) ) {
        setcookie( 'nova_session', '', time() - 3600, '/', '', is_ssl(), true );
    }
    // Destroy WP session + clear auth cookie
    wp_logout();
    return new WP_REST_Response( ['success' => true, 'redirect' => home_url()], 200 );
}

function nova_proxy_image(WP_REST_Request $r): WP_REST_Response   { return nova_proxy('/v1/images/generate','POST',$r->get_json_params()); }
function nova_proxy_video(WP_REST_Request $r): WP_REST_Response   {
    // FIX 2026-06-04: Video re-enabled. Backend /v1/frontend/dashboard/media/video via nova_proxy (X-Internal-Key).
    // Provider-Wiring: aktuell nur OpenAI Sora; Veo blockt auf Free-Plan; Replicate/OpenRouter/CF nicht wired.
    return nova_proxy('/v1/frontend/dashboard/media/video','POST',$r->get_json_params());
}
function nova_proxy_video_status(WP_REST_Request $r): WP_REST_Response {
    // FIX 2026-06-04: Synchroner Endpoint, kein Polling noetig — 410 Gone.
    return new WP_REST_Response(['error'=>'status_endpoint_not_used','message'=>'Video generation is synchronous — no status polling needed.'], 410);
}

/* ── Article Chat Proxy ─────────────────────────────────────────────────────── */
function nova_proxy_article_chat(WP_REST_Request $r): WP_REST_Response {
    $params  = $r->get_json_params();
    $context = sanitize_textarea_field($params['context'] ?? '');
    $model   = sanitize_text_field($params['model'] ?? 'groq/meta-llama/llama-4-scout-17b-16e-instruct');
    $message = sanitize_textarea_field($params['message'] ?? '');
    $history = $params['history'] ?? [];
    $messages = [['role'=>'system','content'=>
        "You are a helpful AI assistant that helps users understand and discuss articles and other content.\n\n".
        "ARTICLE CONTEXT:\n".$context."\n\n".
        "Answer questions based on this context."]];
    foreach ((array)$history as $h) {
        if (isset($h['role'],$h['content']))
            $messages[] = ['role'=>sanitize_text_field($h['role']),'content'=>sanitize_textarea_field($h['content'])];
    }
    $messages[] = ['role'=>'user','content'=>$message];
    $body = json_encode(['model'=>$model,'messages'=>$messages,'stream'=>false,'max_tokens'=>800]);
    $settings2    = get_option('nova_ai_settings', []);
    $internal_key = $settings2['internal_key'] ?? '';
    $resp = wp_remote_post(nova_get_backend_base().'/v1/chat', [
        'body'    => $body,
        'headers' => [
            'Content-Type'   => 'application/json',
            'X-Internal-Key' => $internal_key,
        ],
        'timeout' => 30,
    ]);
    if (is_wp_error($resp)) return new WP_REST_Response(['error'=>$resp->get_error_message()],502);
    return new WP_REST_Response(json_decode(wp_remote_retrieve_body($resp), true), wp_remote_retrieve_response_code($resp));
}


/* ── Subscription & Purchases callbacks ─────────────────────────────────────── */
function nova_normalize_tier(string $tier): string {
    return ($tier === 'free') ? 'free' : 'paid';
}

function nova_proxy_subscription(WP_REST_Request $r): WP_REST_Response {
    $user = wp_get_current_user();
    if (!$user || !$user->ID) return new WP_REST_Response(['error'=>'not logged in'], 401);
    $client_id = get_user_meta($user->ID, 'nova_client_id', true) ?: '';
    if (!$client_id) return new WP_REST_Response(['ok'=>true,'tier'=>'free','status'=>'none','client_id'=>'']);
    // Proxy to backend
    $resp = nova_proxy('/v1/tiers/subscription/'.$client_id);
    $data = $resp->get_data();
    // Normalize tier in response
    if (!empty($data['tier'])) $data['tier'] = nova_normalize_tier($data['tier']);
    return new WP_REST_Response($data, $resp->get_status());
}

function nova_proxy_subscription_cancel(WP_REST_Request $r): WP_REST_Response {
    $user = wp_get_current_user();
    if (!$user || !$user->ID) return new WP_REST_Response(['error'=>'not logged in'], 401);
    $client_id = get_user_meta($user->ID, 'nova_client_id', true) ?: '';
    if (!$client_id) return new WP_REST_Response(['error'=>'no client_id found'], 400);
    $resp = nova_proxy('/v1/tiers/cancel', 'POST', ['user_id'=>$client_id]);
    // On success: downgrade WP user_meta to free
    if ($resp->get_status() < 300) {
        update_user_meta($user->ID, 'nova_tier', 'free');
    }
    return $resp;
}

function nova_proxy_purchases(WP_REST_Request $r): WP_REST_Response {
    $user = wp_get_current_user();
    if (!$user || !$user->ID) return new WP_REST_Response(['error'=>'not logged in'], 401);
    $client_id = get_user_meta($user->ID, 'nova_client_id', true) ?: '';
    if (!$client_id) return new WP_REST_Response(['ok'=>true,'purchases'=>[],'client_id'=>'']);
    return nova_proxy('/v1/tiers/purchases/'.$client_id);
}

/* ── Admin REST callbacks ───────────────────────────────────────────────────── */
function nova_admin_status(): WP_REST_Response {
    // FIX 2026-03-10: /health hat kein /v1 Prefix
    $r = nova_proxy('/health');
    $data = $r->get_data();
    if (empty($data['model_count'])) {
        $fh = nova_proxy('/health');
        $fhd = $fh->get_data();
        if (!empty($fhd['model_count'])) { $data['model_count'] = $fhd['model_count']; $r->set_data($data); }
    }
    return $r;
}

function nova_admin_logs(WP_REST_Request $r): WP_REST_Response {
    $cat   = sanitize_text_field($r->get_param('category') ?? 'all');
    $limit = intval($r->get_param('limit') ?? 100);
    $valid_cats = ['all','api','llm','mcp','error','agent'];
    if (!in_array($cat, $valid_cats, true)) $cat = 'all';
    // FIX 2026-03-11: pass category filter to backend
    $qs = "limit={$limit}";
    if ($cat !== 'all') $qs .= "&category={$cat}";
    return nova_proxy("/v1/triforce/logs/recent?{$qs}");
}

function nova_admin_agents(): WP_REST_Response {
    return nova_proxy('/v1/tristar/cli-agents');
}

function nova_admin_agent_action(WP_REST_Request $r): WP_REST_Response {
    $agent  = sanitize_text_field($r['agent_id']);
    $action = sanitize_text_field($r['action']);
    $map    = ['start'=>'start','stop'=>'stop','restart'=>'restart'];
    if (!isset($map[$action])) return new WP_REST_Response(['error'=>'invalid action'],400);
    return nova_proxy("/v1/tristar/cli-agents/{$agent}/{$action}", 'POST');
}

function nova_admin_mcp_tools(): WP_REST_Response {
    // FIX 2026-03-11: Use server-side MCP tools (81 tools) instead of client-side node/tools (7 desktop tools)
    // /v1/mcp accepts JSON-RPC without auth and returns all server-side tools
    $args = [
        'method'  => 'POST',
        'timeout' => 15,
        'headers' => ['Content-Type' => 'application/json'],
        'body'    => wp_json_encode(['jsonrpc' => '2.0', 'method' => 'tools/list', 'id' => 1]),
    ];
    $response = wp_remote_request(nova_get_backend_base() . '/v1/mcp', $args);
    if (is_wp_error($response)) return new WP_REST_Response(['error' => $response->get_error_message()], 502);
    $body = json_decode(wp_remote_retrieve_body($response), true) ?? [];
    // Normalize JSON-RPC result → {tools: [...]} format admin.js expects
    $tools = $body['result']['tools'] ?? [];
    // Add inputSchema-based default args hint for admin.js
    foreach ($tools as &$t) {
        $props = $t['inputSchema']['properties'] ?? [];
        $t['args'] = array_fill_keys(array_keys($props), '');
    }
    unset($t);
    return new WP_REST_Response(['tools' => $tools, 'count' => count($tools)], 200);
}

function nova_admin_mcp_call(WP_REST_Request $r): WP_REST_Response {
    // FIX 2026-03-11 v2: Use server-side MCP /v1/mcp tools/call (JSON-RPC, no auth needed)
    // This replaces the broken /v1/mcp/node/call (requires connected desktop client + JWT)
    $params = $r->get_json_params();
    $tool   = sanitize_text_field($params['tool'] ?? '');
    $args   = $params['args'] ?? [];
    if (!$tool) return new WP_REST_Response(['error' => 'tool required'], 400);
    $rpc_body = wp_json_encode([
        'jsonrpc'  => '2.0',
        'method'   => 'tools/call',
        'id'       => 1,
        'params'   => ['name' => $tool, 'arguments' => (object)$args],
    ]);
    $resp = wp_remote_request(nova_get_backend_base() . '/v1/mcp', [
        'method'  => 'POST',
        'timeout' => 60,
        'headers' => ['Content-Type' => 'application/json'],
        'body'    => $rpc_body,
    ]);
    if (is_wp_error($resp)) return new WP_REST_Response(['error' => $resp->get_error_message()], 502);
    $body = json_decode(wp_remote_retrieve_body($resp), true) ?? [];
    // Normalize JSON-RPC result → flat response for admin.js
    if (isset($body['error'])) {
        return new WP_REST_Response(['error' => $body['error']['message'] ?? 'MCP error'], 400);
    }
    $content = $body['result']['content'] ?? [];
    $text = implode("\n", array_map(fn($c) => $c['text'] ?? json_encode($c), $content));
    return new WP_REST_Response(['ok' => true, 'result' => $text, 'raw' => $body['result'] ?? []], 200);
}

function nova_admin_crawler_get(): WP_REST_Response {
    return nova_proxy('/v1/admin/crawler/config');
}

function nova_admin_crawler_set(WP_REST_Request $r): WP_REST_Response {
    return nova_proxy('/v1/admin/crawler/config', 'POST', $r->get_json_params());
}

function nova_admin_vault_keys(): WP_REST_Response {
    return nova_proxy_auth('/v1/tristar/settings/api-keys');
}

function nova_admin_vault_set(WP_REST_Request $r): WP_REST_Response {
    $params = $r->get_json_params();
    $key    = sanitize_text_field($params['key'] ?? '');
    $value  = $params['value'] ?? '';
    if (!$key) return new WP_REST_Response(['error'=>'key required'],400);
    return nova_proxy_auth('/v1/tristar/settings/api-keys', 'PUT', ['keys'=>[$key=>$value]]);
}

function nova_admin_settings_get(): WP_REST_Response {
    $s = get_option('nova_ai_settings', []);
    // Never expose full secret values
    return new WP_REST_Response(['ok'=>true,'settings'=>$s]);
}

function nova_admin_settings_set(WP_REST_Request $r): WP_REST_Response {
    if (!wp_verify_nonce($r->get_header('X-WP-Nonce') ?: $r->get_param('_wpnonce') ?: '', 'wp_rest'))
        return new WP_REST_Response(['error'=>'bad nonce'],403);
    $params = $r->get_json_params();
    $s = get_option('nova_ai_settings', []);
    $allowed = ['api_endpoint','api_endpoint_internal','mcp_endpoint','downloads_path',
                'default_model','discuss_button_enabled','widget_enabled','widget_position',
                'widget_color','widget_title','widget_welcome','widget_icon'];
    foreach ($allowed as $k) {
        if (!array_key_exists($k, $params)) {
            continue;
        }
        $value = sanitize_text_field((string)($params[$k]));
        if ($k === 'api_endpoint') {
            $value = nova_normalize_backend_url($value, NOVA_AI_LOCAL_BACKEND);
        } elseif ($k === 'api_endpoint_internal') {
            $value = nova_normalize_backend_url($value, NOVA_AI_BACKEND);
        } elseif ($k === 'mcp_endpoint') {
            $value = nova_normalize_backend_url($value, NOVA_AI_LOCAL_BACKEND);
        }
        $s[$k] = $value;
    }
    update_option('nova_ai_settings', $s);
    return new WP_REST_Response(['ok'=>true]);
}

function nova_admin_restart(WP_REST_Request $r): WP_REST_Response {
    return nova_proxy('/v1/tristar/cli-agents/reload-prompts', 'POST');
}

function nova_admin_bootstrap(WP_REST_Request $r): WP_REST_Response {
    return nova_proxy('/v1/bootstrap', 'POST');
}

/* ── Assets ─────────────────────────────────────────────────────────────────── */
add_action('wp_enqueue_scripts', function () {
    $url = NOVA_AI_PLUGIN_URL.'assets/';
    $ver = NOVA_AI_VERSION;
    // FIX 2026-03-10: filemtime() as version forces Cloudflare/WP cache bust on every JS update
    $js_ver  = @filemtime(NOVA_AI_PLUGIN_DIR.'assets/nova-ai.js')  ?: $ver;
    $css_ver = @filemtime(NOVA_AI_PLUGIN_DIR.'assets/nova-ai.css') ?: $ver;
    wp_enqueue_style('nova-ai-frontend', $url.'nova-ai.css', [], $css_ver);
    wp_enqueue_script('nova-ai-frontend', $url.'nova-ai.js', [], $js_ver, true);
    wp_localize_script('nova-ai-frontend', 'novaAiConfig', [
        'apiBase'    => rest_url('nova-ai/v1'),
        'nonce'      => wp_create_nonce('wp_rest'),
        'nonceUrl'   => rest_url('nova-ai/v1/nonce'),
        'autoTheme'  => true,
        'version'    => $ver,
        'isLoggedIn' => is_user_logged_in(), // FIX 2026-04-11: Boolean statt String
    ]);
    // novaAccountConfig.isLoggedIn via Inline-Script (nicht gecacht, immer frisch)
    if (is_page('account') || strpos($_SERVER['REQUEST_URI'] ?? '', '/account') !== false) {
        $logged = is_user_logged_in();
        wp_add_inline_script('nova-ai-frontend',
            'if(window.novaAccountConfig){novaAccountConfig.isLoggedIn=' . ($logged?'true':'false') . ';}'
        );
    }
});

/* ── Admin Menu ─────────────────────────────────────────────────────────────── */
add_action('admin_menu', function () {
    add_menu_page(
        'Nova AI',
        'Nova AI',
        'manage_options',
        'nova-ai',
        'nova_render_admin_page',
        'dashicons-format-chat',
        30
    );
});

add_action('admin_enqueue_scripts', function ($hook) {
    if (strpos($hook, 'nova-ai') === false) return;
    wp_enqueue_style('nova-ai-admin', NOVA_AI_PLUGIN_URL.'admin/css/admin.css', [], NOVA_AI_VERSION);
    wp_enqueue_script('nova-ai-admin', NOVA_AI_PLUGIN_URL.'admin/js/admin.js', [], NOVA_AI_VERSION, true);
    wp_localize_script('nova-ai-admin', 'novaAdminConfig', [
        'restUrl'     => rest_url('nova-ai/v1'),
        'nonce'       => wp_create_nonce('wp_rest'),
        'apiEndpoint' => nova_get_display_backend_base(),
        'version'     => NOVA_AI_VERSION,
    ]);
});

function nova_render_admin_page(): void {
    $tab = isset($_GET['tab']) ? sanitize_text_field($_GET['tab']) : 'dashboard';
    $settings = get_option('nova_ai_settings', []);
    // Handle settings save via traditional POST (fallback if JS fails)
    if (isset($_POST['nova_ai_save']) && check_admin_referer('nova_ai_settings')) {
        $allowed = ['api_endpoint','default_model','discuss_button_enabled','widget_enabled',
                    'widget_position','widget_color','widget_title','widget_welcome'];
        foreach ($allowed as $k) {
            if (!isset($_POST[$k])) {
                continue;
            }
            $value = sanitize_text_field($_POST[$k]);
            if ($k === 'api_endpoint') {
                $value = nova_normalize_backend_url($value, NOVA_AI_LOCAL_BACKEND);
            }
            $settings[$k] = $value;
        }
        $settings['discuss_button_enabled'] = !empty($_POST['discuss_button_enabled']);
        $settings['widget_enabled']         = !empty($_POST['widget_enabled']);
        update_option('nova_ai_settings', $settings);
        echo '<div class="notice notice-success is-dismissible"><p>✅ Settings gespeichert.</p></div>';
        $settings = get_option('nova_ai_settings', []);
    }
    ?>
<div class="wrap" id="nova-admin-wrap">
<h1>🚀 Nova AI <span style="font-size:13px;opacity:.6;font-weight:400">v<?= esc_html(NOVA_AI_VERSION) ?></span></h1>

<nav class="nav-tab-wrapper">
<?php foreach ([
    'dashboard'=>'📊 Dashboard','settings'=>'⚙️ Settings','system'=>'🖥️ System Status',
    'agents'=>'🤖 Agents','mcp'=>'🌐 MCP Tools','crawler'=>'🕷️ Crawler','vault'=>'🔑 API Vault'
] as $t => $label): ?>
    <a href="?page=nova-ai&tab=<?= $t ?>" class="nav-tab <?= $tab===$t?'nav-tab-active':'' ?>"><?= $label ?></a>
<?php endforeach; ?>
</nav>

<div class="nova-admin-content" style="margin-top:20px">

<?php if ($tab === 'dashboard'): ?>
<div class="nova-stats-row" style="display:flex;gap:16px;flex-wrap:wrap;margin-bottom:20px">
    <div class="nova-stat-card card" style="padding:16px;min-width:180px;text-align:center">
        <div id="stat-status" style="font-size:28px">⏳</div>
        <strong id="stat-status-text">Checking…</strong><br><small>Backend Status</small>
    </div>
    <div class="nova-stat-card card" style="padding:16px;min-width:180px;text-align:center">
        <div style="font-size:28px" id="stat-models">—</div>
        <small>Available Models</small>
    </div>
    <div class="nova-stat-card card" style="padding:16px;min-width:180px;text-align:center">
        <div style="font-size:28px" id="stat-agents">—</div>
        <small>Active Agents</small>
    </div>
    <div class="nova-stat-card card" style="padding:16px;min-width:180px;text-align:center">
        <div style="font-size:28px">🔗</div>
        <small><?= esc_html(nova_get_backend_base()) ?></small>
    </div>
</div>
<div style="display:flex;gap:12px;flex-wrap:wrap;margin-bottom:20px">
    <button class="button button-primary" id="btn-refresh-all">🔄 Refresh Status</button>
    <button class="button" id="btn-test-api">🧪 Test API</button>
    <button class="button" id="btn-view-models">📋 All Models</button>
</div>
<div class="card" style="padding:16px;margin-bottom:16px">
    <h3 style="margin-top:0">📋 Available Shortcodes</h3>
    <table class="widefat">
    <tr><td><code>[ailinux_ai_playground]</code></td><td>AI Chat + Vision + Media</td><td><button class="button button-small nova-copy" data-copy="[ailinux_ai_playground]">📋</button></td></tr>
    <tr><td><code>[ailinux_downloads]</code></td><td>Download Browser</td><td><button class="button button-small nova-copy" data-copy="[ailinux_downloads]">📋</button></td></tr>
    </table>
</div>
<div class="card" style="padding:16px">
    <h3 style="margin-top:0">📜 Recent Log <button class="button button-small" id="btn-refresh-log">🔄</button></h3>
    <div id="admin-log" style="background:#1a1a1a;color:#ccc;font-family:monospace;font-size:12px;height:220px;overflow-y:auto;padding:10px;border-radius:4px">Loading logs…</div>
</div>

<?php elseif ($tab === 'settings'): ?>
<div class="card" style="padding:20px;max-width:680px">
<h3 style="margin-top:0">⚙️ Plugin Settings</h3>
<form method="post">
<?php wp_nonce_field('nova_ai_settings'); ?>
<table class="form-table">
<tr><th>Backend API URL</th><td>
    <input type="text" name="api_endpoint" class="regular-text" value="<?= esc_attr($settings['api_endpoint'] ?? nova_get_display_backend_base()) ?>">
    <p class="description">Internal: <?= esc_html(nova_get_backend_base()) ?></p>
</td></tr>
<tr><th>Default Model</th><td>
    <input type="text" name="default_model" class="regular-text" value="<?= esc_attr($settings['default_model'] ?? '') ?>" placeholder="groq/llama-3.3-70b-versatile">
</td></tr>
<tr><th>Discuss Button</th><td>
    <label><input type="checkbox" name="discuss_button_enabled" <?= !empty($settings['discuss_button_enabled'])?'checked':'' ?>> “Discuss with AI” on posts/pages</label>
</td></tr>
<tr><th>Chat Widget</th><td>
    <label><input type="checkbox" name="widget_enabled" <?= !empty($settings['widget_enabled'])?'checked':'' ?>> Enable floating widget</label>
</td></tr>
</table>
<input type="submit" name="nova_ai_save" class="button button-primary" value="Save">
</form>
</div>

<?php elseif ($tab === 'system'): ?>
<div style="display:flex;gap:12px;margin-bottom:16px">
    <button class="button button-primary" id="btn-system-refresh">🔄 Refresh</button>
</div>
<div id="system-grid" style="display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:16px">
    <div class="card" style="padding:16px"><h4>🔌 Backend API</h4><div id="sys-backend">⏳</div></div>
    <div class="card" style="padding:16px"><h4>🌐 MCP Server</h4><div id="sys-mcp">⏳</div></div>
    <div class="card" style="padding:16px"><h4>🦙 Ollama</h4><div id="sys-ollama">⏳</div></div>
    <div class="card" style="padding:16px"><h4>🔑 Vault</h4><div id="sys-vault">⏳</div></div>
</div>
<div class="card" style="padding:16px;margin-top:16px">
    <h3 style="margin-top:0">📋 Models <span style="font-weight:400;font-size:13px">— <span id="model-count">…</span></span></h3>
    <div style="margin-bottom:10px;display:flex;gap:8px">
        <input type="text" id="model-filter" placeholder="🔍 Filter…" style="flex:1">
        <select id="provider-filter"><option value="">All Providers</option></select>
    </div>
    <div id="models-table" style="max-height:400px;overflow-y:auto">⏳ Loading models…</div>
</div>

<?php elseif ($tab === 'agents'): ?>
<div style="display:flex;gap:12px;margin-bottom:16px">
    <button class="button button-primary" id="btn-agents-refresh">🔄 Refresh</button>
    <button class="button" id="btn-agents-bootstrap">🚀 Bootstrap All</button>
</div>
<div id="agents-grid" style="display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:16px">
    <div class="card" style="padding:16px">⏳ Loading agents…</div>
</div>

<?php elseif ($tab === 'mcp'): ?>
<div style="display:flex;gap:12px;margin-bottom:16px">
    <button class="button button-primary" id="btn-mcp-refresh">🔄 Load tools</button>
</div>
<div style="display:grid;grid-template-columns:2fr 1fr;gap:16px">
    <div class="card" style="padding:16px">
        <h3 style="margin-top:0">🛠️ MCP Tools</h3>
        <input type="text" id="mcp-tool-filter" placeholder="🔍 Search tools…" style="width:100%;margin-bottom:10px">
        <div id="mcp-tools-list" style="max-height:500px;overflow-y:auto">⏳</div>
    </div>
    <div class="card" style="padding:16px">
        <h3 style="margin-top:0">▶️ Tool aufrufen</h3>
        <label>Tool:</label><br>
        <input type="text" id="mcp-call-tool" placeholder="tool_name" style="width:100%;margin-bottom:8px"><br>
        <label>Args (JSON):</label><br>
        <textarea id="mcp-call-args" rows="6" style="width:100%;font-family:monospace;font-size:12px">{}</textarea><br>
        <button class="button button-primary" id="btn-mcp-call" style="margin-top:8px">▶️ Ausführen</button>
        <h4>Ergebnis:</h4>
        <pre id="mcp-call-result" style="background:#1a1a1a;color:#ccc;padding:10px;font-size:11px;max-height:300px;overflow-y:auto;white-space:pre-wrap"></pre>
    </div>
</div>

<?php elseif ($tab === 'crawler'): ?>
<div class="card" style="padding:16px;max-width:680px">
    <h3 style="margin-top:0">🕷️ Crawler Konfiguration</h3>
    <div id="crawler-config">⏳ Lade Config…</div>
    <div id="crawler-form" style="display:none">
        <table class="form-table" id="crawler-table"></table>
        <button class="button button-primary" id="btn-crawler-save">💾 Save</button>
    </div>
    <h3>📊 Crawler Status</h3>
    <button class="button" id="btn-crawler-refresh">🔄 Refresh</button>
    <div id="crawler-status" style="margin-top:10px">—</div>
</div>

<?php elseif ($tab === 'vault'): ?>
<div class="card" style="padding:16px;max-width:680px">
    <h3 style="margin-top:0">🔑 API Vault Keys</h3>
    <div id="vault-keys">⏳ Lade Keys…</div>
    <h3>🔐 Key setzen / aktualisieren</h3>
    <table class="form-table">
        <tr><th>Key Name</th><td><input type="text" id="vault-key-name" class="regular-text" placeholder="ANTHROPIC_API_KEY"></td></tr>
        <tr><th>Value</th><td><input type="password" id="vault-key-value" class="regular-text" placeholder="sk-…"></td></tr>
    </table>
    <button class="button button-primary" id="btn-vault-set">💾 Key speichern</button>
    <div id="vault-msg" style="margin-top:10px"></div>
</div>

<?php endif; ?>

</div><!-- .nova-admin-content -->
</div><!-- .wrap -->
    <?php
}

/* ── Shortcode: AI Playground ───────────────────────────────────────────────── */
add_shortcode('ailinux_ai_playground', function ($atts): string {
    $label = esc_attr($atts['label'] ?? 'AILINUX AI PLAYGROUND');
    $title = esc_html($atts['title'] ?? 'Nova Frontend');
    $desc  = esc_html($atts['desc']  ?? 'Chat, vision analysis and media generation with backend-managed model routing.');
    ob_start(); ?>
<div class="nova-ai-shell" data-nova-theme="auto">
  <div class="nova-hero">
    <div class="nova-hero-label"><?= $label ?></div>
    <h2 class="nova-hero-title"><?= $title ?></h2>
    <p class="nova-hero-desc"><?= $desc ?></p>
    <div class="nova-hero-badges">
      <span class="nova-badge" data-health-badge>Checking backend…</span>
      <span class="nova-badge" data-model-count>—</span>
    </div>
  </div>
  <div class="nova-toolbar">
    <button class="nova-tab active" data-tab="nova-panel-chat">Chat</button>
    <button class="nova-tab" data-tab="nova-panel-vision">Vision Analysis</button>
    <button class="nova-tab" data-tab="nova-panel-media">Media Generation</button>
  </div>
  <div class="nova-panel active" id="nova-panel-chat">
    <div class="nova-form-row-inline">
      <div class="nova-form-group" style="flex:1">
        <label class="nova-label" for="nova-chat-model">Chat Model</label>
        <select id="nova-chat-model" name="nova-chat-model" class="nova-select nova-chat-model" data-model="chat"></select>
      </div>
      <button class="nova-chat-clear" title="Clear chat">🗑</button>
    </div>
    <button class="nova-system-show nova-small-btn">+ System Prompt</button>
    <div class="nova-form-group nova-system-group nova-hidden">
      <label class="nova-label" for="nova-chat-system">System-Prompt <button class="nova-system-toggle">▲</button></label>
      <textarea id="nova-chat-system" name="nova-chat-system" class="nova-textarea nova-chat-system" rows="3" placeholder="You are a helpful assistant…"></textarea>
    </div>
    <div class="nova-chat-history" role="log" aria-live="polite">
      <div class="nova-chat-welcome">
        <div class="nova-chat-welcome-icon">✦</div>
        <p>Ask me anything — <kbd>Enter</kbd> sendet, <kbd>Shift+Enter</kbd> new line</p>
      </div>
    </div>
    <div class="nova-input-area">
      <div class="nova-input-row">
        <textarea id="nova-chat-prompt" name="nova-chat-prompt" class="nova-prompt nova-chat-prompt" placeholder="Type a message…" rows="1" aria-label="Message"></textarea>
        <button class="nova-send-btn nova-chat-send" aria-label="Send">
          <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor"><path d="M.5 1.163A1 1 0 0 1 1.97.28l12.868 6.837a1 1 0 0 1 0 1.766L1.969 15.72A1 1 0 0 1 .5 14.836V10.33a1 1 0 0 1 .816-.983L8.5 8 1.316 6.653A1 1 0 0 1 .5 5.67V1.163Z"/></svg>
        </button>
      </div>
      <div class="nova-shortcuts-hint">
        <span><kbd class="nova-key">↑</kbd> previous input</span>
        <span><kbd class="nova-key">Esc</kbd> clear</span>
        <span><kbd class="nova-key">Ctrl+K</kbd> new chat</span>
      </div>
    </div>
  </div>
  <div class="nova-panel" id="nova-panel-vision">
    <div class="nova-form-group">
      <label class="nova-label" for="nova-vision-model">Vision Model</label>
      <select id="nova-vision-model" name="nova-vision-model" class="nova-select nova-vision-model" data-model="vision"></select>
    </div>
    <div class="nova-form-group">
      <label class="nova-label" for="nova-vision-url">Image URL</label>
      <input type="url" id="nova-vision-url" name="nova-vision-url" class="nova-input nova-vision-url" placeholder="https://…">
    </div>
    <div class="nova-form-group">
      <label class="nova-label" for="nova-vision-file">Or upload a file <small style="opacity:.6">(jpg / png / webp / gif — analysis only, not stored)</small></label>
      <input type="file" id="nova-vision-file" name="nova-vision-file" class="nova-input nova-vision-file"
             accept="image/jpeg,image/png,image/webp,image/gif" style="padding:6px;cursor:pointer;">
      <div class="nova-vision-preview"></div>
    </div>
    <div class="nova-form-group">
      <label class="nova-label" for="nova-vision-task">Analysis task</label>
      <input type="text" id="nova-vision-task" name="nova-vision-task" class="nova-input nova-vision-task" value="Describe this image in detail.">
    </div>
    <button class="nova-action-btn nova-vision-btn">Analyze image</button>
    <div class="nova-output-box">
      <div class="nova-output-label">Result</div>
      <div class="nova-output-text nova-vision-output"></div>
    </div>
  </div>
  <div class="nova-panel" id="nova-panel-media">
    <div class="nova-subtabs">
      <button class="nova-subtab active" data-subtab="bild">Image</button>
      <button class="nova-subtab" data-subtab="video">Video</button>
    </div>
    <div id="nova-media-bild" class="nova-subpanel active">
      <div class="nova-form-group">
        <label class="nova-label" for="nova-img-model">Image model</label>
        <select id="nova-img-model" name="nova-img-model" class="nova-select nova-img-model" data-model="media_image"></select>
      </div>
      <div class="nova-form-group">
        <label class="nova-label">Prompt</label>
        <textarea id="nova-img-prompt" name="nova-img-prompt" class="nova-textarea nova-img-prompt" rows="4" placeholder="A brown bear…"></textarea>
      </div>
      <div class="nova-form-row-inline">
        <div class="nova-form-group">
          <label class="nova-label" for="nova-img-count">Count</label>
          <input type="number" id="nova-img-count" name="nova-img-count" class="nova-input nova-img-count" value="1" min="1" max="4">
        </div>
        <div class="nova-form-group">
          <label class="nova-label" for="nova-img-aspect">Format</label>
          <select id="nova-img-aspect" class="nova-select nova-img-aspect">
            <option value="1:1" selected>1:1 – Square</option>
            <option value="16:9">16:9 – Widescreen</option>
            <option value="4:3">4:3 – Standard</option>
            <option value="3:4">3:4 – Portrait</option>
            <option value="9:16">9:16 – Smartphone</option>
          </select>
        </div>
        <div class="nova-form-group" style="flex:1">
          <label class="nova-label" for="nova-img-size">Resolution</label>
          <select id="nova-img-size" class="nova-select nova-img-size"></select>
        </div>
      </div>
      <button class="nova-action-btn nova-img-btn">Generate image</button>
      <div class="nova-progress nova-img-progress"><div class="nova-progress-bar" style="width:0%"></div></div>
      <div class="nova-output-box nova-img-output"><div class="nova-output-text"></div></div>
    </div>
    <div id="nova-media-video" class="nova-subpanel nova-hidden">
      <div class="nova-form-group">
        <label class="nova-label" for="nova-vid-model">Videomodell</label>
        <select id="nova-vid-model" name="nova-vid-model" class="nova-select nova-vid-model" data-model="media_video"></select>
      </div>
      <div class="nova-form-group">
        <label class="nova-label" for="nova-vid-prompt">Prompt</label>
        <textarea id="nova-vid-prompt" name="nova-vid-prompt" class="nova-textarea nova-vid-prompt" rows="4" placeholder="A brown bear running through a forest…"></textarea>
      </div>
      <div class="nova-form-row-inline">
        <div class="nova-form-group">
          <label class="nova-label" for="nova-vid-duration">Duration (s)</label>
          <input type="number" id="nova-vid-duration" name="nova-vid-duration" class="nova-input nova-vid-duration" value="8" min="4" max="30">
        </div>
        <div class="nova-form-group">
          <label class="nova-label" for="nova-vid-aspect">Format</label>
          <select id="nova-vid-aspect" class="nova-select nova-vid-aspect">
            <option value="16:9" selected>16:9 – Widescreen</option>
            <option value="9:16">9:16 – Portrait</option>
          </select>
        </div>
        <div class="nova-form-group" style="flex:1">
          <label class="nova-label" for="nova-vid-resolution">Resolution</label>
          <select id="nova-vid-resolution" class="nova-select nova-vid-resolution"></select>
        </div>
      </div>
      <button class="nova-action-btn nova-vid-btn">Generate video</button>
      <div class="nova-progress nova-vid-progress"><div class="nova-progress-bar" style="width:0%"></div></div>
      <div class="nova-output-box nova-vid-output"><div class="nova-output-text"></div></div>
    </div>
  </div>
  <div class="nova-panel" id="nova-panel-account">
    <div style="padding:1.5rem 0">
      <div class="nova-account-container" id="nova-account-panel">
        <div style="text-align:center;padding:2rem;color:#94a3b8">
          <div style="font-size:2rem;margin-bottom:.5rem">&#128100;</div>
          <div>Loading account…</div>
        </div>
      </div>
    </div>
  </div>
</div>
    <?php return ob_get_clean();
});

/* ── Shortcode: Downloads (File Browser) ────────────────────────────────────── */
add_shortcode('ailinux_downloads', function ($atts): string {
    $label = esc_attr($atts['label'] ?? 'AILINUX DOWNLOADS');
    $title = esc_html($atts['title'] ?? 'Downloads');
    $desc  = esc_html($atts['desc']  ?? 'Files and packages ready to download.');
    $raw   = wp_remote_get(nova_get_backend_base().'/health', ['timeout'=>10, 'headers'=>['X-Internal-Key' => defined('NOVA_AI_INTERNAL_KEY') ? NOVA_AI_INTERNAL_KEY : (get_option('nova_ai_settings', [])['internal_key'] ?? '')]]);
    $tree  = null;
    if (!is_wp_error($raw)) {
        $body = json_decode(wp_remote_retrieve_body($raw), true);
        if (!empty($body['ok'])) $tree = $body;
    }
    $total_files = 0; $total_bytes = 0;
    if ($tree) {
        $total_bytes = (int)($tree['total_bytes'] ?? 0);
        $count_all = function($node) use (&$count_all) {
            $c = count($node['files'] ?? []);
            foreach (($node['folders'] ?? []) as $f) $c += $count_all($f);
            return $c;
        };
        $total_files = $count_all($tree);
    }
    $fmt = function(int $b): string {
        if ($b >= 1073741824) return round($b/1073741824,1).' GB';
        if ($b >= 1048576)    return round($b/1048576,1).' MB';
        if ($b >= 1024)       return round($b/1024,1).' KB';
        return $b.' B';
    };
    ob_start(); ?>
<div class="nova-dl-wrap" data-nova-theme="auto">

  <?php /* ── Hero header ── */ ?>
  <div class="nova-dl-hero">
    <div class="nova-dl-hero__label"><?= $label ?></div>
    <h2 class="nova-dl-hero__title"><?= $title ?></h2>
    <p class="nova-dl-hero__desc"><?= $desc ?></p>
    <?php if ($tree): ?>
    <div class="nova-dl-hero__stats">
      <span class="nova-dl-stat"><strong><?= $total_files ?></strong> files</span>
      <span class="nova-dl-stat"><strong><?= count($tree['folders'] ?? []) ?></strong> folders</span>
      <?php if ($total_bytes > 0): ?>
      <span class="nova-dl-stat"><strong><?= $fmt($total_bytes) ?></strong> total</span>
      <?php endif; ?>
    </div>
    <?php endif; ?>
  </div>

  <?php if (!$tree): ?>
    <div class="nova-dl-error">⚠ Backend nicht erreichbar – bitte später erneut versuchen.</div>
  <?php else: ?>

  <?php /* ── Breadcrumb ── */ ?>
  <nav class="nova-dl-crumb" id="nova-dl-crumb" aria-label="Verzeichnis-Navigation"></nav>

  <?php /* ── Card grid ── */ ?>
  <div class="nova-dl-grid" id="nova-dl-grid"></div>

  <style>
  /* ── Downloads Shortcode Styles ─────────────────────────── */
  .nova-dl-wrap {
    font-family: var(--font-sans, system-ui, sans-serif);
    width: 100%;
    color: var(--text, #e8edf2);
  }
  /* Hero */
  .nova-dl-hero {
    background: linear-gradient(135deg, var(--bg-1, #131822) 0%, var(--bg-2, #1b2330) 100%);
    border: 1px solid var(--line, #263040);
    border-radius: 16px;
    padding: clamp(20px, 4vw, 40px);
    margin-bottom: 28px;
    text-align: center;
  }
  .nova-dl-hero__label {
    font-size: 0.72rem;
    font-weight: 700;
    letter-spacing: 0.12em;
    color: var(--accent-active, #3aa0ff);
    text-transform: uppercase;
    margin-bottom: 10px;
  }
  .nova-dl-hero__title {
    font-size: clamp(1.5rem, 3vw, 2.2rem);
    font-weight: 700;
    margin: 0 0 10px;
    color: var(--text, #e8edf2);
  }
  .nova-dl-hero__desc {
    color: var(--muted, #a9b3c0);
    font-size: 1rem;
    margin: 0 0 18px;
    max-width: 56ch;
    margin-inline: auto;
  }
  .nova-dl-hero__stats {
    display: flex;
    flex-wrap: wrap;
    justify-content: center;
    gap: 10px;
  }
  .nova-dl-stat {
    background: rgba(58,160,255,.12);
    border: 1px solid rgba(58,160,255,.25);
    color: var(--text, #e8edf2);
    border-radius: 999px;
    padding: 5px 14px;
    font-size: 0.85rem;
  }
  .nova-dl-stat strong { color: var(--accent-active, #3aa0ff); }
  /* Error */
  .nova-dl-error {
    background: rgba(255,80,80,.1);
    border: 1px solid rgba(255,80,80,.3);
    color: #ff8080;
    border-radius: 10px;
    padding: 16px 20px;
    font-size: 0.95rem;
  }
  /* Breadcrumb */
  .nova-dl-crumb {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 4px;
    font-size: 0.88rem;
    color: var(--muted, #a9b3c0);
    margin-bottom: 18px;
    min-height: 28px;
  }
  .nova-dl-crumb a {
    color: var(--accent-active, #3aa0ff);
    text-decoration: none;
    padding: 3px 8px;
    border-radius: 6px;
    transition: background .15s;
  }
  .nova-dl-crumb a:hover { background: rgba(58,160,255,.12); }
  .nova-dl-crumb-sep { opacity: .35; margin: 0 2px; }
  .nova-dl-crumb-cur { font-weight: 600; color: var(--text, #e8edf2); padding: 3px 8px; }
  /* Card grid */
  .nova-dl-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(min(100%, 300px), 1fr));
    gap: 14px;
  }
  .nova-dl-card {
    display: flex;
    align-items: flex-start;
    gap: 14px;
    background: var(--bg-1, #131822);
    border: 1px solid var(--line, #263040);
    border-radius: 12px;
    padding: 16px;
    cursor: pointer;
    transition: border-color .18s, box-shadow .18s, transform .15s;
    position: relative;
    overflow: hidden;
  }
  .nova-dl-card:hover {
    border-color: var(--accent-active, #3aa0ff);
    box-shadow: 0 4px 20px rgba(58,160,255,.15);
    transform: translateY(-2px);
  }
  .nova-dl-card.is-file { cursor: default; }
  .nova-dl-card__icon {
    font-size: 2rem;
    line-height: 1;
    flex-shrink: 0;
    margin-top: 2px;
  }
  .nova-dl-card__body {
    flex: 1 1 0;
    min-width: 0;
    display: flex;
    flex-direction: column;
    gap: 5px;
  }
  .nova-dl-card__name {
    font-weight: 600;
    font-size: 0.94rem;
    word-break: break-word;
    color: var(--text, #e8edf2);
  }
  .nova-dl-card__name a {
    color: inherit;
    text-decoration: none;
  }
  .nova-dl-card__name a:hover { color: var(--accent-active, #3aa0ff); }
  /* AI description */
  .nova-dl-card__desc {
    font-size: 0.8rem;
    color: var(--muted, #a9b3c0);
    line-height: 1.4;
    margin: 0;
  }
  .nova-dl-ai-badge {
    display: inline-flex;
    align-items: center;
    gap: 3px;
    font-size: 0.65rem;
    font-weight: 700;
    color: var(--accent-active, #3aa0ff);
    border: 1px solid currentColor;
    border-radius: 4px;
    padding: 1px 5px;
    margin-right: 5px;
    vertical-align: middle;
    opacity: .75;
    white-space: nowrap;
  }
  /* Meta row */
  .nova-dl-card__meta {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    font-size: 0.75rem;
    color: var(--muted, #a9b3c0);
    opacity: .7;
    margin-top: 2px;
  }
  .nova-dl-sha {
    font-family: ui-monospace, monospace;
    font-size: 0.68rem;
    opacity: .55;
    word-break: break-all;
  }
  /* Action button */
  .nova-dl-card__action {
    flex-shrink: 0;
    display: flex;
    align-items: center;
  }
  .nova-dl-btn {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 36px;
    height: 36px;
    border-radius: 8px;
    font-size: 1.1rem;
    font-weight: 700;
    text-decoration: none;
    transition: background .15s, color .15s;
    border: 1px solid var(--line, #263040);
    background: var(--bg-2, #1b2330);
    color: var(--accent-active, #3aa0ff);
    cursor: pointer;
    font-family: inherit;
    flex-shrink: 0;
  }
  .nova-dl-btn:hover {
    background: var(--accent-active, #3aa0ff);
    color: #0e1116;
    border-color: var(--accent-active, #3aa0ff);
  }
  /* Back card */
  .nova-dl-card.is-back {
    border-style: dashed;
    opacity: .7;
  }
  .nova-dl-card.is-back:hover { opacity: 1; }
  /* Light mode */
  html[data-theme='light'] .nova-dl-hero {
    background: linear-gradient(135deg, #f5f7fb, #f0f4ff);
    border-color: #d7dee9;
  }
  html[data-theme='light'] .nova-dl-hero__title { color: #0f141b; }
  html[data-theme='light'] .nova-dl-card {
    background: #fff;
    border-color: #d7dee9;
  }
  html[data-theme='light'] .nova-dl-card__name { color: #0f141b; }
  html[data-theme='light'] .nova-dl-btn {
    background: #f0f4ff;
    border-color: #d7dee9;
    color: #2f7df4;
  }
  /* Responsive */
  @media (max-width: 480px) {
    .nova-dl-grid { gap: 10px; }
    .nova-dl-card { padding: 12px; gap: 10px; }
    .nova-dl-card__icon { font-size: 1.6rem; }
  }
  </style>

  <script>
  (function(){
    var TREE = <?= json_encode(['files'=>$tree['files']??[],'folders'=>$tree['folders']??[]], JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES) ?>;
    var pathStack = [];

    function fmt(b) {
      b = parseInt(b)||0;
      if (b >= 1073741824) return (b/1073741824).toFixed(1)+' GB';
      if (b >= 1048576)    return (b/1048576).toFixed(1)+' MB';
      if (b >= 1024)       return (b/1024).toFixed(1)+' KB';
      return b+' B';
    }
    function esc(s) {
      var d = document.createElement('div');
      d.textContent = String(s||'');
      return d.innerHTML;
    }
    function getNode(path) {
      var node = TREE;
      for (var i = 0; i < path.length; i++) {
        var found = null;
        for (var j = 0; j < (node.folders||[]).length; j++) {
          if (node.folders[j].name === path[i]) { found = node.folders[j]; break; }
        }
        if (!found) return TREE;
        node = found;
      }
      return node;
    }

    function makeCard(icon, name, descHtml, metaHtml, actionHtml, extraClass, clickFn) {
      var card = document.createElement('div');
      card.className = 'nova-dl-card ' + (extraClass||'');
      card.innerHTML =
        '<div class="nova-dl-card__icon">'+icon+'</div>'
        +'<div class="nova-dl-card__body">'
          +'<div class="nova-dl-card__name">'+name+'</div>'
          +(descHtml ? '<p class="nova-dl-card__desc">'+descHtml+'</p>' : '')
          +(metaHtml ? '<div class="nova-dl-card__meta">'+metaHtml+'</div>' : '')
        +'</div>'
        +(actionHtml ? '<div class="nova-dl-card__action">'+actionHtml+'</div>' : '');
      if (clickFn) card.addEventListener('click', clickFn);
      return card;
    }

    function render() {
      var node = getNode(pathStack);
      var crumb = document.getElementById('nova-dl-crumb');
      var grid  = document.getElementById('nova-dl-grid');

      /* ── Breadcrumb ── */
      var bcHtml = '<a href="#" data-nav="-1">🏠 Root</a>';
      for (var i = 0; i < pathStack.length; i++) {
        bcHtml += '<span class="nova-dl-crumb-sep">›</span>'
                + '<a href="#" data-nav="'+i+'">'+esc(pathStack[i])+'</a>';
      }
      if (pathStack.length > 0) {
        bcHtml += '<span class="nova-dl-crumb-sep">›</span>'
                + '<span class="nova-dl-crumb-cur">'+esc(pathStack[pathStack.length-1])+'</span>';
      }
      crumb.innerHTML = bcHtml;
      crumb.querySelectorAll('a[data-nav]').forEach(function(a){
        a.addEventListener('click', function(e){
          e.preventDefault();
          var idx = parseInt(this.getAttribute('data-nav'));
          pathStack = idx < 0 ? [] : pathStack.slice(0, idx+1);
          render();
        });
      });

      /* ── Grid ── */
      grid.innerHTML = '';

      /* Back button */
      if (pathStack.length > 0) {
        grid.appendChild(makeCard(
          '↩', '<em>Back</em>', '', '', '', 'is-back',
          function(){ pathStack.pop(); render(); }
        ));
      }

      /* Folders */
      (node.folders||[]).forEach(function(f){
        var desc = f.description
          ? '<span class="nova-dl-ai-badge">✦ KI</span>'+esc(f.description)
          : '';
        var meta = (f.file_count != null ? '<span>'+f.file_count+' files</span>' : '')
                 + (f.total_size_formatted || f.total_size ? '<span>'+(f.total_size_formatted||fmt(f.total_size))+'</span>' : '');
        grid.appendChild(makeCard(
          f.icon||'📁',
          esc(f.name),
          desc, meta,
          '<button class="nova-dl-btn" title="Open">›</button>',
          'is-folder',
          function(){ pathStack.push(f.name); render(); }
        ));
      });

      /* Files */
      (node.files||[]).forEach(function(f){
        var nameHtml = f.url
          ? '<a href="'+esc(f.url)+'" download onclick="event.stopPropagation()">'+esc(f.name)+'</a>'
          : esc(f.name);
        var desc = f.description
          ? '<span class="nova-dl-ai-badge">✦ KI</span>'+esc(f.description)
          : '';
        var meta = '<span>'+(f.size_formatted||fmt(f.size||0))+'</span>'
                 + (f.modified ? '<span>'+esc(f.modified)+'</span>' : '')
                 + (f.sha1 ? '<span class="nova-dl-sha">SHA1: '+esc(f.sha1.substring(0,12))+'…</span>' : '');
        var action = f.url
          ? '<a class="nova-dl-btn" href="'+esc(f.url)+'" download onclick="event.stopPropagation()" title="Download">↓</a>'
          : '';
        grid.appendChild(makeCard(f.icon||'📄', nameHtml, desc, meta, action, 'is-file', null));
      });

      /* Empty */
      if (!node.folders?.length && !node.files?.length && pathStack.length === 0) {
        grid.innerHTML = '<div style="grid-column:1/-1;text-align:center;padding:40px;color:var(--muted,#a9b3c0)">📂 No files available.</div>';
      } else if (!node.folders?.length && !node.files?.length) {
        grid.innerHTML = '<div style="grid-column:1/-1;text-align:center;padding:40px;color:var(--muted,#a9b3c0)">📂 Empty folder.</div>';
      }
    }
    render();
  })();
  </script>
  <?php endif; ?>
</div>
    <?php return ob_get_clean();
});

/* ── AccountSuiteService ─────────────────────────────────────────────────────── */
require_once NOVA_AI_PLUGIN_DIR . 'services/AuthService.php';
\NovAI\Services\AuthService::instance();

require_once NOVA_AI_PLUGIN_DIR . 'services/AccountSuiteService.php';
\NovAI\Services\AccountSuiteService::instance();

/* ── WP Block filter ────────────────────────────────────────────────────────── */
add_filter('render_block_core/shortcode', function ($content) {
    if (has_shortcode($content,'ailinux_ai_playground') || has_shortcode($content,'ailinux_downloads'))
        return do_shortcode($content);
    return $content;
});

/* ── Discuss with AI Button (the_content injection) ───────────────────────── */
// DISABLED 2026-04-11: AI Discuss Button entfernt per Markus' Anforderung
// add_filter('the_content', function (string $content): string {
if (false) { // Dead code — kept for reference
(function (string $content): string {
    $s = get_option('nova_ai_settings', []);
    if (empty($s['discuss_button_enabled'])) return $content;
    if (!is_singular()) return $content; // Only on single posts/pages

    ob_start(); ?>
<div id="ai-discuss-wrap">
  <button id="ai-discuss-btn" class="ai-discuss-fab" aria-label="Discuss with AI" title="Discuss with AI">
    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path></svg>
    <span>Discuss with AI</span>
  </button>
  <div id="ai-discuss-panel" class="ai-discuss-panel" aria-hidden="true" role="dialog" aria-label="Discuss this article with AI">
    <div class="ai-discuss-header">
      <span class="ai-discuss-title">&#10022; Discuss with AI</span>
      <div class="ai-discuss-header-right">
        <select id="ai-model-select" class="ai-discuss-model-sel" title="Model"></select>
        <button id="ai-discuss-close" class="ai-discuss-close" aria-label="Close">&#x2715;</button>
      </div>
    </div>
    <div id="ai-discuss-chat" class="ai-discuss-chat" role="log" aria-live="polite">
      <div class="ai-discuss-welcome">Ask me anything about this article &mdash; <kbd>Ctrl+Enter</kbd> sends.</div>
    </div>
    <div id="ai-discuss-output" class="ai-discuss-status"></div>
    <div class="ai-discuss-input-row">
      <textarea id="ai-discuss-input" class="ai-discuss-input" placeholder="Ask about this article&hellip;" rows="2" aria-label="Message"></textarea>
      <button id="ai-discuss-send" class="ai-discuss-send" aria-label="Send">
        <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor"><path d="M.5 1.163A1 1 0 0 1 1.97.28l12.868 6.837a1 1 0 0 1 0 1.766L1.969 15.72A1 1 0 0 1 .5 14.836V10.33a1 1 0 0 1 .816-.983L8.5 8 1.316 6.653A1 1 0 0 1 .5 5.67V1.163Z"/></svg>
      </button>
    </div>
  </div>
</div>
    <?php
    $html = ob_get_clean();
    return $content . $html;
});}

/* AILinux emergency downloads shortcode override */
add_shortcode('ailinux_downloads', function($atts) {
    $base = '/var/www/html/downloads';
    $urlbase = home_url('/downloads');
    $out = '<div class="nova-dl-wrap"><h2>AILinux Downloads</h2>';
    if (!is_dir($base)) return $out.'<p>Downloads path not found.</p></div>';
    $files = [];
    $it = new RecursiveIteratorIterator(new RecursiveDirectoryIterator($base, FilesystemIterator::SKIP_DOTS));
    foreach ($it as $f) {
        if (!$f->isFile()) continue;
        $rel = ltrim(str_replace($base, '', $f->getPathname()), '/');
        $files[] = [$rel, $f->getSize(), $f->getMTime()];
    }
    sort($files);
    $out .= '<p><strong>'.count($files).'</strong> files available</p><div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:12px">';
    foreach ($files as [$rel,$size,$mtime]) {
        $href = trailingslashit($urlbase).implode('/', array_map('rawurlencode', explode('/', $rel)));
        $mb = $size >= 1048576 ? round($size/1048576,1).' MB' : round($size/1024,1).' KB';
        $out .= '<div style="padding:14px;border:1px solid #263040;border-radius:10px;background:#131822;color:#e8edf2">';
        $out .= '<div style="font-weight:700;word-break:break-word">'.esc_html($rel).'</div>';
        $out .= '<div style="font-size:.85rem;color:#a9b3c0;margin:6px 0">'.esc_html($mb).' · '.esc_html(date('Y-m-d H:i',$mtime)).'</div>';
        $out .= '<a href="'.esc_url($href).'" download style="color:#3aa0ff;font-weight:700">Download ↓</a>';
        $out .= '</div>';
    }
    $out .= '</div></div>';
    return $out;
});
require_once __DIR__ . '/includes/downloads-public-override.php';
