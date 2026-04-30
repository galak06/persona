<?php
/**
 * brand-fix: sitewide footer disclaimer + methodology/affiliate-disclosure links.
 *
 * Injected via the Code Snippets plugin (active on dogfoodandfun.com).
 * Reversible: deactivate the snippet and the markup disappears.
 * Output is keyed by class names so CSS/JS can target it if needed later.
 */

add_action( 'wp_footer', function () {
    // Only render on the public front-end; skip admin/AJAX/feed/REST requests.
    if ( is_admin() || wp_doing_ajax() || is_feed() || defined( 'REST_REQUEST' ) ) {
        return;
    }

    $disclosure  = esc_url( home_url( '/affiliate-disclosure/' ) );
    $methodology = esc_url( home_url( '/methodology/' ) );
    $disclaimer  = esc_url( home_url( '/disclaimer/' ) );

    ?>
    <div class="dff-site-disclaimer" role="contentinfo" style="text-align:center;padding:14px 16px;font-size:13px;line-height:1.5;color:#666;max-width:820px;margin:0 auto;">
        Dog Food &amp; Fun is written by a dog owner, not a veterinarian.
        Nothing here is medical advice.
        <a href="<?php echo $disclosure; ?>">Affiliate Disclosure</a>
        &middot;
        <a href="<?php echo $methodology; ?>">Methodology</a>
        &middot;
        <a href="<?php echo $disclaimer; ?>">Disclaimer</a>
    </div>
    <?php
}, 20 );
