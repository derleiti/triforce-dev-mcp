<?php
/**
 * Plugin Name: AILinux User Management
 * Plugin URI: https://ailinux.me
 * Description: User-Registrierung, Abo-Verwaltung und API-Zugang für AILinux Clients
 * Version: 1.1.0
 * Author: Markus Leitermann
 * License: GPL v2 or later
 *
 * Features:
 * - User-Registrierung mit TriForce API Sync
 * - Abo-Pläne (Free/Pro/Enterprise)
 * - Stripe/PayPal Integration
 * - Client-Credentials Dashboard
 * - Device-Management
 */

if (!defined('ABSPATH')) exit;

define('AILINUX_VERSION', '1.1.0');
define('AILINUX_API_URL', 'https://api.ailinux.me/v1'); // Legacy fallback; shared TriForce Settings take precedence.
if (!defined('AILINUX_WEBHOOK_SECRET') && getenv('WEBHOOK_SECRET')) { define('AILINUX_WEBHOOK_SECRET', getenv('WEBHOOK_SECRET')); } // Set via wp-config.php or env

class AILinux_User_Plugin {
    
    private static $instance = null;
    
    public static function instance() {
        if (self::$instance === null) {
            self::$instance = new self();
        }
        return self::$instance;
    }
    
    private function __construct() {
        // Hooks registrieren
        add_action('init', [$this, 'init']);
        add_action('user_register', [$this, 'on_user_register']);
        add_action('admin_menu', [$this, 'add_admin_menu']);
        add_action('wp_enqueue_scripts', [$this, 'enqueue_scripts']);

        // AILinux/TriForce ist Master für Account-Login und Passwort-Reset
        add_filter('lostpassword_url', [$this, 'ailinux_lostpassword_url'], 10, 2);
        add_action('login_init', [$this, 'redirect_wp_auth_to_ailinux_login'], 1);
        
        // Shortcodes
        add_shortcode('ailinux_dashboard', [$this, 'render_dashboard']);
        add_shortcode('ailinux_pricing', [$this, 'render_pricing']);
        
        // AJAX Handler
        add_action('wp_ajax_ailinux_register_device', [$this, 'ajax_register_device']);
        add_action('wp_ajax_ailinux_get_credentials', [$this, 'ajax_get_credentials']);
        add_action('wp_ajax_ailinux_sync_settings', [$this, 'ajax_sync_settings']);
        
        // WooCommerce Integration (falls vorhanden)
        if (class_exists('WooCommerce')) {
            add_action('woocommerce_order_status_completed', [$this, 'on_payment_complete']);
            add_action('woocommerce_subscription_status_cancelled', [$this, 'on_subscription_cancelled']);
        }
    }
    
    public function init() {
        // Custom Post Type für Subscription Plans
        register_post_type('ailinux_plan', [
            'labels' => [
                'name' => 'AILinux Pläne',
                'singular_name' => 'Plan',
            ],
            'public' => false,
            'show_ui' => true,
            'menu_icon' => 'dashicons-cloud',
        ]);
    }

    /**
     * AILinux/TriForce ist Master für Account-Passwörter.
     * WordPress-Passwort-Reset wird deshalb auf login.ailinux.me umgeleitet.
     */
    public function ailinux_lostpassword_url($url, $redirect) {
        return 'https://ailinux.me/wp-login.php?action=lostpassword&ailinux_wp_admin=1';
    }

    /**
     * Verhindert, dass User WordPress als Passwort-Master benutzen.
     * Admin-Login bleibt erreichbar mit ?ailinux_wp_admin=1
     */
    public function redirect_wp_auth_to_ailinux_login() {
        $action = isset($_REQUEST['action']) ? sanitize_key($_REQUEST['action']) : 'login';

        // Never redirect the native WordPress password-reset actions. The reset
        // form posts to action=resetpass without our custom bypass parameter;
        // redirecting that POST discards pass1/pass2 and creates an endless loop.
        if (in_array($action, ['lostpassword', 'retrievepassword', 'rp', 'resetpass'], true)) {
            return;
        }

        if (isset($_GET['ailinux_wp_admin']) && $_GET['ailinux_wp_admin'] === '1') {
            // Emergency/admin bypass: stop later login_init redirects from other plugins/themes.
            remove_all_actions('login_init');
            return;
        }

        if ($action === 'login') {
            $login = function_exists('ailinux_triforce_setting') ? ailinux_triforce_setting('login_url', 'https://login.ailinux.me') : 'https://login.ailinux.me';
            wp_redirect($login ?: 'https://login.ailinux.me/');
            exit;
        }
    }
    
