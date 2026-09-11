<?php
/**
 * AILinux Auth Service
 * Unified authentication: WordPress ↔ AILinux backend.
 *
 * @package NovAI
 * @version 4.7.0
 */

namespace NovAI\Services;

defined('ABSPATH') || exit;

class AuthService {

    private static ?self $instance = null;
    private string $api_endpoint;
    private string $api_endpoint_internal;
    private string $login_page = 'https://ailinux.me/account';

    private static function normalize_tier_value(string $tier): string {
        $t = strtolower(trim($tier));
        return ($t === 'free' || $t === '') ? 'free' : 'paid';
    }

    // =========================================================================
    // Singleton
    // =========================================================================

    public static function instance(): self {
        if (self::$instance === null) {
            self::$instance = new self();
        }
        return self::$instance;
    }

    private function __construct() {
        $settings = get_option('nova_ai_settings', []);
        $this->api_endpoint          = $settings['api_endpoint'] ?? 'https://api.ailinux.me';
        $this->api_endpoint_internal = $settings['api_endpoint_internal'] ?? '';

        $this->init_hooks();
    }

    private function init_hooks(): void {
        // Existing shortcodes
        add_shortcode('ailinux_login',       [$this, 'render_login_shortcode']);
        add_shortcode('ailinux_account',     [$this, 'render_account_shortcode']);
        add_shortcode('ailinux_register',    [$this, 'render_register_shortcode']);
        add_shortcode('ailinux_auth_button', [$this, 'render_auth_button']);

        // New shortcode
        add_shortcode('nova_login_button', [$this, 'render_nova_login_button']);
        add_shortcode('ailinux_google_login', [$this, 'render_google_login_button']);

        // Login/register redirect
        add_action('login_form_login',    [$this, 'maybe_redirect_login']);
        add_action('login_form_register', [$this, 'maybe_redirect_register']);

        // Central logout / redirect handling
        add_filter('logout_redirect', [$this, 'filter_logout_redirect'], 10, 3);
        add_filter('allowed_redirect_hosts', [$this, 'allow_login_redirect_host']);

        // REST
        add_action('rest_api_init', [$this, 'register_rest_routes']);

        // CORS
        add_filter('rest_pre_dispatch',     [$this, 'handle_cors_preflight'], 10, 3);
        add_filter('rest_pre_serve_request', [$this, 'add_cors_headers'], 10, 4);

        // Sync new WP users to AILinux
        add_action('user_register', [$this, 'sync_user_to_ailinux']);

        // Gutenberg block
        add_action('init', [$this, 'register_login_block'], 15);

        // Scripts
        add_action('wp_enqueue_scripts', [$this, 'enqueue_auth_scripts']);

        // Admin AJAX
        add_action('wp_ajax_nova_set_user_tier',        [$this, 'ajax_set_user_tier']);
        add_action('wp_ajax_nova_invalidate_session',   [$this, 'ajax_invalidate_session']);
    }

    // =========================================================================
    // STATIC API (used globally without instance)
    // =========================================================================

    /**
     * Check if the current request has an authenticated user (WP session active).
     */
    public static function is_logged_in(): bool {
        return is_user_logged_in();
    }

    /**
     * Return current user info including Nova-specific meta.
     */
    public static function get_current_user(): ?array {
        if (!is_user_logged_in()) {
            return null;
        }
        $user_id = get_current_user_id();
        $user    = get_userdata($user_id);
        if (!$user) {
            return null;
        }

        $nova_admin_email = defined('NOVA_ADMIN_EMAIL') ? NOVA_ADMIN_EMAIL : 'admin@ailinux.me';

        return [
            'id'        => $user_id,
            'email'     => $user->user_email,
            'name'      => $user->display_name,
            'tier'      => get_user_meta($user_id, 'nova_tier', true) ?: 'free',
            'client_id' => get_user_meta($user_id, 'nova_client_id', true) ?: '',
            'is_admin'  => $user->user_email === $nova_admin_email,
        ];
    }

    /**
     * Return true only for admin@ailinux.me.
     */
    public static function is_nova_admin(): bool {
        if (!is_user_logged_in()) {
            return false;
        }
        $nova_admin_email = defined('NOVA_ADMIN_EMAIL') ? NOVA_ADMIN_EMAIL : 'admin@ailinux.me';
        return wp_get_current_user()->user_email === $nova_admin_email;
    }

    /**
     * Auto-login from nova_session cookie.
     * Hooked at init priority 1 (see nova-ai-frontend.php).
     */
    public static function maybe_auto_login(): void {
        // Skip if disabled
        if (defined('NOVA_AUTO_LOGIN') && !NOVA_AUTO_LOGIN) {
            return;
        }

        // Already logged in via WP session – nothing to do
        if (is_user_logged_in()) {
            return;
        }

        $cookie_name = defined('NOVA_SESSION_COOKIE') ? NOVA_SESSION_COOKIE : 'nova_session';
        $token = isset($_COOKIE[$cookie_name]) ? sanitize_text_field(wp_unslash($_COOKIE[$cookie_name])) : '';
        if (empty($token)) {
            return;
        }

        // Validate token with AILinux backend
        $settings = get_option('nova_ai_settings', []);
        $endpoint = !empty($settings['api_endpoint_internal'])
            ? $settings['api_endpoint_internal']
            : ($settings['api_endpoint'] ?? 'https://api.ailinux.me');

        $response = wp_remote_get($endpoint . '/v1/auth/verify', [
            'headers'   => ['Authorization' => 'Bearer ' . $token],
            'timeout'   => 5,
            'sslverify' => true,
        ]);

        if (is_wp_error($response) || wp_remote_retrieve_response_code($response) !== 200) {
            // Invalid – clear cookie
            self::clear_session_cookie();
            return;
        }

        $body = json_decode(wp_remote_retrieve_body($response), true);
        if (empty($body['valid']) || empty($body['email'])) {
            self::clear_session_cookie();
            return;
        }

        $email = sanitize_email($body['email']);
        $user_id = email_exists($email);

        if (!$user_id) {
            // Create minimal WP user
            $base     = sanitize_user(strstr($email, '@', true)) ?: 'nova';
            $username = $base;
            $i        = 1;
            while (username_exists($username)) {
                $username = $base . $i++;
            }
            $user_id = wp_create_user($username, wp_generate_password(), $email);
            if (is_wp_error($user_id)) {
                return;
            }
        }

        // Update meta
        update_user_meta($user_id, 'nova_session_token', $token);
        update_user_meta($user_id, 'nova_ailinux_email', $email);
        if (!empty($body['tier'])) {
            $ntier = self::normalize_tier_value(sanitize_text_field($body['tier']));
            update_user_meta($user_id, 'nova_tier', $ntier);
        }
        if (!empty($body['client_id'])) {
            update_user_meta($user_id, 'nova_client_id', sanitize_text_field($body['client_id']));
        }

        // Log in for this request AND set cookie for browser
        wp_set_current_user($user_id);
        wp_set_auth_cookie($user_id, true);
    }

