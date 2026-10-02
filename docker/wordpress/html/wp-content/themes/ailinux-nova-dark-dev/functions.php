<?php
/**
 * Theme functions
 *
 * @package Ailinux_Nova_Dark
 */

// Load version from style.css to avoid early wp_get_theme() call
if ( ! defined( 'AILINUX_NOVA_DARK_VERSION' ) ) {
    $theme_version = '1.0.0';
    $style_css = get_template_directory() . '/style.css';
    if ( file_exists( $style_css ) ) {
        $theme_data = get_file_data( $style_css, array( 'Version' => 'Version' ) );
        if ( ! empty( $theme_data['Version'] ) ) {
            $theme_version = $theme_data['Version'];
        }
    }
    define( 'AILINUX_NOVA_DARK_VERSION', $theme_version );
}

define( 'AILINUX_NOVA_DARK_DIR', get_template_directory() );
define( 'AILINUX_NOVA_DARK_URI', get_template_directory_uri() );

// Load CSS++ Integration (Optional Enhancement Layer)
if ( file_exists( AILINUX_NOVA_DARK_DIR . '/inc/csspp-integration.php' ) ) {
	require_once AILINUX_NOVA_DARK_DIR . '/inc/csspp-integration.php';
}

if ( ! function_exists( 'ailinux_nova_dark_setup' ) ) {
    function ailinux_nova_dark_setup() {
        load_theme_textdomain( 'ailinux-nova-dark', AILINUX_NOVA_DARK_DIR . '/languages' );

        add_theme_support( 'automatic-feed-links' );
        add_theme_support( 'title-tag' );
        add_theme_support( 'post-thumbnails' );
        add_theme_support( 'responsive-embeds' );
        add_theme_support( 'html5', [
            'comment-form',
            'comment-list',
            'gallery',
            'caption',
            'style',
            'script',
            'navigation-widgets',
        ] );
        add_theme_support( 'align-wide' );
        add_theme_support( 'editor-styles' );
        add_theme_support( 'custom-logo', [
            'height'      => 80,
            'width'       => 240,
            'flex-height' => true,
            'flex-width'  => true,
        ] );

        register_nav_menus( [
            'primary' => __( 'Primary Menu', 'ailinux-nova-dark' ),
            'footer'  => __( 'Footer Menu', 'ailinux-nova-dark' ),
        ] );

        add_image_size( 'ailinux-hero', 1920, 1080, true );
        add_image_size( 'ailinux-card', 1200, 675, true );

        add_editor_style( 'editor-styles.css' );
    }
}
add_action( 'after_setup_theme', 'ailinux_nova_dark_setup' );


/**
 * Public pages use English as their canonical source language.
 * GTranslate owns the visitor-selected language via its googtrans cookie/UI.
 */
function ailinux_nova_dark_frontend_source_locale( $locale ) {
    if ( is_admin() || wp_doing_ajax() || ( defined( 'REST_REQUEST' ) && REST_REQUEST ) ) {
        return $locale;
    }

    return 'en_US';
}
add_filter( 'determine_locale', 'ailinux_nova_dark_frontend_source_locale', 100 );
add_filter( 'locale', 'ailinux_nova_dark_frontend_source_locale', 100 );

function ailinux_nova_dark_widgets_init() {
    register_sidebar( [
        'name'          => __( 'Sidebar', 'ailinux-nova-dark' ),
        'id'            => 'sidebar-1',
        'description'   => __( 'Optional sidebar for widgets.', 'ailinux-nova-dark' ),
        'before_widget' => '<section id="%1$s" class="widget %2$s">',
        'after_widget'  => '</section>',
        'before_title'  => '<h3 class="widget-title">',
        'after_title'   => '</h3>',
    ] );

    register_sidebar( [
        'name'          => __( 'Footer Widgets', 'ailinux-nova-dark' ),
        'id'            => 'footer-widgets',
        'description'   => __( 'Widgets added here will appear in the footer.', 'ailinux-nova-dark' ),
        'before_widget' => '<div id="%1$s" class="widget %2$s">',
        'after_widget'  => '</div>',
        'before_title'  => '<h2 class="widget-title footer-title">',
        'after_title'   => '</h2>',
    ] );
}
add_action( 'widgets_init', 'ailinux_nova_dark_widgets_init' );

function ailinux_nova_dark_get_asset_version( $relative_path ) {
    $file = AILINUX_NOVA_DARK_DIR . $relative_path;

    return file_exists( $file ) ? filemtime( $file ) : AILINUX_NOVA_DARK_VERSION;
}

/**
 * Früh laden: Color-Mode im HEAD, um FOUC zu vermeiden.
 * Lädt dist/colorMode.js (aus Vite-Eintrag assets/js/color-mode.js).
 */
add_action('wp_enqueue_scripts', function () {
    // Color-Mode MUSS im HEAD, nicht im Footer.
    wp_enqueue_script(
        'ailinux-nova-dark-color-mode',
        AILINUX_NOVA_DARK_URI . '/dist/colorMode.js',
        [],
        ailinux_nova_dark_get_asset_version('/dist/colorMode.js'),
        false // HEAD!
    );
}, 1); // höchste Priorität

/**
 * Kern-Skripte und Styles laden.
 * Lädt app.js, mobile-menu.js und die Haupt-Stylesheets.
 * Injiziert außerdem die API-Basis via wp_localize_script.
 */
function ailinux_nova_dark_enqueue_assets() {
    wp_enqueue_style(
        'ailinux-nova-dark-style',
        AILINUX_NOVA_DARK_URI . '/dist/style.css',
        [],
        ailinux_nova_dark_get_asset_version( '/dist/style.css' )
    );

    wp_enqueue_style(
        'ailinux-nova-dark-fonts',
        'https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap',
        [],
        null
    );

    wp_enqueue_style(
        'ailinux-nova-dark-ai-panel-fixes',
        AILINUX_NOVA_DARK_URI . '/css/ai-panel-fixes.css',
        ['ailinux-nova-dark-style'],
        ailinux_nova_dark_get_asset_version( '/css/ai-panel-fixes.css' )
    );

    // Nova AI Plugin Bridge CSS - Theme/Plugin Integration
    wp_enqueue_style(
        'ailinux-nova-plugin-bridge',
        AILINUX_NOVA_DARK_URI . '/css/nova-plugin-bridge.css',
        ['ailinux-nova-dark-style', 'nova-ai-frontend'],
        ailinux_nova_dark_get_asset_version( '/css/nova-plugin-bridge.css' )
    );


    // Hauptbundle NUR EINMAL laden
    wp_enqueue_script(
        'ailinux-nova-dark-app',
        AILINUX_NOVA_DARK_URI . '/dist/app.js',
        [],
        ailinux_nova_dark_get_asset_version('/dist/app.js'),
        true
    );

     wp_enqueue_script(
         'ailinux-nova-dark-mobile-menu',
         AILINUX_NOVA_DARK_URI . '/dist/mobile-menu.js',
         [],
         ailinux_nova_dark_get_asset_version( '/dist/mobile-menu.js' ),
         true
     );

     // webgpu.js dequeued: ES module (export statement) breaks Rocket Loader,
     // and the module is not referenced anywhere in the active theme.
     // wp_dequeue_script( 'ailinux-nova-dark-webgpu' ); // already not enqueued

    // API-Basis konfigurierbar (Customizer) + für JS verfügbar machen
    $api_base = trim(get_theme_mod('ailinux_nova_dark_api_base', 'https://api.ailinux.me'));
    if ( empty( $api_base ) ) {
        $api_base = 'https://api.ailinux.me';
    }
    $api_base = rtrim( $api_base, '/' );
    $parsed_base = wp_parse_url( $api_base );
    if ( $parsed_base && ! empty( $parsed_base['host'] ) ) {
        $host = $parsed_base['host'];
        $scheme = $parsed_base['scheme'] ?? 'http';
        if ( $scheme == 'https' && in_array( $host, ['localhost', '127.0.0.1', 'host.docker.internal'], true ) ) {
            $port = isset( $parsed_base['port'] ) ? ':' . $parsed_base['port'] : '';
            $path = $parsed_base['path'] ?? '';
            $api_base = 'http://' . $host . $port . $path;
        }
    }
    $default_model = get_theme_mod( 'ailinux_nova_dark_default_model', 'llama4:latest' );

    // Pass WP login state to JS
    $current_user = wp_get_current_user();
    $wp_logged_in = is_user_logged_in();
    wp_localize_script('ailinux-nova-dark-app', 'AILINUX_USER', [
        'loggedIn'    => $wp_logged_in,
        'displayName' => $wp_logged_in ? $current_user->display_name : '',
        'email'       => $wp_logged_in ? $current_user->user_email : '',
        'isAdmin'     => $wp_logged_in && current_user_can('manage_options'),
        'logoutUrl'   => wp_logout_url(home_url()),
    ]);

        wp_localize_script('ailinux-nova-dark-app', 'NOVA_API', [
        'DISABLED'       => false,
        'BASE'           => $api_base,
        'CHAT_ENDPOINT'  => '/v1/chat',
        'MODELS_ENDPOINT'=> 'https://ailinux.me/wp-json/nova-ai/v1/models',
        'HEALTH_ENDPOINT'=> '/health',
        'DEFAULT_MODEL'  => 'gemini/gemini-2.5-flash',
    ]);

    wp_localize_script( 'ailinux-nova-dark-app', 'AILinuxNova', [
        'accent'       => get_theme_mod( 'ailinux_nova_dark_accent', 'accent-blue' ),
        'heroLayout'   => get_theme_mod( 'ailinux_nova_dark_hero_layout', 'grid' ),
        'cardDensity'  => get_theme_mod( 'ailinux_nova_dark_card_density', 'airy' ),
        'scrollOffset' => 92,
    ] );

    // Add AI context for single posts
    if ( is_singular( 'post' ) ) {
        global $post;
        $title = get_the_title( $post );
        $excerpt = has_excerpt( $post ) ? get_the_excerpt( $post ) : wp_trim_words( $post->post_content, 50 );
        $context_prompt = sprintf(
            __( 'Discuss this post: "%s" - %s', 'ailinux-nova-dark' ),
            $title,
            wp_strip_all_tags( $excerpt )
        );

        wp_localize_script( 'ailinux-nova-dark-app', 'AIContext', [
            'contextPrompt' => $context_prompt,
            'postTitle'     => $title,
            'postExcerpt'   => wp_strip_all_tags( $excerpt ),
        ] );
    }
}
add_action( 'wp_enqueue_scripts', 'ailinux_nova_dark_enqueue_assets', 20 );