    /**
     * Wird aufgerufen wenn sich ein neuer User registriert
     */
    public function on_user_register($user_id) {
        $user = get_userdata($user_id);
        
        // An TriForce API senden
        $response = $this->api_request('webhook/user-created', [
            'event' => 'user_created',
            'user_id' => 'wp_' . $user_id,
            'data' => [
                'email' => $user->user_email,
                'username' => $user->user_login,
                'tier' => 'free',
            ],
            'timestamp' => current_time('c'),
        ]);
        
        if ($response && isset($response['user'])) {
            // Credentials in User Meta speichern
            update_user_meta($user_id, '_ailinux_user_id', $response['user']['user_id']);
            update_user_meta($user_id, '_ailinux_client_id', $response['user']['client_id']);
            update_user_meta($user_id, '_ailinux_client_secret', $response['user']['client_secret']);
            update_user_meta($user_id, '_ailinux_tier', 'free');
            
            // Welcome Email mit Credentials senden
            $this->send_welcome_email($user, $response['user']);
        }
    }
    
    /**
     * Wird aufgerufen bei erfolgreicher Zahlung
     */
    public function on_payment_complete($order_id) {
        $order = wc_get_order($order_id);
        $user_id = $order->get_user_id();
        
        // Tier aus Produkt ermitteln
        $tier = 'pro'; // Default
        foreach ($order->get_items() as $item) {
            $product = $item->get_product();
            $product_tier = $product->get_meta('_ailinux_tier');
            if ($product_tier) {
                $tier = $product_tier;
            }
        }
        
        // An TriForce API senden
        $this->api_request('webhook/payment-success', [
            'event' => 'payment_success',
            'user_id' => 'wp_' . $user_id,
            'data' => [
                'tier' => $tier,
                'amount' => $order->get_total(),
                'currency' => $order->get_currency(),
                'order_id' => $order_id,
            ],
            'timestamp' => current_time('c'),
        ]);
        
        // User Meta aktualisieren
        update_user_meta($user_id, '_ailinux_tier', $tier);
    }
    
    /**
     * Wird aufgerufen bei Abo-Kündigung
     */
    public function on_subscription_cancelled($subscription) {
        $user_id = $subscription->get_user_id();
        
        $this->api_request('webhook/subscription-cancelled', [
            'event' => 'subscription_cancelled',
            'user_id' => 'wp_' . $user_id,
            'data' => [],
            'timestamp' => current_time('c'),
        ]);
        
        update_user_meta($user_id, '_ailinux_tier', 'free');
    }
    
    /**
     * API Request an TriForce Server
     */
    private function api_request($endpoint, $data, $method = 'POST') {
        $base = function_exists('ailinux_triforce_api_v1') ? ailinux_triforce_api_v1(true) : AILINUX_API_URL;
        $url = rtrim($base, '/') . '/' . ltrim($endpoint, '/');
        $signature = $this->generate_signature($data);
        if ($signature === '') {
            error_log('AILinux API request blocked: TriForce webhook secret is not configured');
            return null;
        }
        
        $args = [
            'method' => $method,
            'headers' => [
                'Content-Type' => 'application/json',
                'X-Webhook-Signature' => $signature,
            ],
            'body' => json_encode($data),
            'timeout' => 30,
        ];
        
        $response = wp_remote_request($url, $args);
        
        if (is_wp_error($response)) {
            error_log('AILinux API Error: ' . $response->get_error_message());
            return null;
        }
        
        $body = wp_remote_retrieve_body($response);
        return json_decode($body, true);
    }
    
    /**
     * Generiert Webhook-Signatur
     */
    private function generate_signature($data) {
        $secret = function_exists('ailinux_triforce_setting')
            ? ailinux_triforce_setting('webhook_secret', '')
            : (defined('AILINUX_WEBHOOK_SECRET') ? AILINUX_WEBHOOK_SECRET : (getenv('WEBHOOK_SECRET') ?: ''));
        if ($secret === '') { return ''; }
        return hash_hmac('sha256', wp_json_encode($data), $secret);
    }
    