    /**
     * Full logout: notify backend, clear nova cookie, WP logout.
     */
    public static function logout(): void {
        $cookie_name = defined('NOVA_SESSION_COOKIE') ? NOVA_SESSION_COOKIE : 'nova_session';

        if (is_user_logged_in()) {
            $user_id = get_current_user_id();
            $token   = get_user_meta($user_id, 'nova_session_token', true);

            if ($token) {
                $settings = get_option('nova_ai_settings', []);
                $endpoint = $settings['api_endpoint'] ?? 'https://api.ailinux.me';

                // Fire-and-forget to backend
                wp_remote_post($endpoint . '/v1/auth/logout', [
                    'headers'  => [
                        'Authorization' => 'Bearer ' . $token,
                        'Content-Type'  => 'application/json',
                    ],
                    'timeout'  => 3,
                    'blocking' => false,
                ]);

                delete_user_meta($user_id, 'nova_session_token');
            }
        }

        self::clear_session_cookie();
        wp_logout();
    }

    /**
     * Validate a nova_token and write tier/client_id into user meta.
     */
    public static function sync_after_login(int $wp_user_id, string $nova_token): void {
        $settings = get_option('nova_ai_settings', []);
        $endpoint = !empty($settings['api_endpoint_internal'])
            ? $settings['api_endpoint_internal']
            : ($settings['api_endpoint'] ?? 'https://api.ailinux.me');

        $response = wp_remote_get($endpoint . '/v1/auth/verify', [
            'headers' => ['Authorization' => 'Bearer ' . $nova_token],
            'timeout' => 8,
        ]);

        if (is_wp_error($response) || wp_remote_retrieve_response_code($response) !== 200) {
            return;
        }

        $body = json_decode(wp_remote_retrieve_body($response), true);
        if (empty($body['valid'])) {
            return;
        }

        update_user_meta($wp_user_id, 'nova_session_token', sanitize_text_field($nova_token));
        if (!empty($body['tier'])) {
            $ntier = self::normalize_tier_value(sanitize_text_field($body['tier']));
            update_user_meta($wp_user_id, 'nova_tier', $ntier);
        }
        if (!empty($body['client_id'])) {
            update_user_meta($wp_user_id, 'nova_client_id', sanitize_text_field($body['client_id']));
        }
    }

    private static function clear_session_cookie(): void {
        $cookie_names = array_unique([
            defined('NOVA_SESSION_COOKIE') ? NOVA_SESSION_COOKIE : 'nova_session',
            'nova_session',
            'ailinux_token',
            'ailinux_logout',
        ]);

        foreach ($cookie_names as $cookie_name) {
            if (!$cookie_name) {
                continue;
            }
            setcookie($cookie_name, '', time() - 3600, '/', '', is_ssl(), true);
            setcookie($cookie_name, '', time() - 3600, '/', '.ailinux.me', is_ssl(), true);
            unset($_COOKIE[$cookie_name]);
        }
    }
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

    // =========================================================================
    // SHORTCODES
    // =========================================================================

    /**
     * [nova_login_button] – Login or logout button with current user display.
     */
    public function render_nova_login_button($atts): string {
        $settings    = get_option('nova_ai_settings', []);
        $login_url   = $settings['login_url'] ?? $this->login_page;
        $scheme      = is_ssl() ? 'https://' : 'http://';
        $current_url = $scheme . ($_SERVER['HTTP_HOST'] ?? '') . ($_SERVER['REQUEST_URI'] ?? '/');

        if (self::is_logged_in()) {
            $user     = self::get_current_user();
            $username = esc_html($user['name'] ?: $user['email']);
            $nonce    = wp_create_nonce('nova_logout');
            $rest_url = esc_url(rest_url('nova-ai/v1/auth/logout'));
            $home     = esc_url(home_url());

            return <<<HTML
<div class="nova-auth-widget">
    <span class="nova-auth-user">{$username}</span>
    <button class="nova-auth-logout-btn"
            data-nonce="{$nonce}"
            data-url="{$rest_url}"
            data-home="{$home}">Logout</button>
</div>
<script>
(function(){
    document.querySelectorAll('.nova-auth-logout-btn').forEach(function(btn){
        btn.addEventListener('click', function(){
            btn.disabled = true;
            fetch(btn.dataset.url, {
                method: 'POST',
                headers: {'X-WP-Nonce': btn.dataset.nonce, 'Content-Type': 'application/json'}
            }).finally(function(){ window.location.href = btn.dataset.home; });
        });
    });
})();
</script>
HTML;
        }

        $login_with_redirect = esc_url($login_url . '?redirect_back=' . urlencode($current_url));
        return '<a href="' . $login_with_redirect . '" class="nova-auth-login-btn">Sign in with AILinux</a>';
    }

