"""HTML/CSS template for the dogfoodandfun.com About Me page.

Design system: Fraunces + DM Sans, coral accents, warm parchment sections —
identical to the homepage so both pages feel like one cohesive brand.
"""
from __future__ import annotations
import html as hl

_ABOUT_IMG = "https://dogfoodandfun.com/wp-content/uploads/2026/01/ChatGPT-Image-Jan-10-2026-06_28_33-PM.png"
_BADGE_IMG = "https://dogfoodandfun.com/wp-content/uploads/2026/05/nalla-approved-badge.png"

_METHOD = [
    ("01", "🔬", "Ingredient Forensics", "Full AAFCO label reads — named proteins, fillers, allergens, and hidden carb loads. No marketing copy accepted."),
    ("02", "🧮", "Cost-Per-Serving Math", "Actual value per day, regardless of bag price. A so-called premium bag that's cheaper per serving beats a budget one that isn't."),
    ("03", "📊", "Comparative Benchmarking", "Every product stacked against 2–3 direct competitors on protein density, ingredient quality score, and cost."),
    ("04", "📚", "Peer-Reviewed Sourcing", "Veterinary research only. No influencer opinions, no brand-funded studies, no anecdotal claims without data."),
]

_IS_LIST = [
    "A software engineer who treats pet nutrition like a spec sheet",
    "Someone who has tested dozens of foods on a sensitive-stomached shepherd mix",
    "Independent — no brand partnerships that compromise editorial standards",
    "A dog owner first, a reviewer second",
]

_NOT_LIST = [
    "A veterinarian or certified pet nutritionist",
    "A brand partner or sponsored content creator",
    "A medical authority — always verify health decisions with your vet",
]