    /**
     * Sendet Welcome Email mit Credentials
     */
    private function send_welcome_email($user, $credentials) {
        $subject = 'Willkommen bei AILinux! Deine Zugangsdaten';
        
        $message = "Hallo {$user->display_name}!\n\n";
        $message .= "Willkommen bei AILinux. Hier sind deine Zugangsdaten für den Desktop-Client:\n\n";
        $message .= "Client-ID: {$credentials['client_id']}\n";
        $message .= "Client-Secret: {$credentials['client_secret']}\n\n";
        $message .= "WICHTIG: Bewahre diese Daten sicher auf! Das Secret wird nur einmal angezeigt.\n\n";
        $message .= "Download Desktop-Client: https://ailinux.me/download\n";
        $message .= "Dokumentation: https://ailinux.me/docs\n\n";
        $message .= "Bei Fragen: support@ailinux.me\n\n";
        $message .= "Viel Erfolg!\nDein AILinux Team";
        
        wp_mail($user->user_email, $subject, $message);
    }
    
    /**
     * Admin Menu
     */
    public function add_admin_menu() {
        add_menu_page(
            'AILinux',
            'AILinux',
            'manage_options',
            'ailinux',
            [$this, 'render_admin_page'],
            'dashicons-cloud',
            30
        );
        
        add_submenu_page(
            'ailinux',
            'Benutzer',
            'Benutzer',
            'manage_options',
            'ailinux-users',
            [$this, 'render_users_page']
        );
        
        add_submenu_page(
            'ailinux',
            'Statistiken',
            'Statistiken',
            'manage_options',
            'ailinux-stats',
            [$this, 'render_stats_page']
        );
    }
    
    /**
     * Frontend Scripts
     */
    public function enqueue_scripts() {
        if (is_user_logged_in()) {
            wp_enqueue_script(
                'ailinux-dashboard',
                plugin_dir_url(__FILE__) . 'js/dashboard.js',
                ['jquery'],
                AILINUX_VERSION,
                true
            );
            
            wp_localize_script('ailinux-dashboard', 'ailinux', [
                'ajax_url' => admin_url('admin-ajax.php'),
                'nonce' => wp_create_nonce('ailinux_nonce'),
            ]);
        }
    }
    
    /**
     * Shortcode: Dashboard für eingeloggte User
     */
    public function render_dashboard($atts) {
        if (!is_user_logged_in()) {
            return '<p>Bitte <a href="https://login.ailinux.me/">mit deinem AILinux Account einloggen</a> um das Dashboard zu sehen.</p>';
        }
        
        $user_id = get_current_user_id();
        $client_id = get_user_meta($user_id, '_ailinux_client_id', true);
        $tier = get_user_meta($user_id, '_ailinux_tier', true) ?: 'free';
        
        ob_start();
        ?>
        <div class="ailinux-dashboard">
            <h2>AILinux Dashboard</h2>
            
            <div class="ailinux-card">
                <h3>Dein Plan: <span class="tier-badge tier-<?php echo esc_attr($tier); ?>"><?php echo ucfirst($tier); ?></span></h3>
                <?php if ($tier === 'free'): ?>
                    <p>Upgrade auf Pro für Cloud-Modelle und mehr!</p>
                    <a href="/pricing" class="button">Upgrade</a>
                <?php endif; ?>
            </div>
            
            <div class="ailinux-card">
                <h3>API Zugangsdaten</h3>
                <p><strong>Client-ID:</strong> <code><?php echo esc_html($client_id); ?></code></p>
                <p><strong>Client-Secret:</strong> <code>********</code> 
                    <button class="button" onclick="ailinuxShowSecret()">Anzeigen</button>
                </p>
                <p class="description">Diese Daten brauchst du für den Desktop-Client.</p>
            </div>
            
            <div class="ailinux-card">
                <h3>Registrierte Geräte</h3>
                <div id="ailinux-devices-list">Lädt...</div>
                <button class="button" onclick="ailinuxRegisterDevice()">Neues Gerät registrieren</button>
            </div>
            
            <div class="ailinux-card">
                <h3>Download</h3>
                <p><a href="/download/ailinux-desktop-linux.deb" class="button button-primary">Linux (.deb)</a></p>
                <p><a href="/download/ailinux-desktop-windows.exe" class="button">Windows (.exe)</a></p>
            </div>
        </div>
        <?php
        return ob_get_clean();
    }
    