    /**
     * [ailinux_google_login] - top-level Google broker entry.
     */
    public function render_google_login_button($atts): string {
        if (self::is_logged_in()) {
            return '';
        }
        $atts = shortcode_atts(['redirect' => home_url('/')], $atts, 'ailinux_google_login');
        $redirect = $this->resolve_redirect_url((string) $atts['redirect']);
        $url = add_query_arg([
            'google' => '1',
            'redirect' => $redirect,
        ], 'https://login.ailinux.me/');
        return '<a class="ail-btn ail-google-login" data-no-swup href="' . esc_url($url) . '" aria-label="Continue with Google"><span class="ail-google-g" aria-hidden="true">G</span> Continue with Google</a>';
    }

    /**
     * Register the login button as a Gutenberg block (server-side rendered).
     */
    public function register_login_block(): void {
        if (!function_exists('register_block_type')) {
            return;
        }
        register_block_type('nova-ai/login-button', [
            'api_version'     => 2,
            'title'           => 'Nova AI Login Button',
            'description'     => 'Login/Logout-Button für Nova AI (server-side gerendert)',
            'category'        => 'widgets',
            'render_callback' => function ($attrs) {
                return do_shortcode('[nova_login_button]');
            },
            'supports'        => ['html' => false],
        ]);
    }

    // =========================================================================
    // REST ROUTES
    // =========================================================================

    public function register_rest_routes(): void {
        register_rest_route('nova-ai/v1', '/auth/status', [
            'methods'             => 'GET',
            'callback'            => [$this, 'api_auth_status'],
            'permission_callback' => '__return_true',
        ]);

        register_rest_route('nova-ai/v1', '/auth/sync', [
            'methods'             => 'POST',
            'callback'            => [$this, 'api_sync_user'],
            'permission_callback' => '__return_true',
        ]);
        // GET: Browser-basierter WP-Login (setzt Cookie cross-domain via Redirect)
        register_rest_route('nova-ai/v1', '/auth/wp-login', [
            'methods'             => 'GET',
            'callback'            => [$this, 'api_wp_login_redirect'],
            'permission_callback' => '__return_true',
        ]);

        register_rest_route('nova-ai/v1', '/auth/logout', [
            'methods'             => 'POST',
            'callback'            => [$this, 'api_logout'],
            'permission_callback' => '__return_true',
        ]);

        register_rest_route('nova-ai/v1', '/auth/lost-password', [
            'methods'             => 'POST',
            'callback'            => [$this, 'api_lost_password'],
            'permission_callback' => '__return_true',
        ]);


        register_rest_route('nova-ai/v1', '/auth/profile', [
            'methods'             => 'POST',
            'callback'            => [$this, 'api_update_profile'],
            'permission_callback' => '__return_true',
        ]);

        register_rest_route('nova-ai/v1', '/auth/profile', [
            'methods'             => 'GET',
            'callback'            => [$this, 'api_get_profile'],
            'permission_callback' => '__return_true',
        ]);

        // Admin endpoints (nova admin only)
        register_rest_route('nova-ai/v1', '/admin/users', [
            'methods'             => 'GET',
            'callback'            => [$this, 'api_admin_list_users'],
            'permission_callback' => [$this, 'check_nova_admin_permission'],
        ]);

        register_rest_route('nova-ai/v1', '/admin/set-tier', [
            'methods'             => 'POST',
            'callback'            => [$this, 'api_admin_set_tier'],
            'permission_callback' => [$this, 'check_nova_admin_permission'],
        ]);

        register_rest_route('nova-ai/v1', '/admin/invalidate-session', [
            'methods'             => 'POST',
            'callback'            => [$this, 'api_admin_invalidate_session'],
            'permission_callback' => [$this, 'check_nova_admin_permission'],
        ]);
    }


    private function sync_purchases_to_wp(int $wp_user_id, string $client_id): void {
        if (empty($client_id)) return;
        $endpoint = !empty($this->api_endpoint_internal) ? $this->api_endpoint_internal : $this->api_endpoint;
        $resp = wp_remote_get(rtrim($endpoint, '/') . '/tiers/purchases/' . urlencode($client_id), ['timeout' => 5]);
        if (is_wp_error($resp) || wp_remote_retrieve_response_code($resp) !== 200) return;
        $body = json_decode(wp_remote_retrieve_body($resp), true);
        if (!empty($body['purchases']) && is_array($body['purchases'])) {
            update_user_meta($wp_user_id, 'nova_purchases', $body['purchases']);
        }
    }

    public function check_nova_admin_permission(): bool {
        return current_user_can('manage_options') && self::is_nova_admin();
    }

    // =========================================================================
    // REST CALLBACKS
    // =========================================================================

    public function api_auth_status(): array {
        return [
            'wp_logged_in' => is_user_logged_in(),
            'user'         => $this->get_current_ailinux_user(),
            'login_url'    => $this->login_page,
            'account_url'  => $this->login_page . '?view=account',
        ];
    }

    public function api_logout(\WP_REST_Request $request): array {
        self::logout();
        return ['success' => true, 'redirect' => home_url()];
    }

    public function api_sync_user(\WP_REST_Request $request) {
        $email        = sanitize_email($request->get_param('email'));
        $token        = sanitize_text_field($request->get_param('token'));
        $tier         = sanitize_text_field($request->get_param('tier'));
        $client_id    = sanitize_text_field($request->get_param('client_id'));
        $display_name = sanitize_text_field($request->get_param('name'));

        if (!$email || !$token) {
            return new \WP_Error('missing_params', 'Email and token required', ['status' => 400]);
        }

        $verified = $this->verify_ailinux_token($email, $token);
        if (!$verified) {
            return new \WP_Error('invalid_token', 'Token verification failed', ['status' => 401]);
        }

        $user_id = $this->ensure_wp_user($email, $display_name);
        if (is_wp_error($user_id)) {
            return $user_id;
        }

        $verified_tier      = $this->extract_ailinux_field($verified, ['tier', 'plan', 'subscription']);
        $verified_client_id = $this->extract_ailinux_field($verified, ['client_id', 'clientId', 'id']);
        $verified_name      = $this->extract_ailinux_field($verified, ['name', 'display_name', 'full_name']);

        if ($verified_name && !$display_name) {
            wp_update_user(['ID' => $user_id, 'display_name' => $verified_name]);
        }

        update_user_meta($user_id, 'nova_ailinux_email', $email);
        update_user_meta($user_id, 'nova_session_token', $token);

        if ($verified_client_id || $client_id) {
            update_user_meta($user_id, 'nova_client_id', $verified_client_id ?: $client_id);
        }
        if ($verified_tier || $tier) {
            update_user_meta($user_id, 'nova_tier', self::normalize_tier_value($verified_tier ?: $tier));
        }

        // Sync purchases to WP user_meta
        $this->sync_purchases_to_wp($user_id, $verified_client_id ?: $client_id);

        wp_set_auth_cookie($user_id, true);

        return [
            'success'   => true,
            'user_id'   => $user_id,
            'can_admin' => user_can($user_id, 'manage_options'),
            'message'   => 'User synced and logged in',
        ];
    }

