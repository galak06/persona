"""HTML/CSS/JS template for the dogfoodandfun.com homepage rebuild.

Design: Fraunces display font + DM Sans body, Astra global color vars throughout,
micro-interactions on cards/buttons, dynamic blog grid server-rendered.
"""
from __future__ import annotations
import html as hl

_HERO_IMG = "https://dogfoodandfun.com/wp-content/uploads/2026/06/hero_dog2_opt.jpg"
_ABOUT_IMG = "https://dogfoodandfun.com/wp-content/uploads/2026/01/ChatGPT-Image-Jan-10-2026-06_28_33-PM.png"
_BADGE_IMG = "https://dogfoodandfun.com/wp-content/uploads/2026/05/nalla-approved-badge.png"

_CAT_IMG_GROOMING = "https://dogfoodandfun.com/wp-content/uploads/2026/06/Grooming.jpeg"
_CAT_IMG_FOOD = "https://dogfoodandfun.com/wp-content/uploads/2026/06/Food-Diet.jpeg"
_CAT_IMG_LIFESTYLE = "https://dogfoodandfun.com/wp-content/uploads/2026/06/Lifestyle-Gear.jpeg"
_CAT_IMG_TRAINING = "https://dogfoodandfun.com/wp-content/uploads/2026/06/Training.jpeg"

_CATS = [
    ("01", "🛁", "Grooming", "Simple DIY guides to keep your dog's coat healthy and clean without the stress of a salon visit.", "/category/grooming/", "Explore Grooming Guides", _CAT_IMG_GROOMING),
    ("02", "🥩", "Food &amp; Diet", "We decode the labels. Honest breakdowns of kibble, wet food, and treats to help you avoid the marketing hype.", "/category/food-and-diet/", "Explore Food &amp; Diet Tips", _CAT_IMG_FOOD),
    ("03", "🦮", "Lifestyle &amp; Gear", "From durable toys to comfortable beds—gear reviews tested by Nalla herself for everyday living.", "/category/lifestyle-and-gear/", "Explore Lifestyle Guides", _CAT_IMG_LIFESTYLE),
    ("04", "🎓", "Training", "Positive reinforcement tips and practical tricks to strengthen the bond between you and your dog.", "/category/training/", "Explore Training Guides", _CAT_IMG_TRAINING),
]

_STEPS = [
    ("01", "Ingredient Forensics", "We parse every AAFCO label — named proteins, fillers, controversial additives, and hidden carbohydrate loads. No marketing copy accepted."),
    ("02", "Comparative Benchmarking", "Every product is measured against 2–3 direct competitors on cost-per-serving, protein density, and ingredient quality score."),
    ("03", "Real-World Testing", "Where applicable, we test gear on Nalla and document results over weeks — not a single afternoon."),
]


def _post_card(post: dict) -> str:
    title = hl.escape(post["title"])
    link = hl.escape(post["link"])
    excerpt = hl.escape(post["excerpt"][:120]) + "…" if post["excerpt"] else ""
    date = hl.escape(post["date"])
    img = post["image"]
    cat = hl.escape(post.get("cat_name", ""))
    img_html = (f'<div class="dff-bpost-img" style="background-image:url(\'{hl.escape(img)}\')"></div>'
                if img else '<div class="dff-bpost-img dff-bpost-nophoto"></div>')
    badge = f'<span class="dff-bpost-cat">{cat}</span>' if cat else ""
    return f"""<a class="dff-bpost-card" href="{link}">
  {img_html}
  <div class="dff-bpost-body">
    {badge}
    <h3 class="dff-bpost-title">{title}</h3>
    <p class="dff-bpost-excerpt">{excerpt}</p>
    <div class="dff-bpost-footer">
      <time class="dff-bpost-date">{date}</time>
      <span class="dff-bpost-cta">Get the Full Guide →</span>
    </div>
  </div>
</a>"""