// Schnellere Schrift-Lieferung & statische Ressourcenhinweise
add_filter( 'wp_resource_hints', function( $hints, $relation_type ) {
    if ( 'preconnect' === $relation_type ) {
        $hints[] = 'https://fonts.googleapis.com';
        $hints[] = 'https://fonts.gstatic.com';
    }
    return $hints;
}, 10, 2 );

// FIX 2026-04-11: Duplicate style.css enqueue removed (already loaded at priority 20)
// Preload hint instead of second enqueue:
add_action('wp_head', function() {
    echo '<link rel="preload" href="' . esc_url(AILINUX_NOVA_DARK_URI . '/dist/style.css') . '" as="style">' . "\n";
}, 1);

function ailinux_nova_dark_disable_wpemoji() {
    remove_action( 'wp_head', 'print_emoji_detection_script', 7 );
    remove_action( 'wp_print_styles', 'print_emoji_styles' );
}
add_action( 'init', 'ailinux_nova_dark_disable_wpemoji' );

function ailinux_nova_dark_body_classes( $classes ) {
    $classes[] = get_theme_mod( 'ailinux_nova_dark_accent', 'accent-blue' );
    $classes[] = 'hero-layout-' . get_theme_mod( 'ailinux_nova_dark_hero_layout', 'grid' );
    $classes[] = 'card-density-' . get_theme_mod( 'ailinux_nova_dark_card_density', 'airy' );

    if ( is_front_page() || is_home() ) {
        $classes[] = 'has-hero-section';
    }

    if ( ! get_theme_mod( 'ailinux_nova_dark_header_sticky', true ) ) {
        $classes[] = 'no-sticky-header';
    }

    return $classes;
}
add_filter( 'body_class', 'ailinux_nova_dark_body_classes' );

function ailinux_nova_dark_skip_link() {
    echo '<a class="skip-link" href="#content">' . esc_html__( 'Skip to content', 'ailinux-nova-dark' ) . '</a>';
}
add_action( 'wp_body_open', 'ailinux_nova_dark_skip_link', 5 );