    public function api_admin_list_users(\WP_REST_Request $request): array {
        $users  = get_users(['fields' => ['ID', 'user_email', 'display_name'], 'number' => 200]);
        $result = [];
        foreach ($users as $u) {
            $last_active = get_user_meta($u->ID, 'nova_last_active', true);
            $result[] = [
                'id'          => $u->ID,
                'email'       => $u->user_email,
                'name'        => $u->display_name,
                'tier'        => get_user_meta($u->ID, 'nova_tier', true) ?: 'free',
                'last_active' => $last_active ?: '',
            ];
        }
        return $result;
    }

    public function api_admin_set_tier(\WP_REST_Request $request) {
        $user_id = (int) $request->get_param('user_id');
        $tier    = sanitize_text_field($request->get_param('tier'));

        $allowed = ['free', 'paid'];
        if (!$user_id || !in_array($tier, $allowed, true)) {
            error_log('[NovAI Auth] Blocked invalid tier: ' . $tier . ' for uid ' . $user_id);
            return new \WP_Error('invalid_tier', 'Invalid tier value', ['status' => 400]);
        }

        // Block privilege escalation attempts
        $target = get_userdata($user_id);
        if (!$target) {
            return new \WP_Error('not_found', 'User not found', ['status' => 404]);
        }

        $nova_admin_email = defined('NOVA_ADMIN_EMAIL') ? NOVA_ADMIN_EMAIL : 'admin@ailinux.me';
        if ($target->user_email === $nova_admin_email) {
            // Admin's tier is managed separately – block
            error_log('[NovAI Auth] Blocked attempt to change admin tier for uid ' . $user_id);
            return new \WP_Error('forbidden', 'Cannot change admin tier', ['status' => 403]);
        }

        update_user_meta($user_id, 'nova_tier', $tier);
        return ['success' => true, 'user_id' => $user_id, 'tier' => $tier];
    }

    public function api_admin_invalidate_session(\WP_REST_Request $request) {
        $user_id = (int) $request->get_param('user_id');
        if (!$user_id) {
            return new \WP_Error('missing_params', 'user_id required', ['status' => 400]);
        }

        delete_user_meta($user_id, 'nova_session_token');

        // Destroy all WP auth sessions
        $sessions = \WP_Session_Tokens::get_instance($user_id);
        $sessions->destroy_all();

        return ['success' => true, 'user_id' => $user_id];
    }

    // =========================================================================
    // ADMIN AJAX
    // =========================================================================

    public function ajax_set_user_tier(): void {
        check_ajax_referer('nova_admin_action', 'nonce');
        if (!current_user_can('manage_options')) {
            wp_die('Unauthorized', 403);
        }

        $user_id = (int) ($_POST['user_id'] ?? 0);
        $tier    = sanitize_text_field($_POST['tier'] ?? '');
        $allowed = ['free', 'paid'];

        if (!$user_id || !in_array($tier, $allowed, true)) {
            wp_send_json_error('Invalid params');
        }

        $nova_admin_email = defined('NOVA_ADMIN_EMAIL') ? NOVA_ADMIN_EMAIL : 'admin@ailinux.me';
        $target           = get_userdata($user_id);
        if ($target && $target->user_email === $nova_admin_email) {
            error_log('[NovAI Auth] Blocked tier change for admin via AJAX, uid ' . $user_id);
            wp_send_json_error('Cannot change admin tier');
        }

        update_user_meta($user_id, 'nova_tier', $tier);
        wp_send_json_success(['user_id' => $user_id, 'tier' => $tier]);
    }

    public function ajax_invalidate_session(): void {
        check_ajax_referer('nova_admin_action', 'nonce');
        if (!current_user_can('manage_options')) {
            wp_die('Unauthorized', 403);
        }

        $user_id = (int) ($_POST['user_id'] ?? 0);
        if (!$user_id) {
            wp_send_json_error('Invalid user_id');
        }

        delete_user_meta($user_id, 'nova_session_token');
        $sessions = \WP_Session_Tokens::get_instance($user_id);
        $sessions->destroy_all();

        wp_send_json_success(['user_id' => $user_id]);
    }

    // =========================================================================
    // CORS
    // =========================================================================

    public function add_cors_headers($value, $server, $request, $result) {
        $origin = get_http_origin();
        if ($origin && in_array($origin, $this->get_allowed_origins(), true)) {
            header('Access-Control-Allow-Origin: ' . $origin);
            header('Access-Control-Allow-Credentials: true');
            header('Access-Control-Allow-Methods: GET, POST, OPTIONS');
            header('Access-Control-Allow-Headers: Content-Type, Authorization, X-WP-Nonce');
            header('Vary: Origin');
        }
        return $value;
    }

    public function handle_cors_preflight($result, $server, $request) {
        if ('OPTIONS' !== $request->get_method()) {
            return $result;
        }
        $origin = get_http_origin();
        if ($origin && in_array($origin, $this->get_allowed_origins(), true)) {
            return new \WP_REST_Response(null, 200);
        }
        return $result;
    }