def build_html(posts: list[dict], schema_json: str = "") -> str:
    cards = "\n".join(_post_card(p) for p in posts[:3])
    cat_cards = "\n".join(
        f"""<a class="dff-cat-card" href="{url}">
  <div class="dff-cat-img" style="background-image:url('{img}')"></div>
  <span class="dff-cat-num">{num}</span>
  <span class="dff-cat-icon">{icon}</span>
  <h3 class="dff-cat-name">{name}</h3>
  <p class="dff-cat-desc">{desc}</p>
  <span class="dff-cat-cta">{cta} →</span>
</a>"""
        for num, icon, name, desc, url, cta, img in _CATS
    )
    steps_html = "\n".join(
        f"""<div class="dff-step">
  <div class="dff-step-num">{icon}</div>
  <div class="dff-step-body">
    <h3 class="dff-step-title">{title}</h3>
    <p class="dff-step-desc">{desc}</p>
  </div>
</div>"""
        for icon, title, desc in _STEPS
    )

    return f"""<!-- wp:html -->
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Fraunces:ital,opsz,wght@0,9..144,700;0,9..144,800;1,9..144,400&family=DM+Sans:opsz,wght@9..40,400;9..40,500;9..40,600&display=swap" rel="stylesheet">

<div class="dff-hp">

<section class="dff-hero" style="background-image:url('{_HERO_IMG}')">
  <div class="dff-hero-overlay"></div>
  <div class="dff-hero-content">
    <div class="dff-hero-badges">
      <span>✓ 50+ Brands Reviewed</span><span>✓ Independent &amp; Honest</span><span>✓ Nalla-Tested</span>
    </div>
    <h1 class="dff-hero-h1">No hype. Just data.<br><em class="dff-hero-accent">And Nalla.</em></h1>
    <p class="dff-hero-sub">Join Nalla and me as we dig into honest product reviews, nutritional facts, and tips to keep your best friend happy and healthy.</p>
    <a class="dff-btn" href="/blog/">See What Nalla Recommends</a>
  </div>
</section>

<section class="dff-about">
  <div class="dff-about-inner">
    <div class="dff-about-img"><img src="{_ABOUT_IMG}" alt="Nalla the dog" loading="lazy"></div>
    <div class="dff-about-body">
      <span class="dff-eyebrow">🐾 Meet Nalla's Dad</span>
      <h2 class="dff-about-h2">Hi, I'm Nalla's Dad</h2>
      <h3 class="dff-about-h3">Engineer by Day, Dog Nutrition Researcher by Night</h3>
      <p>I'm not a veterinarian. I'm a software engineer who got frustrated by vague pet food marketing and applied the same analytical mindset I use at work — reading ingredient labels like spec sheets, comparing cost-per-serving like infrastructure costs, and refusing to accept "premium" as a data point.</p>
      <p>This site is what I wish existed when I got Nalla: honest, numbers-driven breakdowns without the fluff.</p>
    </div>
  </div>
</section>

<section class="dff-cats">
  <div class="dff-inner">
    <h2 class="dff-section-h2">What We Cover</h2>
    <div class="dff-cats-grid">{cat_cards}</div>
  </div>
</section>

<section class="dff-nalla">
  <div class="dff-inner">
    <div class="dff-nalla-inner">
      <img src="{_BADGE_IMG}" alt="Nalla Certified badge" class="dff-nalla-badge" loading="lazy">
      <div class="dff-nalla-body">
        <h2>Nalla-Certified</h2>
        <p>Every product, food, and gadget on this site is personally tested by Nalla — my shepherd mix and chief taste tester. Look for the <strong>Nalla Certified</strong> badge on reviews she's personally signed off on.</p>
        <a class="dff-nalla-cta" href="/tag/nalla-certified/">See Everything Nalla Loves →</a>
      </div>
    </div>
  </div>
</section>

<section class="dff-blog">
  <div class="dff-inner">
    <div class="dff-blog-hd"><h2 class="dff-section-h2">Recent Posts</h2><a class="dff-blog-all" href="/blog/">View all →</a></div>
    <div class="dff-blog-grid">{cards}</div>
  </div>
</section>

<section class="dff-nl">
  <div class="dff-nl-inner">
    <h2 class="dff-nl-h2">Get Nalla's Weekly Picks</h2>
    <p class="dff-nl-sub">Ingredient breakdowns, gear reviews, and what Nalla's been testing.</p>
    <form id="vet-ajax-form" class="dff-nl-form">
      <input type="email" name="email" placeholder="your@email.com" required>
      <button type="submit" id="vet-btn">Yes, Send Me Recipes!</button>
    </form>
    <div id="vet-msg"></div>
  </div>
</section>

<section class="dff-steps">
  <div class="dff-inner">
    <h2 class="dff-section-h2">How We Review — Our 3-Step Process</h2>
    <div class="dff-steps-grid">{steps_html}</div>
  </div>
</section>

</div>

{_css()}
{_js()}
<!-- /wp:html -->"""