function ailinux_nova_dark_customize_register( $wp_customize ) {
    // Theme Options Section
    $wp_customize->add_section( 'ailinux_nova_dark_options', [
        'title'       => __( 'Theme Options', 'ailinux-nova-dark' ),
        'description' => __( 'Passe Akzentfarben und Layout-Einstellungen an.', 'ailinux-nova-dark' ),
        'priority'    => 30,
    ] );

    // Accent Color
    $wp_customize->add_setting( 'ailinux_nova_dark_accent', [
        'default'           => 'accent-blue',
        'sanitize_callback' => function ( $value ) {
            return in_array( $value, [ 'accent-blue', 'accent-green' ], true ) ? $value : 'accent-blue';
        },
    ] );
    $wp_customize->add_control( 'ailinux_nova_dark_accent', [
        'label'   => __( 'Primary Accent', 'ailinux-nova-dark' ),
        'section' => 'ailinux_nova_dark_options',
        'type'    => 'radio',
        'choices' => [
            'accent-blue'  => __( 'Blue', 'ailinux-nova-dark' ),
            'accent-green' => __( 'Green', 'ailinux-nova-dark' ),
        ],
    ] );

    // Hero Layout
    $wp_customize->add_setting( 'ailinux_nova_dark_hero_layout', [
        'default'           => 'grid',
        'sanitize_callback' => function ( $value ) {
            return in_array( $value, [ 'grid', 'list' ], true ) ? $value : 'grid';
        },
    ] );
    $wp_customize->add_control( 'ailinux_nova_dark_hero_layout', [
        'label'   => __( 'Hero Layout', 'ailinux-nova-dark' ),
        'section' => 'ailinux_nova_dark_options',
        'type'    => 'radio',
        'choices' => [ 'grid' => 'Grid', 'list' => 'List' ],
    ] );

    // Card Density
    $wp_customize->add_setting( 'ailinux_nova_dark_card_density', [
        'default'           => 'airy',
        'sanitize_callback' => function ( $value ) {
            return in_array( $value, [ 'airy', 'compact' ], true ) ? $value : 'airy';
        },
    ] );
    $wp_customize->add_control( 'ailinux_nova_dark_card_density', [
        'label'   => __( 'Blog Card Density', 'ailinux-nova-dark' ),
        'section' => 'ailinux_nova_dark_options',
        'type'    => 'radio',
        'choices' => [ 'airy' => 'Airy', 'compact' => 'Compact' ],
    ] );

    // Header Section
    $wp_customize->add_section( 'ailinux_nova_dark_header_options', [
        'title'    => __( 'Header', 'ailinux-nova-dark' ),
        'priority' => 35,
    ] );

    $wp_customize->add_setting( 'ailinux_nova_dark_header_sticky', [
        'default'           => true,
        'sanitize_callback' => 'rest_sanitize_boolean',
    ] );
    $wp_customize->add_control( 'ailinux_nova_dark_header_sticky', [
        'label'   => __( 'Sticky Header', 'ailinux-nova-dark' ),
        'section' => 'ailinux_nova_dark_header_options',
        'type'    => 'checkbox',
    ] );

    $wp_customize->add_setting( 'ailinux_nova_dark_header_bg_color', [
        'default'           => '',
        'sanitize_callback' => 'sanitize_hex_color',
        'transport'         => 'postMessage',
    ] );
    $wp_customize->add_control( new WP_Customize_Color_Control( $wp_customize, 'ailinux_nova_dark_header_bg_color', [
        'label'    => __( 'Header Background Color', 'ailinux-nova-dark' ),
        'section'  => 'ailinux_nova_dark_header_options',
    ] ) );

    $wp_customize->add_setting( 'ailinux_nova_dark_header_text_color', [
        'default'           => '',
        'sanitize_callback' => 'sanitize_hex_color',
        'transport'         => 'postMessage',
    ] );
    $wp_customize->add_control( new WP_Customize_Color_Control( $wp_customize, 'ailinux_nova_dark_header_text_color', [
        'label'    => __( 'Header Text Color', 'ailinux-nova-dark' ),
        'section'  => 'ailinux_nova_dark_header_options',
    ] ) );

    // Footer Section
    $wp_customize->add_section( 'ailinux_nova_dark_footer_options', [
        'title'    => __( 'Footer', 'ailinux-nova-dark' ),
        'priority' => 36,
    ] );

    $wp_customize->add_setting( 'ailinux_nova_dark_footer_bg_color', [
        'default'           => '',
        'sanitize_callback' => 'sanitize_hex_color',
        'transport'         => 'postMessage',
    ] );
    $wp_customize->add_control( new WP_Customize_Color_Control( $wp_customize, 'ailinux_nova_dark_footer_bg_color', [
        'label'    => __( 'Footer Background Color', 'ailinux-nova-dark' ),
        'section'  => 'ailinux_nova_dark_footer_options',
    ] ) );

    $wp_customize->add_setting( 'ailinux_nova_dark_footer_text_color', [
        'default'           => '',
        'sanitize_callback' => 'sanitize_hex_color',
        'transport'         => 'postMessage',
    ] );
    $wp_customize->add_control( new WP_Customize_Color_Control( $wp_customize, 'ailinux_nova_dark_footer_text_color', [
        'label'    => __( 'Footer Text Color', 'ailinux-nova-dark' ),
        'section'  => 'ailinux_nova_dark_footer_options',
    ] ) );

    $wp_customize->add_setting( 'ailinux_nova_dark_copyright_text', [
        'default'           => sprintf( '&copy; %s %s. %s', date_i18n( 'Y' ), get_bloginfo( 'name' ), __( 'All rights reserved.', 'ailinux-nova-dark' ) ),
        'sanitize_callback' => 'wp_kses_post',
    ] );
    $wp_customize->add_control( 'ailinux_nova_dark_copyright_text', [
        'label'    => __( 'Copyright Text', 'ailinux-nova-dark' ),
        'section'  => 'ailinux_nova_dark_footer_options',
        'type'     => 'textarea',
    ] );

    // Colors Section
    $wp_customize->add_section( 'ailinux_nova_dark_colors', [
        'title'    => __( 'Colors', 'ailinux-nova-dark' ),
        'priority' => 40,
    ] );

    $colors = [
        // Dark Mode
        'ailinux_nova_dark_color_bg_0_dark'   => [ 'label' => __( 'BG 0 (Dark)', 'ailinux-nova-dark' ), 'default' => '#0e1116' ],
        'ailinux_nova_dark_color_bg_1_dark'   => [ 'label' => __( 'BG 1 (Dark)', 'ailinux-nova-dark' ), 'default' => '#131822' ],
        'ailinux_nova_dark_color_bg_2_dark'   => [ 'label' => __( 'BG 2 (Dark)', 'ailinux-nova-dark' ), 'default' => '#1b2330' ],
        'ailinux_nova_dark_color_text_dark'   => [ 'label' => __( 'Text (Dark)', 'ailinux-nova-dark' ), 'default' => '#e8edf2' ],
        'ailinux_nova_dark_color_muted_dark'  => [ 'label' => __( 'Muted (Dark)', 'ailinux-nova-dark' ), 'default' => '#a9b3c0' ],
        // Light Mode
        'ailinux_nova_dark_color_bg_0_light'  => [ 'label' => __( 'BG 0 (Light)', 'ailinux-nova-dark' ), 'default' => '#f5f7fb' ],
        'ailinux_nova_dark_color_bg_1_light'  => [ 'label' => __( 'BG 1 (Light)', 'ailinux-nova-dark' ), 'default' => '#ffffff' ],
        'ailinux_nova_dark_color_bg_2_light'  => [ 'label' => __( 'BG 2 (Light)', 'ailinux-nova-dark' ), 'default' => '#f0f4ff' ],
        'ailinux_nova_dark_color_text_light'  => [ 'label' => __( 'Text (Light)', 'ailinux-nova-dark' ), 'default' => '#0f141b' ],
        'ailinux_nova_dark_color_muted_light' => [ 'label' => __( 'Muted (Light)', 'ailinux-nova-dark' ), 'default' => '#4b5565' ],
    ];

    foreach ( $colors as $setting_id => $options ) {
        $wp_customize->add_setting( $setting_id, [
            'default'           => $options['default'],
            'sanitize_callback' => 'sanitize_hex_color',
            'transport'         => 'postMessage',
        ] );
        $wp_customize->add_control( new WP_Customize_Color_Control( $wp_customize, $setting_id, [
            'label'    => $options['label'],
            'section'  => 'ailinux_nova_dark_colors',
            'settings' => $setting_id,
        ] ) );
    }

    // Typography Section
    $wp_customize->add_section( 'ailinux_nova_dark_typography', [
        'title'    => __( 'Typography', 'ailinux-nova-dark' ),
        'priority' => 50,
    ] );

    $wp_customize->add_setting( 'ailinux_nova_dark_font_sans', [
        'default'           => 'Inter',
        'sanitize_callback' => 'sanitize_text_field',
    ] );
    $wp_customize->add_control( 'ailinux_nova_dark_font_sans', [
        'label'   => __( 'Sans-serif Font Family', 'ailinux-nova-dark' ),
        'section' => 'ailinux_nova_dark_typography',
        'type'    => 'text',
    ] );

    $wp_customize->add_setting( 'ailinux_nova_dark_font_mono', [
        'default'           => 'JetBrains Mono',
        'sanitize_callback' => 'sanitize_text_field',
    ] );
    $wp_customize->add_control( 'ailinux_nova_dark_font_mono', [
        'label'   => __( 'Monospace Font Family', 'ailinux-nova-dark' ),
        'section' => 'ailinux_nova_dark_typography',
        'type'    => 'text',
    ] );

    // API Section
    $wp_customize->add_section( 'ailinux_nova_dark_api', [
        'title'    => __( 'API Settings', 'ailinux-nova-dark' ),
        'priority' => 60,
    ] );

    $wp_customize->add_setting( 'ailinux_nova_dark_api_base', [
        'default'           => 'https://api.ailinux.me',
        'sanitize_callback' => 'esc_url_raw',
    ] );
    $wp_customize->add_control( 'ailinux_nova_dark_api_base', [
        'label'       => __( 'API Base URL', 'ailinux-nova-dark' ),
        'description' => __( 'Backend API endpoint (HTTPS required)', 'ailinux-nova-dark' ),
        'section'     => 'ailinux_nova_dark_api',
        'type'        => 'url',
    ] );

    $wp_customize->add_setting( 'ailinux_nova_dark_default_model', [
        'default'           => 'llama4:latest',
        'sanitize_callback' => 'sanitize_text_field',
    ] );
    $wp_customize->add_control( 'ailinux_nova_dark_default_model', [
        'label'       => __( 'Default AI Model', 'ailinux-nova-dark' ),
        'description' => __( 'Model identifier (e.g., llama4:latest)', 'ailinux-nova-dark' ),
        'section'     => 'ailinux_nova_dark_api',
        'type'        => 'text',
    ] );
}
add_action( 'customize_register', 'ailinux_nova_dark_customize_register' );