    private function get_allowed_origins(): array {
        $origins    = [];
        $settings   = get_option('nova_ai_settings', []);
        $login_url  = $settings['login_url'] ?? 'https://ailinux.me/account';
        $login_orig = $this->to_origin($login_url);
        if ($login_orig) {
            $origins[] = $login_orig;
        }
        $site_orig = $this->to_origin(home_url());
        if ($site_orig && !in_array($site_orig, $origins, true)) {
            $origins[] = $site_orig;
        }
        return $origins;
    }

    private function to_origin(string $url): string {
        $parts = wp_parse_url($url);
        if (empty($parts['scheme']) || empty($parts['host'])) {
            return '';
        }
        $origin = $parts['scheme'] . '://' . $parts['host'];
        if (!empty($parts['port'])) {
            $origin .= ':' . $parts['port'];
        }
        return $origin;
    }

    // =========================================================================
    // LOGIN / REGISTER REDIRECTS
    // =========================================================================

    private function is_ailinux_wp_admin_bypass(): bool {
        if (isset($_GET['ailinux_wp_admin']) && $_GET['ailinux_wp_admin'] === '1') {
            return true;
        }

        $redirect_to = $_GET['redirect_to'] ?? '';
        if (is_string($redirect_to) && strpos($redirect_to, 'ailinux_wp_admin=1') !== false) {
            return true;
        }

        return false;
    }

    public function maybe_redirect_login(): void {
        $redirect    = isset($_GET['redirect_to']) ? $_GET['redirect_to'] : home_url();
        $use_ailinux = get_option('nova_ai_use_unified_login', true);
        if ($use_ailinux && !isset($_GET['wp_login'])) {
            if ($this->is_ailinux_wp_admin_bypass()) {
                return;
            }

            wp_redirect($this->login_page . '?redirect=' . urlencode(wp_validate_redirect($redirect, home_url())));
            exit;
        }
    }

    public function maybe_redirect_register(): void {
        $use_ailinux = get_option('nova_ai_use_unified_login', true);
        if ($use_ailinux && !isset($_GET['wp_register'])) {
            wp_redirect($this->login_page . '?tab=register');
            exit;
        }
    }

    // =========================================================================
    // USER MANAGEMENT HELPERS
    // =========================================================================

    private function verify_ailinux_token(string $email, string $token) {
        $endpoint = $this->get_server_api_endpoint();
        // Canonical TriForce session verification endpoint.
        // HTTP 200 means the bearer token is valid; the canonical email is returned top-level.
        $response = wp_remote_get($endpoint . '/v1/auth/verify', [
            'headers' => ['Authorization' => 'Bearer ' . $token],
            'timeout' => 10,
        ]);

        if (is_wp_error($response)) {
            return false;
        }
        if (wp_remote_retrieve_response_code($response) !== 200) {
            return false;
        }

        $body = json_decode(wp_remote_retrieve_body($response), true);
        if (!is_array($body)) {
            return false;
        }

        // Email kann an mehreren Stellen stehen (neue API: session.email; ältere: top-level)
        $verified_email = $body['session']['email'] ?? ($body['email'] ?? '');
        if ($verified_email && strtolower($verified_email) !== strtolower($email)) {
            return false;
        }

        // Shim: Shape angleichen an das, was Rest-of-Code erwartet (top-level 'email', 'valid')
        $body['valid'] = true;
        if (!isset($body['email']) && $verified_email) {
            $body['email'] = $verified_email;
        }
        return $body;
    }

    private function ensure_wp_user(string $email, string $display_name = '') {
        $user_id = email_exists($email);
        if ($user_id) {
            return $user_id;
        }

        $base = sanitize_user(strstr($email, '@', true));
        if (empty($base)) {
            $base = 'ailinux';
        }

        $username = $base;
        $suffix   = 1;
        while (username_exists($username)) {
            $username = $base . $suffix++;
        }

        $user_id = wp_create_user($username, wp_generate_password(), $email);
        if (is_wp_error($user_id)) {
            return $user_id;
        }

        if (!empty($display_name)) {
            wp_update_user(['ID' => $user_id, 'display_name' => $display_name]);
        }

        return $user_id;
    }

    private function extract_ailinux_field(array $data, array $keys): string {
        foreach ($keys as $key) {
            foreach ([$data, $data['user'] ?? [], $data['data'] ?? []] as $src) {
                if (isset($src[$key]) && $src[$key] !== '') {
                    return (string) $src[$key];
                }
            }
        }
        return '';
    }

    private function get_server_api_endpoint(): string {
        if (!empty($this->api_endpoint_internal)) {
            return $this->api_endpoint_internal;
        }
        $parsed = wp_parse_url($this->api_endpoint);
        $host   = $parsed['host'] ?? '';
        if ($host && in_array($host, ['localhost', '127.0.0.1'], true) && file_exists('/.dockerenv')) {
            $scheme = $parsed['scheme'] ?? 'http';
            $port   = isset($parsed['port']) ? ':' . $parsed['port'] : '';
            $path   = $parsed['path'] ?? '';
            return $scheme . '://host.docker.internal' . $port . $path;
        }
        return $this->api_endpoint;
    }

    // =========================================================================
    // SCRIPTS
    // =========================================================================

    public function enqueue_auth_scripts(): void {
        $plugin_url = defined('NOV_AI_PLUGIN_URL') ? NOV_AI_PLUGIN_URL : plugin_dir_url(dirname(__FILE__));
        $plugin_ver = defined('NOV_AI_VERSION')    ? NOV_AI_VERSION    : '1.0.0';
        wp_enqueue_script(
            'ailinux-auth',
            $plugin_url . 'assets/js/auth.js',
            ['jquery'],
            $plugin_ver,
            true
        );

        wp_localize_script('ailinux-auth', 'ailinuxAuth', [
            'apiEndpoint'    => $this->api_endpoint,
            'loginPage'      => $this->login_page,
            'ajaxUrl'        => admin_url('admin-ajax.php'),
            'restUrl'        => rest_url('nova-ai/v1'),
            'syncUrl'        => rest_url('nova-ai/v1/auth/sync'),
            'adminUrl'       => admin_url(),
            'defaultRedirect' => home_url(),
            'nonce'          => wp_create_nonce('ailinux_auth'),
            'isLoggedIn'     => is_user_logged_in(),
            'currentUser'    => $this->get_current_ailinux_user(),
        ]);
    }