def build_html() -> str:
    method_cards = "\n".join(
        f"""<div class="dff-abpg-mcard">
  <span class="dff-abpg-mnum">{num}</span>
  <span class="dff-abpg-micon">{icon}</span>
  <h3 class="dff-abpg-mtitle">{title}</h3>
  <p class="dff-abpg-mdesc">{desc}</p>
</div>"""
        for num, icon, title, desc in _METHOD
    )
    is_items = "\n".join(
        f'<li><span class="dff-abpg-check">&#10003;</span>{hl.escape(item)}</li>'
        for item in _IS_LIST
    )
    not_items = "\n".join(
        f'<li><span class="dff-abpg-x">&#10005;</span>{hl.escape(item)}</li>'
        for item in _NOT_LIST
    )

    return f"""<!-- wp:html -->
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Fraunces:ital,opsz,wght@0,9..144,700;0,9..144,800;1,9..144,400&family=DM+Sans:opsz,wght@9..40,400;9..40,500;9..40,600&display=swap" rel="stylesheet">

<div class="dff-abpg">

<section class="dff-abpg-hero">
  <div class="dff-abpg-hero-inner">
    <span class="dff-abpg-eyebrow">&#127988; The Person Behind the Reviews</span>
    <h1 class="dff-abpg-h1">
      <span class="dff-abpg-h1-line">Engineer.</span>
      <span class="dff-abpg-h1-line">Dog Dad.</span>
      <span class="dff-abpg-h1-line dff-abpg-accent">Skeptic.</span>
    </h1>
    <p class="dff-abpg-hero-sub">I turned Nalla&#8217;s food sensitivities into a research project. This site is what I wish had existed when I started.</p>
  </div>
</section>

<section class="dff-abpg-story">
  <div class="dff-abpg-inner dff-abpg-story-inner">
    <div class="dff-abpg-story-text">
      <span class="dff-abpg-eyebrow">The Origin Story</span>
      <h2 class="dff-abpg-h2">Why I Built Dog Food &amp; Fun</h2>
      <p>When I got Nalla &#8212; a fluffy shepherd mix with a stomach that rejected half the brands on the shelf &#8212; I went looking for honest guidance. What I found was a wall of sponsored content, vague ingredient lists, and influencer recommendations paid for by the brands they praised.</p>
      <blockquote class="dff-abpg-quote">&#8220;I realized most dog food labels were designed to confuse, not inform. I wasn&#8217;t satisfied with &#8216;good enough&#8217; for Nalla, so I started doing the research myself.&#8221;</blockquote>
      <p>I&#8217;m a software engineer by trade &#8212; comfortable with data, skeptical of marketing claims, and allergic to accepting &#8220;premium&#8221; as a data point. Dog Food &amp; Fun is me applying that same mindset to every bag of kibble, every GPS collar, and every grooming tool I review.</p>
    </div>
    <div class="dff-abpg-story-img">
      <img src="{_ABOUT_IMG}" alt="Nalla the shepherd mix" loading="lazy">
      <p class="dff-abpg-img-caption">Nalla &#8212; chief taste tester &amp; co-founder</p>
    </div>
  </div>
</section>

<section class="dff-abpg-method">
  <div class="dff-abpg-inner">
    <span class="dff-abpg-eyebrow">How I Work</span>
    <h2 class="dff-abpg-h2">My Methodology</h2>
    <div class="dff-abpg-method-grid">{method_cards}</div>
  </div>
</section>

<section class="dff-abpg-iam">
  <div class="dff-abpg-inner">
    <h2 class="dff-abpg-h2">What I Am &#8212; and What I&#8217;m Not</h2>
    <div class="dff-abpg-iam-inner">
      <div class="dff-abpg-iam-col dff-abpg-iam-yes">
        <h3 class="dff-abpg-iam-hd dff-abpg-iam-hd-yes">What I Am</h3>
        <ul class="dff-abpg-list">{is_items}</ul>
      </div>
      <div class="dff-abpg-iam-col dff-abpg-iam-no">
        <h3 class="dff-abpg-iam-hd dff-abpg-iam-hd-no">What I&#8217;m Not</h3>
        <ul class="dff-abpg-list">{not_items}</ul>
      </div>
    </div>
  </div>
</section>

<section class="dff-abpg-nalla">
  <div class="dff-abpg-inner dff-abpg-nalla-inner">
    <img src="{_BADGE_IMG}" alt="Nalla Certified badge" class="dff-abpg-nbadge" loading="lazy">
    <div class="dff-abpg-nalla-body">
      <span class="dff-abpg-eyebrow">&#128054; Meet the Co-Founder</span>
      <h2 class="dff-abpg-h2" style="margin-top:.4em">About Nalla</h2>
      <p>Nalla is a fluffy shepherd mix. She has a sensitive stomach, loves long runs, and has personally tested more dog food brands than most reviewers twice her age. She has never accepted a sponsorship.</p>
      <div class="dff-abpg-nstats">
        <div class="dff-abpg-nstat"><span class="dff-abpg-nstat-val">50+</span><span class="dff-abpg-nstat-lbl">Brands Tested</span></div>
        <div class="dff-abpg-nstat"><span class="dff-abpg-nstat-val">3+</span><span class="dff-abpg-nstat-lbl">Years of Data</span></div>
        <div class="dff-abpg-nstat"><span class="dff-abpg-nstat-val">0</span><span class="dff-abpg-nstat-lbl">Paid Partnerships</span></div>
      </div>
      <a class="dff-abpg-btn" href="/tag/nalla-certified/">See Everything Nalla Has Tested &#8594;</a>
    </div>
  </div>
</section>

<section class="dff-abpg-trans">
  <div class="dff-abpg-inner">
    <h2 class="dff-abpg-h2 dff-abpg-trans-h2">Transparency</h2>
    <p class="dff-abpg-trans-body">This site uses affiliate links &#8212; I earn a small commission if you buy through a link, at no extra cost to you. I have declined several sponsored partnership requests that didn&#8217;t meet my standards. Every product was either purchased by me or personally tested by Nalla before a link went live. Affiliate income helps keep the site independent; it never influences which products I recommend.</p>
    <a class="dff-abpg-trans-link" href="/blog/">Read the Latest Reviews &#8594;</a>
  </div>
</section>

</div>

{_css()}
<!-- /wp:html -->"""