function ailinux_nova_dark_get_customizer_css() {
    ob_start();

    $colors = [
        '--bg-0'   => get_theme_mod( 'ailinux_nova_dark_color_bg_0_dark', '#0e1116' ),
        '--bg-1'   => get_theme_mod( 'ailinux_nova_dark_color_bg_1_dark', '#131822' ),
        '--bg-2'   => get_theme_mod( 'ailinux_nova_dark_color_bg_2_dark', '#1b2330' ),
        '--text'   => get_theme_mod( 'ailinux_nova_dark_color_text_dark', '#e8edf2' ),
        '--muted'  => get_theme_mod( 'ailinux_nova_dark_color_muted_dark', '#a9b3c0' ),
    ];

    $light_colors = [
        '--bg-0'  => get_theme_mod( 'ailinux_nova_dark_color_bg_0_light', '#f5f7fb' ),
        '--bg-1'  => get_theme_mod( 'ailinux_nova_dark_color_bg_1_light', '#ffffff' ),
        '--bg-2'  => get_theme_mod( 'ailinux_nova_dark_color_bg_2_light', '#f0f4ff' ),
        '--text'  => get_theme_mod( 'ailinux_nova_dark_color_text_light', '#0f141b' ),
        '--muted' => get_theme_mod( 'ailinux_nova_dark_color_muted_light', '#4b5565' ),
    ];

    $font_sans = get_theme_mod( 'ailinux_nova_dark_font_sans', 'Inter' );
    $font_mono = get_theme_mod( 'ailinux_nova_dark_font_mono', 'JetBrains Mono' );

    $header_bg_color = get_theme_mod( 'ailinux_nova_dark_header_bg_color' );
    $header_text_color = get_theme_mod( 'ailinux_nova_dark_header_text_color' );
    $footer_bg_color = get_theme_mod( 'ailinux_nova_dark_footer_bg_color' );
    $footer_text_color = get_theme_mod( 'ailinux_nova_dark_footer_text_color' );

    ?>
    :root {
        <?php foreach ( $colors as $variable => $value ) : ?>
            <?php echo esc_attr( $variable ); ?>: <?php echo esc_attr( $value ); ?>;
        <?php endforeach; ?>
        --font-sans: '<?php echo esc_attr( $font_sans ); ?>', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
        --font-mono: '<?php echo esc_attr( $font_mono ); ?>', 'Fira Code', ui-monospace, SFMono-Regular, monospace;
    }

    html[data-theme='light'] {
        <?php foreach ( $light_colors as $variable => $value ) : ?>
            <?php echo esc_attr( $variable ); ?>: <?php echo esc_attr( $value ); ?>;
        <?php endforeach; ?>
    }

    <?php if ( $header_bg_color ) : ?>
    html[data-theme='dark'] .site-header {
        background-color: <?php echo esc_attr( $header_bg_color ); ?>;
    }
    <?php endif; ?>

    <?php if ( $header_text_color ) : ?>
    html[data-theme='dark'] .site-header .site-title,
    html[data-theme='dark'] .site-header .site-description,
    html[data-theme='dark'] .site-header .site-nav a,
    html[data-theme='dark'] .site-header .utility-link {
        color: <?php echo esc_attr( $header_text_color ); ?>;
    }
    <?php endif; ?>

    <?php if ( $footer_bg_color ) : ?>
    .site-footer {
        background-color: <?php echo esc_attr( $footer_bg_color ); ?>;
    }
    <?php endif; ?>

    <?php if ( $footer_text_color ) : ?>
    .site-footer .footer-copy,
    .site-footer .footer-links a,
    .site-footer .footer-social a,
    .site-footer .footer-bottom p {
        color: <?php echo esc_attr( $footer_text_color ); ?>;
    }
    <?php endif; ?>

    <?php

    return ob_get_clean();
}

function ailinux_nova_dark_print_customizer_css() {
    $css = ailinux_nova_dark_get_customizer_css();
    if ( ! empty( $css ) ) {
        echo "<!-- Customizer CSS: Text Light = " . esc_html( get_theme_mod( 'ailinux_nova_dark_color_text_light', 'NOT SET' ) ) . " -->\n";
        echo "<style id='ailinux-customizer-css'>\n" . $css . "\n</style>\n";
    }
}
add_action( 'wp_head', 'ailinux_nova_dark_print_customizer_css', 100 );


function ailinux_nova_dark_render_meta_tags() {
    if ( is_singular() ) {
        global $post;
        $description = has_excerpt( $post ) ? wp_strip_all_tags( get_the_excerpt( $post ) ) : wp_strip_all_tags( wp_trim_words( $post->post_content, 36 ) );
        $image_id    = get_post_thumbnail_id( $post );
        $image_url   = $image_id ? wp_get_attachment_image_url( $image_id, 'full' ) : ( get_site_icon_url() ?: '' );

        echo '<meta property="og:type" content="article" />' . "\n";
        echo '<meta property="og:title" content="' . esc_attr( get_the_title( $post ) ) . '" />' . "\n";
        echo '<meta property="og:description" content="' . esc_attr( $description ) . '" />' . "\n";
        echo '<meta property="og:url" content="' . esc_url( get_permalink( $post ) ) . '" />' . "\n";
        echo '<meta property="og:image" content="' . esc_url( $image_url ) . '" />' . "\n";
        echo '<meta name="twitter:card" content="summary_large_image" />' . "\n";
    }

    if ( is_home() || is_front_page() ) {
        echo '<meta property="og:type" content="website" />' . "\n";
        echo '<meta property="og:title" content="' . esc_attr( get_bloginfo( 'name' ) ) . '" />' . "\n";
        echo '<meta property="og:description" content="' . esc_attr( get_bloginfo( 'description' ) ) . '" />' . "\n";
        echo '<meta property="og:url" content="' . esc_url( home_url() ) . '" />' . "\n";
    }
}
// DISABLED: SEOPress handles OG tags
// add_action( 'wp_head', 'ailinux_nova_dark_render_meta_tags', 5 );

function ailinux_nova_dark_schema_markup() {
    if ( ! is_singular( 'post' ) ) {
        return;
    }

    $post_id   = get_the_ID();
    $image_id  = get_post_thumbnail_id( $post_id );
    $image_url = $image_id ? wp_get_attachment_image_url( $image_id, 'full' ) : '';

    $schema = [
        '@context'       => 'https://schema.org',
        '@type'          => 'BlogPosting',
        'headline'       => get_the_title(),
        'datePublished'  => get_the_date( DATE_W3C ),
        'dateModified'   => get_the_modified_date( DATE_W3C ),
        'author'         => [
            '@type' => 'Person',
            'name'  => get_the_author_meta( 'display_name' ),
        ],
        'publisher'      => [
            '@type' => 'Organization',
            'name'  => get_bloginfo( 'name' ),
        ],
        'mainEntityOfPage' => get_permalink(),
        'description'    => wp_strip_all_tags( get_the_excerpt() ),
    ];

    if ( $image_url ) {
        $schema['image'] = $image_url;
        $schema['publisher']['logo'] = [
            '@type' => 'ImageObject',
            'url'   => $image_url,
        ];
    }

    echo '<script type="application/ld+json">' . wp_json_encode( $schema ) . '</script>';
}
// DISABLED: SEOPress handles Schema markup
// add_action( 'wp_head', 'ailinux_nova_dark_schema_markup', 20 );

function ailinux_nova_dark_breadcrumb_schema() {
    if ( is_home() || is_front_page() ) {
        return;
    }

    $items = [];
    $items[] = [
        '@type'    => 'ListItem',
        'position' => 1,
        'name'     => get_bloginfo( 'name' ),
        'item'     => home_url(),
    ];

    if ( is_singular() ) {
        $items[] = [
            '@type'    => 'ListItem',
            'position' => 2,
            'name'     => single_post_title( '', false ),
            'item'     => get_permalink(),
        ];
    } elseif ( is_archive() ) {
        $items[] = [
            '@type'    => 'ListItem',
            'position' => 2,
            'name'     => get_the_archive_title(),
            'item'     => get_post_type_archive_link( get_post_type() ),
        ];
    }

    if ( count( $items ) < 2 ) {
        return;
    }

    $schema = [
        '@context'        => 'https://schema.org',
        '@type'           => 'BreadcrumbList',
        'itemListElement' => $items,
    ];

    echo '<script type="application/ld+json">' . wp_json_encode( $schema ) . '</script>';
}
// DISABLED: SEOPress handles Breadcrumb Schema
// add_action( 'wp_head', 'ailinux_nova_dark_breadcrumb_schema', 21 );