def _css() -> str:
    return """<style>
.dff-hp{font-family:'DM Sans',-apple-system,sans-serif;color:var(--ast-global-color-1,#3a3a3a);--r:8px;background:var(--ast-global-color-5,#fff)}
.dff-hp *{box-sizing:border-box}
.dff-hp a{text-decoration:none !important}
.dff-hp h1,.dff-hp h2,.dff-hp h3{font-family:'Fraunces',Georgia,serif;line-height:1.15;margin:0 0 .6em}
.dff-inner{max-width:1200px;margin:0 auto;padding:0 clamp(20px,4vw,48px)}
.dff-btn{display:inline-block;background:var(--ast-global-color-0,#ff5f42);color:#fff;font-weight:600;padding:13px 28px;border-radius:var(--r);text-decoration:none !important;transition:opacity .2s,transform .2s;font-size:.95rem}
.dff-btn:hover{opacity:.88;transform:translateY(-1px);color:#fff}
.dff-eyebrow{font-size:.75rem;font-weight:600;letter-spacing:.1em;text-transform:uppercase;color:var(--ast-global-color-0,#ff5f42)}
.dff-section-h2{font-size:clamp(1.6rem,3vw,2.2rem) !important;color:var(--ast-global-color-2,#313131);margin-bottom:1.5rem}
/* HERO */
.dff-hero{position:relative;min-height:580px;background-color:#1c1817;background-size:cover;background-position:center 65%;display:flex;align-items:center;margin-left:calc(-50vw + 50%);margin-right:calc(-50vw + 50%);width:100vw;max-width:100vw}
.dff-hero-overlay{position:absolute;inset:0;background:linear-gradient(105deg,rgba(28,24,23,.92) 0%,rgba(28,24,23,.7) 45%,rgba(28,24,23,.08) 100%)}
.dff-hero-content{position:relative;padding:80px clamp(24px,5vw,80px);max-width:640px}
.dff-hero-badges{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:20px}
.dff-hero-badges span{background:rgba(255,255,255,.15);border:1px solid rgba(255,255,255,.35);border-radius:20px;padding:5px 13px;font-size:.78rem;font-weight:600;color:#fff;letter-spacing:.02em}
.dff-hero-h1{font-size:clamp(2.4rem,5vw,3.6rem) !important;color:#fff;font-weight:800;margin-bottom:.5em}
.dff-hero-accent{font-style:italic;color:var(--ast-global-color-0,#ff5f42)}
.dff-hero-sub{color:rgba(255,255,255,.82);font-size:1.05rem;line-height:1.65;margin-bottom:2rem;max-width:460px}
/* ABOUT */
.dff-about{padding:80px 0;background:#f8f4ee}
.dff-about-inner{max-width:1200px;margin:0 auto;padding:0 clamp(20px,4vw,48px);display:grid;grid-template-columns:1fr 1fr;gap:64px;align-items:center}
.dff-about-img img{width:100%;border-radius:12px;display:block;object-fit:cover;aspect-ratio:4/3}
.dff-about-h2{font-size:clamp(1.8rem,3vw,2.4rem);color:var(--ast-global-color-2,#313131);margin:.4em 0 .2em}
.dff-about-h3{font-size:1rem;font-weight:500;color:var(--ast-global-color-3,#3a3a3a);font-family:'DM Sans',sans-serif;font-style:italic;margin-bottom:1rem}
.dff-about-body p{line-height:1.7;margin-bottom:1rem;color:var(--ast-global-color-1,#3a3a3a)}
/* CATEGORIES */
.dff-cats{padding:80px 0;background:var(--ast-global-color-5,#fff)}
.dff-cats-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:20px}
.dff-cat-card{display:block;background:var(--ast-global-color-5,#fff);border:1px solid var(--ast-global-color-4,#f5f5f5);border-radius:12px;padding:28px 24px;text-decoration:none;color:inherit;transition:transform .2s,box-shadow .2s,border-color .2s;position:relative;overflow:hidden}
.dff-cat-img{height:180px;background-size:cover;background-position:top;margin:-28px -24px 20px;border-radius:10px 10px 0 0}
.dff-cat-card:hover{transform:translateY(-4px);box-shadow:0 12px 36px rgba(255,95,66,.12);border-color:var(--ast-global-color-0,#ff5f42)}
.dff-cat-num{display:block;font-family:'Fraunces',serif;font-size:3rem;font-weight:800;color:rgba(0,0,0,0.07);line-height:1;margin-bottom:4px;position:absolute;top:16px;right:20px}
.dff-cat-icon{font-size:1.8rem;display:block;margin-bottom:10px}
.dff-cat-name{font-size:1.15rem;color:var(--ast-global-color-2,#313131);margin-bottom:.4em}
.dff-cat-desc{font-size:.85rem;color:var(--ast-global-color-3,#3a3a3a);line-height:1.55;margin-bottom:1.2rem}
.dff-cat-cta{font-size:.82rem;font-weight:600;color:var(--ast-global-color-0,#ff5f42);display:block}
/* NALLA CERTIFIED */
.dff-nalla{padding:80px 0;background:#f8f4ee}
.dff-nalla-inner{display:flex;gap:32px;align-items:center;background:var(--ast-global-color-5,#fff);border:1px solid var(--ast-global-color-4,#f5f5f5);border-radius:14px;padding:36px;box-shadow:0 2px 12px rgba(0,0,0,.05)}
.dff-nalla-badge{width:120px;height:auto;flex-shrink:0}
.dff-nalla-body h2{font-size:1.4rem;color:var(--ast-global-color-2,#313131)}
.dff-nalla-body p{line-height:1.6;color:var(--ast-global-color-1,#3a3a3a);margin-bottom:1rem}
.dff-nalla-cta{font-weight:600;color:var(--ast-global-color-0,#ff5f42);text-decoration:none}
.dff-nalla-cta:hover{text-decoration:underline}
/* BLOG */
.dff-blog{padding:80px 0;background:var(--ast-global-color-5,#fff)}
.dff-blog-hd{display:flex;align-items:baseline;justify-content:space-between;margin-bottom:2rem}
.dff-blog-all{color:var(--ast-global-color-0,#ff5f42);font-weight:600;font-size:.9rem;text-decoration:none}
.dff-blog-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:24px}
.dff-bpost-card{display:flex;flex-direction:column;background:var(--ast-global-color-5,#fff);border-radius:12px;overflow:hidden;border:1px solid var(--ast-global-color-4,#f5f5f5);text-decoration:none;color:inherit;transition:transform .2s,box-shadow .2s}
.dff-bpost-card:hover{transform:translateY(-4px);box-shadow:0 12px 36px rgba(0,0,0,.1)}
.dff-bpost-img{height:220px;background-size:cover;background-position:center;background-color:var(--ast-global-color-4,#f5f5f5)}
.dff-bpost-nophoto{background:linear-gradient(135deg,var(--ast-global-color-4,#f5f5f5),var(--ast-global-color-7,#fbfcff))}
.dff-bpost-body{padding:20px;flex:1;display:flex;flex-direction:column}
.dff-bpost-cat{font-size:.7rem;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:var(--ast-global-color-0,#ff5f42);margin-bottom:.5rem;display:block}
.dff-bpost-title{font-size:1rem;line-height:1.35;color:var(--ast-global-color-2,#313131);margin-bottom:.6rem}
.dff-bpost-excerpt{font-size:.85rem;color:var(--ast-global-color-3,#3a3a3a);line-height:1.55;flex:1;margin-bottom:1rem}
.dff-bpost-footer{display:flex;justify-content:space-between;align-items:center;margin-top:auto}
.dff-bpost-date{font-size:.75rem;color:var(--ast-global-color-3,#3a3a3a)}
.dff-bpost-cta{font-size:.78rem;font-weight:600;color:var(--ast-global-color-0,#ff5f42)}
/* NEWSLETTER */
.dff-nl{background:var(--ast-global-color-2,#313131);padding:72px clamp(20px,4vw,48px);text-align:center}
.dff-nl-inner{max-width:600px;margin:0 auto}
.dff-nl-h2{font-size:clamp(1.6rem,3vw,2.2rem);color:#fff;margin-bottom:.5em}
.dff-nl-sub{color:rgba(255,255,255,.75);margin-bottom:2rem;font-size:1rem;line-height:1.6}
.dff-nl-form{display:flex;gap:12px;flex-wrap:wrap;justify-content:center}
.dff-nl-form input{flex:1;min-width:220px;padding:13px 18px;border:1.5px solid rgba(255,255,255,.25);border-radius:var(--r);background:rgba(255,255,255,.08);color:#fff;font-size:.95rem;outline:none;font-family:'DM Sans',sans-serif}
.dff-nl-form input::placeholder{color:rgba(255,255,255,.5)}
.dff-nl-form input:focus{border-color:var(--ast-global-color-0,#ff5f42)}
.dff-nl-form button{background:var(--ast-global-color-0,#ff5f42);color:#fff;border:none;padding:13px 24px;border-radius:var(--r);font-weight:600;font-size:.95rem;cursor:pointer;transition:opacity .2s;font-family:'DM Sans',sans-serif}
.dff-nl-form button:hover{opacity:.88}
#vet-msg{color:rgba(255,255,255,.85);margin-top:12px;font-size:.9rem}
/* STEPS */
.dff-steps{padding:80px 0;background:#f8f4ee}
.dff-steps-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:20px;max-width:1200px;margin:0 auto}
.dff-step{background:var(--ast-global-color-5,#fff);border-radius:12px;padding:28px;box-shadow:0 2px 12px rgba(0,0,0,.05);border-left:3px solid var(--ast-global-color-0,#ff5f42);display:flex;gap:20px;align-items:flex-start}
.dff-step-num{width:52px;height:52px;border-radius:50%;background:var(--ast-global-color-0,#ff5f42);color:#fff;font-family:'Fraunces',serif;font-size:1.2rem;font-weight:800;display:flex;align-items:center;justify-content:center;flex-shrink:0;line-height:1}
.dff-step-body{flex:1}
.dff-step-title{font-size:1rem;color:var(--ast-global-color-2,#313131);margin-bottom:.4em}
.dff-step-desc{font-size:.87rem;line-height:1.6;color:var(--ast-global-color-3,#3a3a3a);margin:0}
/* MOBILE */
@media(max-width:900px){
  .dff-cats-grid{grid-template-columns:repeat(2,1fr)}
  .dff-about-inner{grid-template-columns:1fr;gap:32px}
  .dff-steps-grid{grid-template-columns:1fr;gap:16px}
  .dff-blog-grid{grid-template-columns:1fr}
}
@media(max-width:600px){
  .dff-cats-grid{grid-template-columns:1fr}
  .dff-hero{min-height:420px}
}
</style>"""


def _js() -> str:
    return """<script>
jQuery&&jQuery(function($){
  $('#vet-ajax-form').on('submit',function(e){
    e.preventDefault();
    var btn=$('#vet-btn'),msg=$('#vet-msg'),email=$(this).find('[name=email]').val();
    btn.text('...').prop('disabled',true);
    $.ajax({url:'/wp-admin/admin-ajax.php',type:'POST',data:{action:'vet_subscribe',email:email},
      success:function(r){
        if(r.success){btn.text('Yes, Send Me Recipes!').prop('disabled',false);$('#vet-ajax-form')[0].reset();msg.text("You're on the list! 🐾").fadeIn();setTimeout(function(){msg.fadeOut();},4000);}
        else{btn.text('Retry').prop('disabled',false);}
      },error:function(){btn.text('Error').prop('disabled',false);}
    });
  });
});
</script>"""