    // =========================================================================
    // SHORTCODES (existing)
    // =========================================================================

    public function render_login_shortcode($atts): string {
        $atts        = shortcode_atts(['redirect' => home_url(), 'style' => 'button'], $atts);
        $redirect_url = $this->resolve_redirect_url($atts['redirect']);

        if (is_user_logged_in()) {
            return $this->render_account_shortcode($atts);
        }

        ob_start();

        if ($atts['style'] === 'iframe') { ?>
            <iframe src="<?php echo esc_url($this->login_page); ?>?embed=1"
                    class="ailinux-login-iframe"
                    title="AILinux Account Login"
                    loading="eager"
                    scrolling="no"
                    referrerpolicy="strict-origin-when-cross-origin"
                    style="width:100%;max-width:100%;min-width:0;height:820px;border:none;border-radius:12px;display:block;overflow:hidden;"
                    allow="clipboard-write; identity-credentials-get"></iframe>
            <style>
            .ailinux-login-iframe{width:100%!important;max-width:100%!important;min-width:0!important;display:block;margin:0 auto;background:transparent}
            @media(max-width:640px){.ailinux-login-iframe{height:780px!important;border-radius:10px!important}}
            </style>
            <script>
            (function(){
                if(window.__ailinuxLoginIframeResizeInstalled)return;
                window.__ailinuxLoginIframeResizeInstalled=true;
                window.addEventListener('message',function(event){
                    if(event.origin!=='https://login.ailinux.me')return;
                    var data=event.data;
                    if(!data||data.type!=='ailinux_login_resize')return;
                    var height=Math.max(420,Math.min(1800,Number(data.height)||0));
                    if(!height)return;
                    document.querySelectorAll('.ailinux-login-iframe').forEach(function(frame){
                        frame.style.setProperty('height',height+'px','important');
                    });
                });
            })();
            </script>
        <?php } elseif ($atts['style'] === 'form') { ?>
            <div class="ailinux-login-form" id="ailinux-login-form" data-redirect="<?php echo esc_attr($redirect_url); ?>">
                <div class="ailinux-logo">🤖 AILinux</div>
                <input type="email" id="ailinux-email" placeholder="Email" required>
                <input type="password" id="ailinux-password" placeholder="Password" required>
                <button type="button" onclick="ailinuxLogin()">Sign in</button>
                <p class="ailinux-register-link">
                    <a href="<?php echo esc_url($this->login_page); ?>?tab=register">Account erstellen</a>
                </p>
                <div id="ailinux-login-message"></div>
            </div>
            <style>
            .ailinux-login-form{max-width:400px;margin:2rem auto;padding:2rem;background:#12121a;border-radius:16px;border:1px solid #2a2a3a}
            .ailinux-login-form .ailinux-logo{font-size:1.5rem;text-align:center;margin-bottom:1.5rem;color:#fff}
            .ailinux-login-form input{width:100%;padding:.875rem;margin-bottom:1rem;background:#0a0a0f;border:1px solid #2a2a3a;border-radius:8px;color:#fff;font-size:1rem}
            .ailinux-login-form input:focus{border-color:#3b82f6;outline:none}
            .ailinux-login-form button{width:100%;padding:1rem;background:linear-gradient(135deg,#3b82f6,#8b5cf6);border:none;border-radius:10px;color:#fff;font-weight:600;cursor:pointer}
            .ailinux-register-link{text-align:center;margin-top:1rem}
            .ailinux-register-link a{color:#3b82f6}
            #ailinux-login-message{margin-top:1rem;padding:.75rem;border-radius:8px;text-align:center;display:none}
            #ailinux-login-message.error{display:block;background:rgba(239,68,68,.15);color:#ef4444}
            #ailinux-login-message.success{display:block;background:rgba(16,185,129,.15);color:#10b981}
            </style>
        <?php } else { ?>
            <a href="<?php echo esc_url($this->login_page . '?redirect=' . urlencode($redirect_url)); ?>"
               class="ailinux-auth-btn">🔐 Sign in</a>
            <style>
            .ailinux-auth-btn{display:inline-flex;align-items:center;gap:.5rem;padding:.75rem 1.5rem;background:linear-gradient(135deg,#3b82f6,#8b5cf6);color:#fff;text-decoration:none;border-radius:10px;font-weight:600;transition:all .2s}
            .ailinux-auth-btn:hover{transform:translateY(-2px);box-shadow:0 10px 20px rgba(59,130,246,.3);color:#fff}
            </style>
        <?php }

        return ob_get_clean();
    }

    public function render_account_shortcode($atts): string {
        $atts = shortcode_atts(['show_tier' => true, 'show_logout' => true], $atts);

        ob_start(); ?>
        <div class="ailinux-account" id="ailinux-account">
            <div class="ailinux-account-loading">Loading account...</div>
        </div>
        <style>
        .ailinux-account{padding:1.5rem;background:#12121a;border-radius:16px;border:1px solid #2a2a3a;max-width:400px}
        .ailinux-account-info{display:flex;align-items:center;gap:1rem}
        .ailinux-account-avatar{width:48px;height:48px;background:linear-gradient(135deg,#3b82f6,#8b5cf6);border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:1.25rem}
        .ailinux-account-details h3{margin:0;font-size:1rem;color:#fff}
        .ailinux-account-tier{display:inline-block;padding:.25rem .75rem;background:linear-gradient(135deg,#8b5cf6,#3b82f6);border-radius:20px;font-size:.75rem;font-weight:600;color:#fff;margin-top:.25rem}
        .ailinux-account-actions{margin-top:1rem;display:flex;gap:.5rem}
        .ailinux-account-actions a{padding:.5rem 1rem;background:#2a2a3a;color:#94a3b8;text-decoration:none;border-radius:8px;font-size:.875rem}
        .ailinux-account-actions a:hover{background:#3a3a4a;color:#fff}
        </style>
        <script>
        document.addEventListener('DOMContentLoaded', function() {
            var container = document.getElementById('ailinux-account');
            var token = localStorage.getItem('ailinux_token');
            var email = localStorage.getItem('ailinux_email');
            var tier  = localStorage.getItem('ailinux_tier') || 'free';
            if (token && email) {
                container.innerHTML = '<div class="ailinux-account-info"><div class="ailinux-account-avatar">👤</div><div class="ailinux-account-details"><h3>' + email + '</h3><span class="ailinux-account-tier">' + tier.toUpperCase() + '</span></div></div><div class="ailinux-account-actions"><a href="https://ailinux.me/account">Account</a><a href="https://update.ailinux.me">Downloads</a><a href="#" onclick="ailinuxLogout();return false;">Logout</a></div>';
            } else {
                container.innerHTML = '<p style="color:#94a3b8;text-align:center">Not signed in</p><a href="https://ailinux.me/account" class="ailinux-auth-btn" style="display:block;text-align:center;margin-top:1rem">🔐 Sign in</a>';
            }
        });
        function ailinuxLogout() {
            localStorage.removeItem('ailinux_token');
            localStorage.removeItem('ailinux_email');
            localStorage.removeItem('ailinux_tier');
            localStorage.removeItem('ailinux_client_id');
            location.reload();
        }
        </script>
        <?php
        return ob_get_clean();
    }

    public function render_register_shortcode($atts): string {
        $atts = shortcode_atts(['style' => 'button'], $atts);
        ob_start(); ?>
        <a href="<?php echo esc_url($this->login_page . '?tab=register'); ?>" class="ailinux-register-btn">
            🎉 Register for free
        </a>
        <style>
        .ailinux-register-btn{display:inline-flex;align-items:center;gap:.5rem;padding:.75rem 1.5rem;background:linear-gradient(135deg,#10b981,#06b6d4);color:#fff;text-decoration:none;border-radius:10px;font-weight:600;transition:all .2s}
        .ailinux-register-btn:hover{transform:translateY(-2px);box-shadow:0 10px 20px rgba(16,185,129,.3);color:#fff}
        </style>
        <?php
        return ob_get_clean();
    }

    public function render_auth_button($atts): string {
        ob_start(); ?>
        <div class="ailinux-auth-toggle" id="ailinux-auth-toggle">
            <a href="https://ailinux.me/account" class="ailinux-auth-btn">🔐 Sign in</a>
        </div>
        <script>
        document.addEventListener('DOMContentLoaded', function() {
            var container = document.getElementById('ailinux-auth-toggle');
            var token = localStorage.getItem('ailinux_token');
            var email = localStorage.getItem('ailinux_email');
            if (token && email) {
                container.innerHTML = '<a href="https://ailinux.me/account" class="ailinux-auth-btn ailinux-logged-in">👤 ' + email.split('@')[0] + '</a>';
            }
        });
        </script>
        <style>
        .ailinux-auth-btn{display:inline-flex;align-items:center;gap:.5rem;padding:.5rem 1rem;background:linear-gradient(135deg,#3b82f6,#8b5cf6);color:#fff;text-decoration:none;border-radius:8px;font-weight:500;font-size:.875rem}
        .ailinux-auth-btn.ailinux-logged-in{background:linear-gradient(135deg,#10b981,#06b6d4)}
        </style>
        <?php
        return ob_get_clean();
    }

    // =========================================================================
    // MISC HELPERS
    // =========================================================================

    private function get_current_ailinux_user(): ?array {
        if (is_user_logged_in()) {
            $user = wp_get_current_user();
            return [
                'email'    => $user->user_email,
                'name'     => $user->display_name,
                'wp_user'  => true,
                'tier'     => get_user_meta($user->ID, 'nova_tier', true) ?: 'free',
            ];
        }
        return null;
    }

    private function resolve_redirect_url(string $redirect): string {
        $redirect = trim($redirect);
        if ($redirect === '') return home_url();
        if (in_array(strtolower($redirect), ['admin', 'dashboard', 'wp-admin'], true)) return admin_url();
        if (str_starts_with($redirect, '/')) return home_url($redirect);
        return wp_validate_redirect($redirect, home_url());
    }

    public function sync_user_to_ailinux(int $user_id): void {
        $user = get_userdata($user_id);
        if (!$user) return;

        $endpoint = $this->get_server_api_endpoint();
        wp_remote_post($endpoint . '/v1/auth/register', [
            'body'    => wp_json_encode([
                'email'    => $user->user_email,
                'password' => wp_generate_password(16),
                'name'     => $user->display_name,
                'source'   => 'wordpress',
            ]),
            'headers' => ['Content-Type' => 'application/json'],
            'timeout' => 10,
        ]);
    }

    public function add_auth_menu_items(string $items, $args): string {
        if ($args->theme_location !== 'primary') return $items;
        return $items . '<li class="menu-item ailinux-menu-auth">' . do_shortcode('[ailinux_auth_button]') . '</li>';
    }

    /**
     * GET /auth/profile — return current WP user profile data
     */
    public function api_get_profile(\WP_REST_Request $request): \WP_REST_Response {
        $token = sanitize_text_field($request->get_param('token') ?? '');
        $user  = null;

        // Try token-based lookup first, then WP session
        if ($token) {
            $users = get_users(['meta_key' => 'nova_session_token', 'meta_value' => $token, 'number' => 1]);
            if (!empty($users)) $user = $users[0];
        }
        if (!$user && is_user_logged_in()) {
            $user = wp_get_current_user();
        }
        if (!$user) {
            return new \WP_REST_Response(['error' => 'Not authenticated'], 401);
        }

        $tier        = get_user_meta($user->ID, 'nova_tier', true) ?: 'free';
        $entitlements = (array)(get_user_meta($user->ID, 'nova_entitlements', true) ?: []);
        $purchases   = (array)(get_user_meta($user->ID, 'nova_purchases', true) ?: []);
        $client_id   = get_user_meta($user->ID, 'nova_client_id', true) ?: '';

        return new \WP_REST_Response([
            'ok'           => true,
            'id'           => $user->ID,
            'email'        => $user->user_email,
            'display_name' => $user->display_name,
            'tier'         => $tier,
            'entitlements' => $entitlements,
            'purchases'    => $purchases,
            'client_id'    => $client_id,
            'can_admin'    => user_can($user->ID, 'manage_options'),
        ], 200);
    }

    /**
     * POST /auth/profile — update WP user profile fields
     */
    public function api_update_profile(\WP_REST_Request $request): \WP_REST_Response {
        $token        = sanitize_text_field($request->get_param('token') ?? '');
        $display_name = sanitize_text_field($request->get_param('display_name') ?? '');
        $user         = null;

        if ($token) {
            $users = get_users(['meta_key' => 'nova_session_token', 'meta_value' => $token, 'number' => 1]);
            if (!empty($users)) $user = $users[0];
        }
        if (!$user && is_user_logged_in()) {
            $user = wp_get_current_user();
        }
        if (!$user) {
            return new \WP_REST_Response(['error' => 'Not authenticated'], 401);
        }

        $updated = [];
        if ($display_name !== '') {
            wp_update_user(['ID' => $user->ID, 'display_name' => $display_name]);
            $updated[] = 'display_name';
        }

        return new \WP_REST_Response(['ok' => true, 'updated' => $updated], 200);
    }

    /**
     * Request a WordPress password reset mail.
     * Generic response avoids account enumeration.
     */
    public function api_lost_password(\WP_REST_Request $request): \WP_REST_Response {
        $email = sanitize_email((string) $request->get_param('email'));

        if (!is_email($email)) {
            return new \WP_REST_Response([
                'ok'      => false,
                'error'   => 'invalid_email',
                'message' => 'Please enter a valid email address.',
            ], 400);
        }

        $user = get_user_by('email', $email);

        if (!$user) {
            return new \WP_REST_Response([
                'ok'      => true,
                'message' => 'If an account exists, a password reset email has been sent.',
            ], 200);
        }

        $result = retrieve_password($user->user_login);

        if (is_wp_error($result)) {
            return new \WP_REST_Response([
                'ok'      => false,
                'error'   => 'mail_failed',
                'message' => 'The password reset email could not be sent. Please try again later.',
            ], 500);
        }

        return new \WP_REST_Response([
            'ok'      => true,
            'message' => 'If an account exists, a password reset email has been sent.',
        ], 200);
    }

    /**
     * GET /wp-json/nova-ai/v1/auth/wp-login?token=...&email=...&tier=...&redirect=...
     * Setzt WP-Auth-Cookie und redirectet — löst Cross-Domain SameSite Problem.
     * Browser navigiert direkt auf ailinux.me, daher Cookie korrekt gesetzt.
     */
    public function api_wp_login_redirect(\WP_REST_Request $request) {
        $code      = sanitize_text_field($request->get_param('code') ?: '');
        $token     = sanitize_text_field($request->get_param('token') ?: '');
        $email     = sanitize_email($request->get_param('email') ?: '');
        $tier      = sanitize_text_field($request->get_param('tier') ?: 'free');
        $name      = sanitize_text_field($request->get_param('name') ?: '');
        $redirect  = esc_url_raw($request->get_param('redirect') ?: home_url('/'));
        $client_id = sanitize_text_field($request->get_param('client_id') ?: '');

        // Preferred bridge: exchange the short-lived one-time code server-to-server.
        // Legacy token/email query parameters remain accepted temporarily for older clients.
        if ($code) {
            $endpoint = $this->get_server_api_endpoint();
            $exchange = wp_remote_post($endpoint . '/v1/auth/browser/exchange', [
                'headers' => ['Content-Type' => 'application/json'],
                'body' => wp_json_encode(['code' => $code, 'purpose' => 'wordpress']),
                'timeout' => 10,
            ]);
            if (is_wp_error($exchange) || wp_remote_retrieve_response_code($exchange) !== 200) {
                wp_redirect(home_url('/') . '?login_error=bridge_exchange_failed');
                exit;
            }
            $payload = json_decode(wp_remote_retrieve_body($exchange), true);
            if (!is_array($payload) || empty($payload['token']) || empty($payload['user_id'])) {
                wp_redirect(home_url('/') . '?login_error=bridge_payload_invalid');
                exit;
            }
            $token = sanitize_text_field($payload['token']);
            $email = sanitize_email($payload['user_id']);
            $tier = sanitize_text_field($payload['tier'] ?? 'free');
            $name = sanitize_text_field($payload['name'] ?? '');
            $client_id = sanitize_text_field($payload['client_id'] ?? '');
        }

        if (!$token || !$email) {
            wp_redirect(home_url('/') . '?login_error=missing_params');
            exit;
        }

        $verified = $this->verify_ailinux_token($email, $token);
        if (!$verified) {
            wp_redirect(home_url('/') . '?login_error=invalid_token');
            exit;
        }

        $user_id = $this->ensure_wp_user($email, $name);
        if (is_wp_error($user_id)) {
            wp_redirect(home_url('/') . '?login_error=user_error');
            exit;
        }

        $verified_tier = $this->extract_ailinux_field($verified, ['tier', 'plan', 'subscription']);
        update_user_meta($user_id, 'nova_tier',          self::normalize_tier_value($verified_tier ?: $tier));
        update_user_meta($user_id, 'nova_session_token', $token);
        update_user_meta($user_id, 'nova_ailinux_email', $email);
        if ($client_id) update_user_meta($user_id, 'nova_client_id', $client_id);

        // Always reset stale WP/auth cookies before issuing a fresh bridge login cookie.
        wp_clear_auth_cookie();
        self::clear_session_cookie();
        wp_set_current_user($user_id);
        wp_set_auth_cookie($user_id, true, is_ssl());

        $safe = (strpos($redirect, home_url()) === 0) ? $redirect : home_url('/');
        wp_redirect($safe);
        exit;
    }

}

// =========================================================================
// Bootstrap
// =========================================================================
add_action('init', function () {
    AuthService::instance();
});