function ailinux_nova_dark_menu_item_classes( $classes, $item ) {
    if ( in_array( 'menu-item-has-children', $classes, true ) ) {
        $title = isset( $item->title ) ? strtolower( wp_strip_all_tags( $item->title ) ) : '';
        if ( false !== strpos( $title, 'foren' ) || false !== strpos( $title, 'forum' ) ) {
            $classes[] = 'menu-item-foren';
        }
    }

    return $classes;
}
add_filter( 'nav_menu_css_class', 'ailinux_nova_dark_menu_item_classes', 10, 2 );


function ailinux_nova_dark_nav_menu_args( $args ) {
    if ( 'primary' === ( $args['theme_location'] ?? '' ) ) {
        $args['container'] = false;

        // Preserve context-specific classes supplied by header.php. Overwriting
        // these removed `desktop-nav` and `mobile-menu`, so both navigations
        // were rendered as desktop menus on small screens.
        if ( empty( $args['menu_class'] ) ) {
            $args['menu_class'] = 'menu main-menu';
        }

        $args['menu_id']     = $args['menu_id'] ?? 'primary-menu';
        $args['depth']       = $args['depth'] ?? 3;
        $args['fallback_cb'] = $args['fallback_cb'] ?? false;
    }

    if ( 'footer' === ( $args['theme_location'] ?? '' ) ) {
        $args['container'] = false;

        if ( empty( $args['menu_class'] ) ) {
            $args['menu_class'] = 'menu footer-menu';
        }

        $args['depth']       = $args['depth'] ?? 1;
        $args['fallback_cb'] = $args['fallback_cb'] ?? false;
    }

    return $args;
}
add_filter( 'wp_nav_menu_args', 'ailinux_nova_dark_nav_menu_args' );



function ailinux_nova_dark_customize_preview_js() {
    wp_enqueue_script(
        'ailinux-nova-dark-customizer',
        AILINUX_NOVA_DARK_URI . '/dist/customizer.js',
        [ 'customize-preview' ],
        ailinux_nova_dark_get_asset_version( '/dist/customizer.js' ),
        true
    );
}
add_action( 'customize_preview_init', 'ailinux_nova_dark_customize_preview_js' );

/**
 * Set posts per page to 12 for blog and archive views
 */
function ailinux_nova_dark_posts_per_page( $query ) {
    if ( ! is_admin() && $query->is_main_query() ) {
        if ( is_home() || is_archive() ) {
            $query->set( 'posts_per_page', 12 );
        }
    }
}
add_action( 'pre_get_posts', 'ailinux_nova_dark_posts_per_page' );

/**
 * Suppress specific 'doing it wrong' notices.
 *
 * This function filters the 'doing_it_wrong_trigger_error' hook to prevent
 * specific notices from being logged, particularly those from third-party plugins
 * that are difficult to fix directly.
 *
 * @param bool   $trigger_error Whether to trigger the error.
 * @param string $message       The error message.
 * @param string $context       The context of the error.
 * @param string $version       The WordPress version that added the message.
 * @return bool True to trigger the error, false to suppress it.
 */
function ailinux_nova_dark_suppress_complianz_notice( $trigger_error, $message, $context, $version ) {
    if ( strpos( $message, 'Translation loading for the `complianz-gdpr` domain was triggered too early' ) !== false ) {
        return false;
    }
    return $trigger_error;
}
add_filter( 'doing_it_wrong_trigger_error', 'ailinux_nova_dark_suppress_complianz_notice', 10, 4 );



function ailinux_nova_dark_maybe_enqueue_bbpress_css() {
    if ( function_exists( 'is_bbpress' ) && is_bbpress() ) {
        wp_enqueue_style(
            'ailinux-nova-bbpress-frontend',
            AILINUX_NOVA_DARK_URI . '/css/bbpress-frontend.css',
            array( 'ailinux-nova-dark-style' ),
            ailinux_nova_dark_get_asset_version( '/css/bbpress-frontend.css' )
        );
    }
}
add_action( 'wp_enqueue_scripts', 'ailinux_nova_dark_maybe_enqueue_bbpress_css', 30 );

/**
 * Enqueue Consent Banner CSS (Complianz optimizations)
 */
function ailinux_nova_dark_enqueue_consent_css() {
    $consent_file = WP_CONTENT_DIR . '/uploads/ailx/consent.css';
    if ( ! file_exists( $consent_file ) ) {
        return; // FIX 2026-04-11: Skip if file missing (prevents filemtime fatal)
    }
    wp_enqueue_style(
        'ailx-consent',
        content_url( 'uploads/ailx/consent.css' ),
        array(),
        filemtime( $consent_file )
    );
}
add_action( 'wp_enqueue_scripts', 'ailinux_nova_dark_enqueue_consent_css', 35 );

/**
 * Load the MCP registry UI from a real theme asset. Keeping this JavaScript out
 * of post_content prevents wpautop/texturize and Cloudflare Rocket Loader from
 * rewriting executable code on the MCP page.
 */
function ailinux_nova_dark_enqueue_mcp_server_page() {
    if ( ! is_page( 1521678 ) && ! is_page( 'mcp-server' ) ) {
        return;
    }

    wp_enqueue_style(
        'ailinux-mcp-server',
        AILINUX_NOVA_DARK_URI . '/dist/mcp-server.css',
        array(),
        ailinux_nova_dark_get_asset_version( '/dist/mcp-server.css' )
    );

    wp_enqueue_script(
        'ailinux-mcp-server',
        AILINUX_NOVA_DARK_URI . '/dist/mcp-server.js',
        array(),
        ailinux_nova_dark_get_asset_version( '/dist/mcp-server.js' ),
        true
    );
}
add_action( 'wp_enqueue_scripts', 'ailinux_nova_dark_enqueue_mcp_server_page', 36 );

/**
 * Fetch the public/non-admin MCP catalogue server-side. The page therefore has
 * useful content even when browser JavaScript, CORS or CDN script rewriting is
 * unavailable. Discovery remains separate from call-time authorization.
 *
 * @return array|WP_Error
 */
function ailinux_nova_dark_get_public_mcp_tools() {
    $cache_key = 'ailinux_public_mcp_tools_v1';
    $cached = get_transient( $cache_key );
    if ( is_array( $cached ) ) {
        return $cached;
    }

    $response = wp_remote_post(
        'https://api.ailinux.me/v1/mcp',
        array(
            'timeout' => 10,
            'headers' => array(
                'Content-Type' => 'application/json',
                'Accept'       => 'application/json',
            ),
            'body' => wp_json_encode(
                array(
                    'jsonrpc' => '2.0',
                    'id'      => 1,
                    'method'  => 'tools/list',
                    'params'  => array( 'inventory' => 'all' ),
                )
            ),
        )
    );

    if ( is_wp_error( $response ) ) {
        return $response;
    }

    if ( 200 !== (int) wp_remote_retrieve_response_code( $response ) ) {
        return new WP_Error( 'mcp_http_error', 'MCP registry returned a non-200 response.' );
    }

    $payload = json_decode( wp_remote_retrieve_body( $response ), true );
    $tools = $payload['result']['tools'] ?? null;
    if ( ! is_array( $tools ) ) {
        return new WP_Error( 'mcp_payload_error', 'MCP registry response did not contain a tool list.' );
    }

    set_transient( $cache_key, $tools, MINUTE_IN_SECONDS );
    return $tools;
}

/**
 * Replace the MCP page's loading placeholders with a server-rendered snapshot.
 */