def _css() -> str:
    return """<style>
.dff-abpg{font-family:'DM Sans',-apple-system,sans-serif;color:var(--ast-global-color-1,#3a3a3a);--r:8px;background:var(--ast-global-color-5,#fff)}
.dff-abpg *{box-sizing:border-box}
.dff-abpg a{text-decoration:none !important}
.dff-abpg h1,.dff-abpg h2,.dff-abpg h3{font-family:'Fraunces',Georgia,serif;line-height:1.15;margin:0 0 .6em}
.dff-abpg-inner{max-width:1200px;margin:0 auto;padding:0 clamp(20px,4vw,48px)}
.dff-abpg-eyebrow{font-size:.75rem;font-weight:600;letter-spacing:.1em;text-transform:uppercase;color:var(--ast-global-color-0,#ff5f42);display:block;margin-bottom:.6rem}
.dff-abpg-h2{font-size:clamp(1.6rem,3vw,2.2rem) !important;color:var(--ast-global-color-2,#313131);margin-bottom:1.5rem}
.dff-abpg-accent{color:var(--ast-global-color-0,#ff5f42);font-style:italic}
.dff-abpg-btn{display:inline-block;background:var(--ast-global-color-0,#ff5f42);color:#fff;font-weight:600;padding:13px 28px;border-radius:var(--r);text-decoration:none !important;transition:opacity .2s,transform .2s;font-size:.95rem}
.dff-abpg-btn:hover{opacity:.88;transform:translateY(-1px);color:#fff}
/* HERO */
.dff-abpg-hero{background:linear-gradient(135deg,#f8f4ee 0%,#fdf6ee 60%,#f0ebe2 100%);margin-left:calc(-50vw + 50%);margin-right:calc(-50vw + 50%);width:100vw;max-width:100vw;padding:100px clamp(24px,5vw,80px) 80px;position:relative;overflow:hidden}
.dff-abpg-hero::before{content:'"';position:absolute;right:5%;top:-80px;font-family:'Fraunces',Georgia,serif;font-size:360px;font-weight:800;color:rgba(255,95,66,.06);line-height:1;pointer-events:none;user-select:none}
.dff-abpg-hero-inner{max-width:700px;position:relative}
.dff-abpg-h1{font-size:clamp(3rem,7vw,5.5rem) !important;font-weight:800;color:var(--ast-global-color-2,#313131);line-height:1.05;display:flex;flex-direction:column;gap:.08em;margin:.5em 0 1em}
.dff-abpg-h1-line{display:block;opacity:0;transform:translateY(22px);animation:dff-abpg-rise .6s ease forwards}
.dff-abpg-h1-line:nth-child(1){animation-delay:.08s}
.dff-abpg-h1-line:nth-child(2){animation-delay:.22s}
.dff-abpg-h1-line:nth-child(3){animation-delay:.38s}
@keyframes dff-abpg-rise{to{opacity:1;transform:none}}
.dff-abpg-hero-sub{font-size:1.1rem;line-height:1.7;color:#666;max-width:520px;margin:0}
/* STORY */
.dff-abpg-story{padding:80px 0;background:var(--ast-global-color-5,#fff)}
.dff-abpg-story-inner{display:grid;grid-template-columns:1fr 1fr;gap:64px;align-items:center}
.dff-abpg-story-text p{line-height:1.75;color:var(--ast-global-color-1,#3a3a3a);margin-bottom:1.2rem;font-size:1rem}
.dff-abpg-quote{border-left:4px solid var(--ast-global-color-0,#ff5f42);margin:2rem 0;padding:.9rem 1.4rem;font-family:'Fraunces',Georgia,serif;font-size:1.12rem;font-style:italic;color:var(--ast-global-color-2,#313131);line-height:1.55;background:#fdf9f7;border-radius:0 6px 6px 0}
.dff-abpg-story-img img{width:100%;border-radius:12px;display:block;object-fit:cover;aspect-ratio:4/3}
.dff-abpg-img-caption{font-size:.78rem;color:#999;text-align:center;margin:.6rem 0 0;font-style:italic}
/* METHODOLOGY */
.dff-abpg-method{padding:80px 0;background:#f8f4ee}
.dff-abpg-method-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:20px;margin-top:2rem}
.dff-abpg-mcard{background:var(--ast-global-color-5,#fff);border-radius:12px;padding:28px 24px;border:1px solid rgba(0,0,0,.06);position:relative;overflow:hidden;transition:transform .2s,box-shadow .2s}
.dff-abpg-mcard:hover{transform:translateY(-4px);box-shadow:0 12px 36px rgba(255,95,66,.1)}
.dff-abpg-mnum{position:absolute;top:14px;right:18px;font-family:'Fraunces',serif;font-size:2.8rem;font-weight:800;color:rgba(0,0,0,.06);line-height:1}
.dff-abpg-micon{font-size:1.8rem;display:block;margin-bottom:12px}
.dff-abpg-mtitle{font-size:1rem;color:var(--ast-global-color-2,#313131);margin-bottom:.4em}
.dff-abpg-mdesc{font-size:.85rem;line-height:1.6;color:#666;margin:0}
/* WHAT I AM */
.dff-abpg-iam{padding:80px 0;background:var(--ast-global-color-5,#fff)}
.dff-abpg-iam-inner{display:grid;grid-template-columns:1fr 1fr;gap:32px;margin-top:2rem}
.dff-abpg-iam-col{border-radius:12px;padding:32px;border:1px solid rgba(0,0,0,.08)}
.dff-abpg-iam-yes{border-top:3px solid #22c55e}
.dff-abpg-iam-no{border-top:3px solid var(--ast-global-color-0,#ff5f42)}
.dff-abpg-iam-hd{font-size:1.1rem;margin:0 0 1.2rem}
.dff-abpg-iam-hd-yes{color:#16a34a}
.dff-abpg-iam-hd-no{color:var(--ast-global-color-0,#ff5f42)}
.dff-abpg-list{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:14px}
.dff-abpg-list li{display:flex;gap:12px;align-items:flex-start;font-size:.93rem;line-height:1.55;color:var(--ast-global-color-1,#3a3a3a)}
.dff-abpg-check{color:#22c55e;font-weight:700;flex-shrink:0;margin-top:.1em}
.dff-abpg-x{color:var(--ast-global-color-0,#ff5f42);font-weight:700;flex-shrink:0;margin-top:.1em}
/* NALLA */
.dff-abpg-nalla{padding:80px 0;background:#f8f4ee}
.dff-abpg-nalla-inner{display:flex;gap:40px;align-items:flex-start}
.dff-abpg-nbadge{width:130px;height:auto;flex-shrink:0;margin-top:8px}
.dff-abpg-nalla-body p{line-height:1.7;color:var(--ast-global-color-1,#3a3a3a);margin-bottom:1.5rem}
.dff-abpg-nstats{display:flex;gap:32px;margin-bottom:2rem;flex-wrap:wrap}
.dff-abpg-nstat{display:flex;flex-direction:column;gap:4px}
.dff-abpg-nstat-val{font-family:'Fraunces',serif;font-size:2.2rem;font-weight:800;color:var(--ast-global-color-0,#ff5f42);line-height:1}
.dff-abpg-nstat-lbl{font-size:.75rem;font-weight:600;text-transform:uppercase;letter-spacing:.07em;color:#888}
/* TRANSPARENCY */
.dff-abpg-trans{padding:80px 0;background:var(--ast-global-color-2,#313131)}
.dff-abpg-trans-h2{color:#fff !important}
.dff-abpg-trans-body{line-height:1.75;color:rgba(255,255,255,.78);font-size:1rem;max-width:720px;margin-bottom:2rem}
.dff-abpg-trans-link{color:var(--ast-global-color-0,#ff5f42);font-weight:600;font-size:.95rem}
.dff-abpg-trans-link:hover{text-decoration:underline !important}
/* MOBILE */
@media(max-width:900px){
  .dff-abpg-story-inner{grid-template-columns:1fr;gap:36px}
  .dff-abpg-method-grid{grid-template-columns:repeat(2,1fr)}
  .dff-abpg-iam-inner{grid-template-columns:1fr}
  .dff-abpg-nalla-inner{flex-direction:column;gap:24px}
}
@media(max-width:600px){
  .dff-abpg-method-grid{grid-template-columns:1fr}
  .dff-abpg-hero{padding:72px clamp(20px,5vw,48px) 60px}
}
</style>"""