    /**
     * Shortcode: Pricing Table
     */
    public function render_pricing($atts) {
        ob_start();
        ?>
        <div class="ailinux-pricing">
            <div class="pricing-card">
                <h3>Free</h3>
                <div class="price">0€ <span>/Monat</span></div>
                <ul>
                    <li>✓ 100 Anfragen/Tag</li>
                    <li>✓ Lokale Ollama-Modelle</li>
                    <li>✓ 2 Geräte</li>
                    <li>✗ Cloud-Modelle (Claude, GPT)</li>
                    <li>✗ Settings-Sync</li>
                </ul>
                <a href="/register" class="button">Kostenlos starten</a>
            </div>
            
            <div class="pricing-card featured">
                <h3>Pro</h3>
                <div class="price">9,99€ <span>/Monat</span></div>
                <ul>
                    <li>✓ 1.000 Anfragen/Tag</li>
                    <li>✓ Alle lokalen Modelle</li>
                    <li>✓ <strong>Cloud-Modelle (Claude, GPT, Gemini)</strong></li>
                    <li>✓ 5 Geräte</li>
                    <li>✓ Settings-Sync</li>
                    <li>✓ Priority Support</li>
                </ul>
                <a href="/checkout?plan=pro" class="button button-primary">Pro wählen</a>
            </div>
            
            <div class="pricing-card">
                <h3>Enterprise</h3>
                <div class="price">49€ <span>/Monat</span></div>
                <ul>
                    <li>✓ Unlimited Anfragen</li>
                    <li>✓ Alle Modelle</li>
                    <li>✓ 50 Geräte</li>
                    <li>✓ Priority Queue</li>
                    <li>✓ Eigene API Keys</li>
                    <li>✓ Dedizierter Support</li>
                </ul>
                <a href="/contact" class="button">Kontakt</a>
            </div>
        </div>
        <?php
        return ob_get_clean();
    }
    
    /**
     * AJAX: Gerät registrieren
     */
    public function ajax_register_device() {
        check_ajax_referer('ailinux_nonce', 'nonce');
        
        $user_id = get_current_user_id();
        $device_name = sanitize_text_field($_POST['device_name']);
        $device_type = sanitize_text_field($_POST['device_type']);
        
        $ailinux_user_id = get_user_meta($user_id, '_ailinux_user_id', true);
        
        $response = $this->api_request("users/{$ailinux_user_id}/devices", [
            'user_id' => $ailinux_user_id,
            'device_name' => $device_name,
            'device_type' => $device_type,
        ]);
        
        wp_send_json($response);
    }
    
    /**
     * Admin Page
     */
    public function render_admin_page() {
        // API Stats holen
        $stats = $this->api_request('admin/stats', [], 'GET');
        ?>
        <div class="wrap">
            <h1>AILinux Dashboard</h1>
            
            <div class="ailinux-admin-stats">
                <div class="stat-card">
                    <h3>Benutzer gesamt</h3>
                    <div class="stat-value"><?php echo $stats['total_users'] ?? 0; ?></div>
                </div>
                <div class="stat-card">
                    <h3>Pro Abos</h3>
                    <div class="stat-value"><?php echo $stats['by_tier']['pro'] ?? 0; ?></div>
                </div>
                <div class="stat-card">
                    <h3>Enterprise</h3>
                    <div class="stat-value"><?php echo $stats['by_tier']['enterprise'] ?? 0; ?></div>
                </div>
                <div class="stat-card">
                    <h3>Geräte</h3>
                    <div class="stat-value"><?php echo $stats['total_devices'] ?? 0; ?></div>
                </div>
            </div>
            
            <h2>API Status</h2>
            <p>Server: <code><?php echo esc_html(function_exists('ailinux_triforce_api_v1') ? ailinux_triforce_api_v1(true) : AILINUX_API_URL); ?></code></p>
        </div>
        <?php
    }
    
    public function render_users_page() {
        echo '<div class="wrap"><h1>AILinux Benutzer</h1><p>Benutzerliste wird geladen...</p></div>';
    }
    
    public function render_stats_page() {
        echo '<div class="wrap"><h1>AILinux Statistiken</h1><p>Statistiken werden geladen...</p></div>';
    }
}

// Plugin initialisieren
add_action('plugins_loaded', ['AILinux_User_Plugin', 'instance']);

// Aktivierung
register_activation_hook(__FILE__, function() {
    // Flush rewrite rules
    flush_rewrite_rules();
});

// Deaktivierung
register_deactivation_hook(__FILE__, function() {
    flush_rewrite_rules();
});

// Bootstrap plugin
AILinux_User_Plugin::instance();