function ailinux_nova_dark_render_mcp_tools( $content ) {
    if ( ! is_page( 1521678 ) && ! is_page( 'mcp-server' ) ) {
        return $content;
    }

    $tools = ailinux_nova_dark_get_public_mcp_tools();
    if ( is_wp_error( $tools ) ) {
        $status_html = '<div class="mcp-status bad" id="mcp-status"><i></i><span>Live tool list unavailable right now.</span></div>';
        return preg_replace(
            '#<div class="mcp-status" id="mcp-status"><i></i><span>.*?</span></div>#s',
            $status_html,
            $content,
            1
        );
    }

    $categories = array();
    $cards = '';
    foreach ( $tools as $tool ) {
        if ( ! is_array( $tool ) || empty( $tool['name'] ) ) {
            continue;
        }

        $groups = isset( $tool['x_inventory_groups'] ) && is_array( $tool['x_inventory_groups'] ) ? $tool['x_inventory_groups'] : array();
        $category = (string) ( $tool['x_task_inventory'] ?? $tool['x_inventory'] ?? ( $groups[0] ?? 'other' ) );
        $categories[ $category ] = true;

        $name = (string) ( $tool['x_display_name'] ?? $tool['name'] );
        $description = (string) ( $tool['description'] ?? '' );
        $access = (string) ( $tool['x_access'] ?? 'policy' );
        $execution = (string) ( $tool['x_execution'] ?? 'server' );
        $hint = (string) ( $tool['x_usage_hint'] ?? '' );
        $search_text = strtolower( $name . ' ' . $description . ' ' . $category . ' ' . $access . ' ' . $execution . ' ' . $hint );

        $cards .= '<article class="mcp-tool" data-category="' . esc_attr( $category ) . '" data-search="' . esc_attr( $search_text ) . '">';
        $cards .= '<h4>' . esc_html( $name ) . '</h4>';
        $cards .= '<p>' . esc_html( $description ) . '</p>';
        $cards .= '<div class="mcp-tags">';
        foreach ( array_filter( array( $category, $access, $execution, $hint ) ) as $tag ) {
            $cards .= '<span class="mcp-tag">' . esc_html( (string) $tag ) . '</span>';
        }
        $cards .= '</div></article>';
    }

    ksort( $categories, SORT_NATURAL | SORT_FLAG_CASE );
    $select = '<select id="mcp-filter" aria-label="Filter MCP tools"><option value="">All categories</option>';
    foreach ( array_keys( $categories ) as $category ) {
        $select .= '<option value="' . esc_attr( $category ) . '">' . esc_html( $category ) . '</option>';
    }
    $select .= '</select>';

    $count = count( $tools );
    $status_html = '<div class="mcp-status ok" id="mcp-status"><i></i><span>Live registry connected - ' . esc_html( (string) $count ) . ' tools currently advertised for this public session</span></div>';

    $content = preg_replace(
        '#<div class="mcp-status" id="mcp-status"><i></i><span>.*?</span></div>#s',
        $status_html,
        $content,
        1
    );
    $content = preg_replace(
        '#<select id="mcp-filter" aria-label="Filter MCP tools">.*?</select>#s',
        $select,
        $content,
        1
    );
    $content = preg_replace(
        '#<div class="mcp-tools" id="mcp-tools"></div>#',
        '<div class="mcp-tools" id="mcp-tools">' . $cards . '</div>',
        $content,
        1
    );
    $content = preg_replace(
        '#<span class="mcp-count" id="mcp-count"></span>#',
        '<span class="mcp-count" id="mcp-count">' . esc_html( (string) $count ) . ' / ' . esc_html( (string) $count ) . ' tools</span>',
        $content,
        1
    );

    return $content;
}
add_filter( 'the_content', 'ailinux_nova_dark_render_mcp_tools', 20 );

// FIX 2026-04-11: Rocket Loader protection + optional CSP nonce for theme scripts
add_filter('script_loader_tag', function ($tag, $handle, $src) {
    // 1. Prevent Rocket Loader from deferring critical scripts (ALWAYS active)
    $cfasync_handles = ['ailinux-nova-dark-color-mode', 'ailinux-nova-dark-app', 'ailinux-nova-dark-mobile-menu', 'ailinux-mcp-server'];
    if (in_array($handle, $cfasync_handles, true) && strpos($tag, 'data-cfasync') === false) {
        $tag = str_replace('<script ', '<script data-cfasync="false" ', $tag);
    }

    // 2. webgpu.js: ES module + cfasync
    if ($handle === 'ailinux-nova-dark-webgpu') {
        $tag = str_replace(' type="text/javascript"', '', $tag);
        if (strpos($tag, 'data-cfasync') === false) {
            $tag = str_replace('<script ', '<script type="module" data-cfasync="false" ', $tag);
        }
    }

    // 3. Optional CSP nonce (only if AILINUX_CSP_NONCE is defined)
    if (defined('AILINUX_CSP_NONCE') && AILINUX_CSP_NONCE) {
        $nonce_handles = ['ailinux-nova-dark-color-mode', 'ailinux-nova-dark-app',
                          'ailinux-nova-dark-mobile-menu', 'ailinux-nova-dark-customizer'];
        if (in_array($handle, $nonce_handles, true)) {
            $tag = str_replace('<script ', '<script nonce="' . esc_attr(AILINUX_CSP_NONCE) . '" ', $tag);
        }
    }

    return $tag;
}, 10, 3);

/**
 * Flush menu cache when a page is saved to fix new pages not appearing in menus.
 * This addresses caching issues where recently added pages don't show up.
 */
function ailinux_nova_dark_flush_menu_on_page_save( $post_id, $post, $update ) {
    if ( $post->post_type !== 'page' || wp_is_post_revision( $post_id ) || ! $update ) {
        return;
    }

    // FIX 2026-04-11: Targeted transient cleanup instead of nuclear wp_cache_flush()
    global $wpdb;
    $wpdb->query( $wpdb->prepare(
        "DELETE FROM {$wpdb->options} WHERE option_name LIKE %s",
        $wpdb->esc_like( '_transient_nav_menu_' ) . '%'
    ) );
}
add_action( 'save_post', 'ailinux_nova_dark_flush_menu_on_page_save', 10, 3 );

/* ============================================================
   NOVA AI — ARTIKEL DISKUSSIONS-WIDGET
   ARTIKEL DISKUSSIONS-WIDGET — re-enabled 2026-04-11
   ============================================================ */

/**
 * Rendert den "Mit KI besprechen" Button + Chat-Panel
 * Wird von template-parts/content-single.php aufgerufen
 */
function nova_article_discuss_widget() {
    global $post;
    if ( ! $post ) return;

    // Artikel-Kontext sammeln
    $title      = get_the_title( $post->ID );
    $content    = wp_strip_all_tags( get_post_field( 'post_content', $post->ID ) );
    $content    = mb_substr( $content, 0, 3000 ); // max 3000 Zeichen für Context-Window
    $excerpt    = has_excerpt( $post->ID )
                  ? wp_strip_all_tags( get_the_excerpt( $post->ID ) )
                  : mb_substr( $content, 0, 200 ) . '…';
    $url        = get_permalink( $post->ID );
    $date       = get_the_date( 'd.m.Y', $post->ID );
    $categories = implode( ', ', wp_list_pluck( get_the_category( $post->ID ), 'name' ) );

    // Kontext als JSON für JS (escaped)
    $context_data = esc_attr( json_encode([
        'title'      => $title,
        'url'        => $url,
        'date'       => $date,
        'categories' => $categories,
        'excerpt'    => $excerpt,
        'content'    => $content,
    ]) );

    $api_base  = function_exists( 'rest_url' ) ? rest_url( 'nova-ai/v1' ) : '/wp-json/nova-ai/v1';
    $nonce_url = rest_url( 'nova-ai/v1/nonce' );

    ?>
<div class="nova-article-discuss" data-post-id="<?php echo esc_attr( $post->ID ); ?>"
     data-context='<?php echo $context_data; ?>'
     data-api-base="<?php echo esc_attr( $api_base ); ?>"
     data-nonce-url="<?php echo esc_attr( $nonce_url ); ?>">

  <button class="nova-discuss-btn" aria-expanded="false" aria-controls="nova-discuss-panel-<?php echo $post->ID; ?>">
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
      <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path>
    </svg>
    <?php esc_html_e( 'Discuss with AI', 'ailinux-nova-dark' ); ?>
  </button>

  <div class="nova-discuss-panel" id="nova-discuss-panel-<?php echo $post->ID; ?>">
    <div class="nova-discuss-header">
      <div class="nova-discuss-header-left">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <circle cx="12" cy="12" r="3"></circle>
          <path d="M12 1v4M12 19v4M4.22 4.22l2.83 2.83M16.95 16.95l2.83 2.83M1 12h4M19 12h4M4.22 19.78l2.83-2.83M16.95 7.05l2.83-2.83"></path>
        </svg>
        <?php esc_html_e( 'AI Assistant', 'ailinux-nova-dark' ); ?>
      </div>
      <select id="nova-discuss-model-<?php echo $post->ID; ?>" name="nova-discuss-model" class="nova-discuss-model-select" aria-label="AI model" data-autoload="true" data-models-url="<?php echo esc_attr( rest_url('nova-ai/v1/models') ); ?>">
        <option value="groq/meta-llama/llama-4-scout-17b-16e-instruct" selected>Loading models…</option>
      </select>
      <button class="nova-discuss-close" aria-label="Close">×</button>
    </div>
    <div class="nova-discuss-context-bar">
      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <circle cx="12" cy="12" r="10"></circle><line x1="12" y1="8" x2="12" y2="12"></line><line x1="12" y1="16" x2="12.01" y2="16"></line>
      </svg>
      <?php esc_html_e( 'Context loaded:', 'ailinux-nova-dark' ); ?>
      <strong><?php echo esc_html( mb_substr( $title, 0, 60 ) ); ?></strong>
    </div>
    <div class="nova-discuss-quick">
      <button class="nova-discuss-quick-btn" data-q="Summarize this article briefly.">📋 Summary</button>
      <button class="nova-discuss-quick-btn" data-q="What are the key points?">🔑 Key points</button>
      <button class="nova-discuss-quick-btn" data-q="Explain the topic for a beginner.">🎓 Explain simply</button>
      <button class="nova-discuss-quick-btn" data-q="What are the main arguments for and against it?">💬 Pros & cons</button>
    </div>
    <div class="nova-discuss-messages" role="log" aria-live="polite"></div>
    <div class="nova-discuss-input-row">
      <textarea id="nova-discuss-input-<?php echo $post->ID; ?>" name="nova-discuss-input" class="nova-discuss-input" rows="1"
                placeholder="<?php esc_attr_e( 'Ask about this article…', 'ailinux-nova-dark' ); ?>"
                maxlength="1000"></textarea>
      <button class="nova-discuss-send" aria-label="Send">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <line x1="22" y1="2" x2="11" y2="13"></line>
          <polygon points="22 2 15 22 11 13 2 9 22 2"></polygon>
        </svg>
      </button>
    </div>
  </div>
</div>
    <?php
}

/* ─── JS für Artikel-Discuss-Widget ───────────────────────── */
add_action( 'wp_footer', function () {
    if ( ! is_singular() ) return;
    ?>
<script>
(function(){
  'use strict';
  document.addEventListener('DOMContentLoaded', function() {
    document.querySelectorAll('.nova-article-discuss').forEach(function(widget) {
      const btn        = widget.querySelector('.nova-discuss-btn');
      const panel      = widget.querySelector('.nova-discuss-panel');
      const closeBtn   = widget.querySelector('.nova-discuss-close');
      const modelSel   = widget.querySelector('.nova-discuss-model-select');
      const messages   = widget.querySelector('.nova-discuss-messages');
      const input      = widget.querySelector('.nova-discuss-input');
      const sendBtn    = widget.querySelector('.nova-discuss-send');
      const quickBtns  = widget.querySelectorAll('.nova-discuss-quick-btn');

      const apiBase    = widget.dataset.apiBase || '/wp-json/nova-ai/v1';
      const nonceUrl   = widget.dataset.nonceUrl || apiBase + '/nonce';
      let   nonce      = window.novaAiConfig?.nonce || '';
      let   history    = [];
      let   ctx        = null;

      // Kontext parsen
      try { ctx = JSON.parse(widget.dataset.context || '{}'); } catch(e) { ctx = {}; }

      // Nonce frisch holen
      async function fetchNonce() {
        try {
          const r = await fetch(nonceUrl, {credentials:'include'});
          if (r.ok) { const d = await r.json(); if (d.nonce) nonce = d.nonce; }
        } catch(e) {}
      }

      // Modelle laden (einmalig beim ersten Öffnen)
      let modelsLoaded = false;
      async function loadModels() {
        if (modelsLoaded) return;
        modelsLoaded = true;
        const modelsUrl = modelSel?.dataset?.modelsUrl || (apiBase + '/models');
        try {
          const r = await fetch(modelsUrl);
          if (!r.ok) return;
          const data = await r.json();
          const chatModels = (data.categories?.chat || data.models || [])
            .filter(function(m) { return m.chat && !m.paused; });
          if (!chatModels.length) return;
          // Aktuellen Wert merken
          const curVal = modelSel.value;
          modelSel.innerHTML = '';
          // Nach Provider gruppieren
          const grouped = {};
          chatModels.forEach(function(m) {
            const p = m.provider || 'other';
            if (!grouped[p]) grouped[p] = [];
            grouped[p].push(m);
          });
          // Sortierte Provider
          const provOrder = ['groq','gemini','mistral','cerebras','github','openrouter','cloudflare','ollama','anthropic','huggingface','replicate'];
          const sortedProviders = Object.keys(grouped).sort(function(a,b) {
            const ia = provOrder.indexOf(a), ib = provOrder.indexOf(b);
            return (ia===-1?99:ia) - (ib===-1?99:ib);
          });
          sortedProviders.forEach(function(prov) {
            const grp = document.createElement('optgroup');
            grp.label = prov.charAt(0).toUpperCase() + prov.slice(1);
            grouped[prov].forEach(function(m) {
              const opt = document.createElement('option');
              opt.value = m.id;
              opt.textContent = m.name || m.id.split('/').pop();
              if (m.id === curVal) opt.selected = true;
              grp.appendChild(opt);
            });
            modelSel.appendChild(grp);
          });
          // Default: behalte groq/llama-4-scout falls vorhanden
          if (!modelSel.querySelector('option[selected]') && modelSel.options.length) {
            modelSel.options[0].selected = true;
          }
        } catch(e) {
          console.warn('Discuss: Model loading failed', e);
        }
      }

      // Button toggle
      btn.addEventListener('click', function() {
        const isOpen = panel.classList.toggle('open');
        btn.setAttribute('aria-expanded', isOpen);
        if (isOpen && !nonce) fetchNonce();
        if (isOpen) loadModels();
        if (isOpen && messages.children.length === 0) {
          addMessage('ai', '👋 I loaded the article “' + (ctx.title || '') + '”. What would you like to know?');
        }
      });
      closeBtn.addEventListener('click', function() {
        panel.classList.remove('open');
        btn.setAttribute('aria-expanded', 'false');
      });

      // Quick prompts
      quickBtns.forEach(function(qb) {
        qb.addEventListener('click', function() {
          input.value = qb.dataset.q || '';
          input.focus();
        });
      });

      // Auto-resize textarea
      input.addEventListener('input', function() {
        this.style.height = 'auto';
        this.style.height = Math.min(this.scrollHeight, 120) + 'px';
      });
      input.addEventListener('keydown', function(e) {
        if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
      });
      sendBtn.addEventListener('click', sendMessage);

      function addMessage(role, text) {
        const el = document.createElement('div');
        el.className = role === 'user' ? 'nova-dm-user' : (role === 'error' ? 'nova-dm-error' : 'nova-dm-ai');
        el.textContent = text;
        messages.appendChild(el);
        messages.scrollTop = messages.scrollHeight;
        return el;
      }

      async function sendMessage() {
        const msg = input.value.trim();
        if (!msg || sendBtn.disabled) return;

        addMessage('user', msg);
        history.push({role:'user', content:msg});
        input.value = '';
        input.style.height = 'auto';
        sendBtn.disabled = true;

        const loadingEl = addMessage('ai', '⏳ Denke nach…');
        loadingEl.classList.add('loading');

        // Kontext als System-String aufbauen
        const contextStr = [
          'Titel: ' + (ctx.title || ''),
          'URL: ' + (ctx.url || ''),
          'Datum: ' + (ctx.date || ''),
          'Kategorien: ' + (ctx.categories || ''),
          '',
          'Inhalt:',
          ctx.content || ctx.excerpt || ''
        ].join('\n');

        // Nonce holen wenn fehlt
        if (!nonce) await fetchNonce();

        try {
          const resp = await fetch(apiBase + '/article-chat', {
            method: 'POST',
            credentials: 'include',
            headers: {
              'Content-Type': 'application/json',
              'X-WP-Nonce': nonce
            },
            body: JSON.stringify({
              model:   modelSel ? modelSel.value : 'groq/meta-llama/llama-4-scout-17b-16e-instruct',
              context: contextStr,
              message: msg,
              history: history.slice(0, -1) // ohne letzten User-Eintrag (wird server-seitig als message gesetzt)
            })
          });

          const data = await resp.json();
          const answer = data?.content || data?.text || data?.raw?.text || data?.message || '';

          loadingEl.classList.remove('loading');
          if (!resp.ok || data?.error) {
            loadingEl.className = 'nova-dm-error';
            loadingEl.textContent = '⚠ ' + (data?.error || 'Fehler beim Abrufen der Antwort.');
            history.pop();
          } else {
            loadingEl.textContent = answer || '(Keine Antwort)';
            history.push({role:'assistant', content: answer});
            // Maximal 20 Einträge in History
            if (history.length > 20) history = history.slice(-20);
          }

        } catch(err) {
          loadingEl.className = 'nova-dm-error';
          loadingEl.textContent = '⚠ Verbindungsfehler: ' + err.message;
          history.pop();
        }

        sendBtn.disabled = false;
        messages.scrollTop = messages.scrollHeight;
        input.focus();
      }
    });
  });
})();
</script>
    <?php
}, 99 );
// discuss widget active

// webgpu.js dequeue: ES Module breaks Rocket Loader. Not used in theme. Remove completely.
add_action('wp_enqueue_scripts', function() {
    wp_dequeue_script('ailinux-nova-dark-webgpu');
    wp_deregister_script('ailinux-nova-dark-webgpu');
}, 99);

// After WP logout: set shared cookie so login.ailinux.me auto-clears JWT
add_action('wp_logout', function() {
    // Cookie on .ailinux.me domain signals login.ailinux.me to clear JWT
    setcookie('ailinux_logout', '1', [
        'expires'  => time() + 300,
        'path'     => '/',
        'domain'   => '.ailinux.me',
        'secure'   => true,
        'httponly' => false,  // JS must read it
        'samesite' => 'Lax',
    ]);
});
add_filter('logout_redirect', function($redirect_to, $requested_redirect_to, $user) {
    if (!empty($requested_redirect_to)) {
        $host = wp_parse_url($requested_redirect_to, PHP_URL_HOST);
        if ($host && in_array(strtolower($host), ['ailinux.me', 'login.ailinux.me'], true)) {
            return $requested_redirect_to;
        }
    }
    return 'https://login.ailinux.me/?action=logout&redirect=' . rawurlencode(home_url('/'));
}, 10, 3);

// ── Auth status endpoint for login.ailinux.me sync ──────────────────────
add_action('rest_api_init', function() {
    register_rest_route('ailinux/v1', '/auth-status', [
        'methods'             => 'GET',
        'callback'            => function() {
            return new WP_REST_Response([
                'logged_in'    => is_user_logged_in(),
                'display_name' => is_user_logged_in() ? wp_get_current_user()->display_name : '',
            ], 200);
        },
        'permission_callback' => '__return_true',
    ]);
});

/* ── FIX 2026-04-11: Force permissive CSP on wp-admin (overrides CF/plugin CSP) ── */
add_action('admin_init', function() {
    // Remove any CSP headers that block unsafe-eval (needed for Block Editor, Customizer, Widgets)
    header_remove('Content-Security-Policy');
    header_remove('content-security-policy');
}, 1);
add_action('send_headers', function() {
    if (is_admin() || is_customize_preview() || isset($_GET['wp_customize']) || isset($_GET['customize_changeset_uuid'])) {
        // Overwrite with permissive admin CSP
        header("Content-Security-Policy: default-src 'self'; script-src 'self' 'unsafe-inline' 'unsafe-eval' https:; style-src 'self' 'unsafe-inline' https:; img-src 'self' data: https: blob:; font-src 'self' data: https:; connect-src 'self' https: wss:; frame-src 'self' https:; worker-src 'self' blob:; media-src 'self' data: https:; object-src 'none'; base-uri 'self'; form-action 'self' https:; frame-ancestors 'self'; upgrade-insecure-requests", true);
    }
}, 99999); // Very high priority to override anything

/* ── CSP Overrides: frame-src & script-src für ailinux.me Subdomains ── */
add_action('send_headers', function() {
    // FIX 2026-04-11: CSP nur im Frontend — Admin/REST/AJAX/Customizer brauchen unsafe-eval
    if (is_admin() || wp_doing_ajax() || (defined('REST_REQUEST') && REST_REQUEST) || wp_doing_cron()) {
        return;
    }
    // Customizer preview runs as frontend but needs unsafe-eval for live-preview JS
    if (is_customize_preview() || isset($_GET['customize_changeset_uuid']) || isset($_GET['wp_customize'])) {
        return;
    }
    $d = "default-src 'self'";
    $s = "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://challenges.cloudflare.com https://www.googletagmanager.com https://translate.googleapis.com https://translate.google.com https://cdn.gtranslate.net https://static.addtoany.com https://js.intercomcdn.com https://accounts.google.com";
    $f = "frame-src 'self' https://ailinux.me/account https://api.ailinux.me https://www.youtube.com https://accounts.google.com https://challenges.cloudflare.com https://www.google.com https://td.doubleclick.net https://static.addtoany.com";
    $c = "connect-src 'self' https://api.ailinux.me wss://api.ailinux.me https:";
    $i = "img-src 'self' data: https: blob:";
    $fo = "font-src 'self' data: https://fonts.gstatic.com";
    $st = "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com";
    $w = "worker-src 'self' blob:";
    $m = "media-src 'self' data: https:";
    $o = "object-src 'none'";
    $b = "base-uri 'self'";
    $fa = "form-action 'self' https://ailinux.me/account";
    $fr = "frame-ancestors 'self'";
    $csp = implode('; ', [$d,$s,$f,$c,$i,$fo,$st,$w,$m,$o,$b,$fa,$fr,'upgrade-insecure-requests']);
    if (!headers_sent()) {
        header('Content-Security-Policy: ' . $csp, true);
    }
}, 9999);


// GTranslate im Admin + REST API deaktivieren (verhindert Invalid JSON beim Widget-Speichern)
// FIX 2026-04-11: Auch output_buffering hooks abfangen + wp_loaded für spätere Registrierungen
add_action('plugins_loaded', function() {
    if (is_admin() || (defined('REST_REQUEST') && REST_REQUEST) || wp_doing_ajax()) {
        remove_action('init', array('GTranslate', 'init'));
        remove_action('wp_head', array('GTranslate', 'add_inline_script'));
        remove_action('wp_footer', array('GTranslate', 'add_float_code'));
        // Catch late registrations
        add_action('wp_loaded', function() {
            remove_action('wp_head', array('GTranslate', 'add_inline_script'));
            remove_action('wp_footer', array('GTranslate', 'add_float_code'));
            // Disable output buffering that injects translation HTML
            if (class_exists('GTranslate')) {
                remove_action('template_redirect', array('GTranslate', 'start_buffering'));
            }
        }, 999);
    }
}, 1);
