"""HTML/CSS template for the dogfoodandfun.com Contact page.

Layout: hero → two-column intro+form → FAQ → CTA.
Form submits via fetch() to /wp-json/wp/v2/comments (core WP endpoint,
not blocked by JWT Auth) so messages land in WP Admin > Comments.
"""
from __future__ import annotations

_PAGE_ID = 2473

_FAQ = [
    (
        "How much should I feed my dog?",
        "Feeding amount depends on weight, activity level, and the food&#8217;s caloric density &#8212; not the generic &#8220;cups per day&#8221; chart on the bag. I calculate per-kcal needs for Nalla based on her weight and run frequency, then cross-reference the food&#8217;s ME value. Most bags underestimate active dogs by 15&#8211;20%.",
    ),
    (
        "Is grain-free food actually better?",
        "Not inherently. &#8220;Grain-free&#8221; replaced grains with legumes and potatoes &#8212; not automatically superior. The FDA flagged a potential link between legume-heavy diets and dilated cardiomyopathy in 2018. Named proteins and digestible carbohydrates matter more than the grain-free label.",
    ),
    (
        "How do I know if my dog has a food allergy?",
        "True food allergies are less common than most owners think &#8212; environmental allergies look nearly identical. Chronic ear infections, paw licking, and GI upset after meals are the clearest signals. An elimination diet (single protein source, 8+ weeks) is the only reliable diagnostic. Vet sign-off before starting.",
    ),
]


def build_html() -> str:
    faq_items = "\n".join(
        f"""<div class="dff-ct-faq-item">
  <button class="dff-ct-faq-q" aria-expanded="false">{q}<span class="dff-ct-faq-icon">+</span></button>
  <div class="dff-ct-faq-a" hidden><p>{a}</p></div>
</div>"""
        for q, a in _FAQ
    )

    return f"""<!-- wp:html -->
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Fraunces:ital,opsz,wght@0,9..144,700;0,9..144,800;1,9..144,400&family=DM+Sans:opsz,wght@9..40,400;9..40,500;9..40,600&display=swap" rel="stylesheet">

<div class="dff-ct">

<section class="dff-ct-hero">
  <div class="dff-ct-hero-inner">
    <span class="dff-ct-eyebrow">&#127988; No robots, no generic responses</span>
    <h1 class="dff-ct-h1">Ask Me Anything<br><em class="dff-ct-accent">About Dog Food.</em></h1>
    <p class="dff-ct-hero-sub">Ingredient questions, product recommendations, picky eaters, sensitive stomachs &#8212; I read every message personally and give you a real answer.</p>
  </div>
</section>

<section class="dff-ct-body">
  <div class="dff-ct-inner dff-ct-body-inner">

    <div class="dff-ct-intro">
      <h2 class="dff-ct-h2">Get in Touch</h2>
      <p>Whether you&#8217;re switching your dog&#8217;s food, comparing two brands, or want a second opinion on an ingredient panel &#8212; send it over. No question is too basic.</p>
      <ul class="dff-ct-promise-list">
        <li><span class="dff-ct-check">&#10003;</span>Personal reply within 24&#8211;48 hours</li>
        <li><span class="dff-ct-check">&#10003;</span>Honest answer, not a sales pitch</li>
        <li><span class="dff-ct-check">&#10003;</span>Your email stays private &#8212; always</li>
      </ul>
      <p class="dff-ct-email-note">Prefer direct email? <a href="mailto:info@dogfoodandfun.com">info@dogfoodandfun.com</a></p>
    </div>

    <div class="dff-ct-form-wrap">
      <form id="dff-cf" novalidate>
        <div class="dff-cf-row">
          <div class="dff-cf-field">
            <label for="dff-cf-fname">First name <span aria-hidden="true">*</span></label>
            <input id="dff-cf-fname" type="text" placeholder="Nalla&#8217;s Dad" required autocomplete="given-name">
          </div>
          <div class="dff-cf-field">
            <label for="dff-cf-lname">Last name</label>
            <input id="dff-cf-lname" type="text" placeholder="Optional" autocomplete="family-name">
          </div>
        </div>
        <div class="dff-cf-field">
          <label for="dff-cf-email">Email <span aria-hidden="true">*</span></label>
          <input id="dff-cf-email" type="email" placeholder="you@example.com" required autocomplete="email">
        </div>
        <div class="dff-cf-field">
          <label for="dff-cf-body">Message <span aria-hidden="true">*</span></label>
          <textarea id="dff-cf-body" rows="5" placeholder="What&#8217;s your question?" required></textarea>
        </div>
        <div id="dff-cf-status" role="alert" hidden></div>
        <button type="submit" id="dff-cf-btn">Send Message</button>
      </form>
    </div>

  </div>
</section>

<section class="dff-ct-faq">
  <div class="dff-ct-inner">
    <span class="dff-ct-eyebrow">Common Questions</span>
    <h2 class="dff-ct-h2">Frequently Asked</h2>
    <div class="dff-ct-faq-list">{faq_items}</div>
  </div>
</section>

<section class="dff-ct-cta">
  <div class="dff-ct-inner dff-ct-cta-inner">
    <div>
      <h2 class="dff-ct-h2 dff-ct-cta-h2">Not sure what to ask?</h2>
      <p class="dff-ct-cta-sub">Browse the reviews and guides &#8212; the answer might already be there.</p>
    </div>
    <div class="dff-ct-cta-btns">
      <a class="dff-ct-btn" href="/blog/">Browse All Guides</a>
      <a class="dff-ct-btn-ghost" href="/recipes/">Dog Food Recipes</a>
    </div>
  </div>
</section>

</div>

{_css()}
{_js()}
<!-- /wp:html -->"""


def _css() -> str:
    return """<style>
.dff-ct{font-family:'DM Sans',-apple-system,sans-serif;color:var(--ast-global-color-1,#3a3a3a);--r:8px;background:var(--ast-global-color-5,#fff)}
.dff-ct *{box-sizing:border-box}
.dff-ct a{text-decoration:none !important}
.dff-ct h1,.dff-ct h2,.dff-ct h3{font-family:'Fraunces',Georgia,serif;line-height:1.15;margin:0 0 .6em}
.dff-ct-inner{max-width:1200px;margin:0 auto;padding:0 clamp(20px,4vw,48px)}
.dff-ct-eyebrow{font-size:.75rem;font-weight:600;letter-spacing:.1em;text-transform:uppercase;color:var(--ast-global-color-0,#ff5f42);display:block;margin-bottom:.6rem}
.dff-ct-h2{font-size:clamp(1.6rem,3vw,2.2rem) !important;color:var(--ast-global-color-2,#313131);margin-bottom:1.2rem}
.dff-ct-accent{color:var(--ast-global-color-0,#ff5f42);font-style:italic}
.dff-ct-btn{display:inline-block;background:var(--ast-global-color-0,#ff5f42);color:#fff;font-weight:600;padding:13px 28px;border-radius:var(--r);text-decoration:none !important;transition:opacity .2s,transform .2s;font-size:.95rem}
.dff-ct-btn:hover{opacity:.88;transform:translateY(-1px);color:#fff}
.dff-ct-btn-ghost{display:inline-block;border:2px solid rgba(255,255,255,.4);color:#fff;font-weight:600;padding:11px 26px;border-radius:var(--r);text-decoration:none !important;transition:border-color .2s,background .2s;font-size:.95rem}
.dff-ct-btn-ghost:hover{border-color:#fff;background:rgba(255,255,255,.1);color:#fff}
/* HERO */
.dff-ct-hero{background:linear-gradient(135deg,#f8f4ee 0%,#fdf6ee 55%,#f0ebe2 100%);margin-left:calc(-50vw + 50%);margin-right:calc(-50vw + 50%);width:100vw;max-width:100vw;padding:80px clamp(24px,5vw,80px) 72px;position:relative;overflow:hidden}
.dff-ct-hero::after{content:'?';position:absolute;right:4%;bottom:-40px;font-family:'Fraunces',Georgia,serif;font-size:300px;font-weight:800;color:rgba(255,95,66,.05);line-height:1;pointer-events:none;user-select:none}
.dff-ct-hero-inner{max-width:680px;position:relative}
.dff-ct-h1{font-size:clamp(2.4rem,5.5vw,4.2rem) !important;font-weight:800;color:var(--ast-global-color-2,#313131);line-height:1.1;margin:.5em 0 1em}
.dff-ct-hero-sub{font-size:1.05rem;line-height:1.7;color:#666;max-width:500px;margin:0}
/* BODY */
.dff-ct-body{padding:80px 0;background:var(--ast-global-color-5,#fff)}
.dff-ct-body-inner{display:grid;grid-template-columns:1fr 1.2fr;gap:64px;align-items:flex-start}
.dff-ct-intro p{line-height:1.75;color:var(--ast-global-color-1,#3a3a3a);margin-bottom:1.2rem}
.dff-ct-promise-list{list-style:none;margin:.5rem 0 1.5rem;padding:0;display:flex;flex-direction:column;gap:10px}
.dff-ct-promise-list li{display:flex;gap:10px;align-items:flex-start;font-size:.93rem;line-height:1.5}
.dff-ct-check{color:#22c55e;font-weight:700;flex-shrink:0}
.dff-ct-email-note{font-size:.88rem;color:#888;margin-top:1.5rem}
.dff-ct-email-note a{color:var(--ast-global-color-0,#ff5f42);font-weight:600}
/* FORM */
.dff-ct-form-wrap form{background:#fdf9f7;border-radius:12px;padding:32px;border:1px solid rgba(0,0,0,.07);display:flex;flex-direction:column;gap:18px}
.dff-cf-row{display:grid;grid-template-columns:1fr 1fr;gap:16px}
.dff-cf-field{display:flex;flex-direction:column;gap:6px}
.dff-cf-field label{font-size:.82rem;font-weight:600;color:var(--ast-global-color-2,#313131)}
.dff-cf-field label span{color:var(--ast-global-color-0,#ff5f42)}
.dff-cf-field input,.dff-cf-field textarea{padding:12px 14px;font-size:.93rem;font-family:'DM Sans',-apple-system,sans-serif;border:1.5px solid #e0e0e0;border-radius:var(--r);background:#fff;color:var(--ast-global-color-2,#313131);outline:none;transition:border-color .2s;width:100%}
.dff-cf-field input:focus,.dff-cf-field textarea:focus{border-color:var(--ast-global-color-0,#ff5f42)}
.dff-cf-field textarea{resize:vertical;min-height:120px}
#dff-cf-btn{background:var(--ast-global-color-0,#ff5f42);color:#fff;border:none;padding:13px 28px;border-radius:var(--r);font-weight:600;font-size:.95rem;cursor:pointer;font-family:'DM Sans',-apple-system,sans-serif;transition:opacity .2s;align-self:flex-start}
#dff-cf-btn:hover{opacity:.88}
#dff-cf-btn:disabled{opacity:.6;cursor:not-allowed}
#dff-cf-status{padding:10px 14px;border-radius:var(--r);font-size:.88rem;line-height:1.5}
.dff-cf-ok{background:#f0fdf4;color:#166534;border:1px solid #bbf7d0}
.dff-cf-err{background:#fff1f2;color:#9f1239;border:1px solid #fecdd3}
/* FAQ */
.dff-ct-faq{padding:80px 0;background:#f8f4ee}
.dff-ct-faq-list{display:flex;flex-direction:column;gap:12px;margin-top:2rem;max-width:800px}
.dff-ct-faq-item{background:var(--ast-global-color-5,#fff);border-radius:10px;border:1px solid rgba(0,0,0,.06);overflow:hidden}
.dff-ct-faq-q{width:100%;display:flex;justify-content:space-between;align-items:center;gap:16px;padding:20px 24px;background:none;border:none;cursor:pointer;font-family:'Fraunces',Georgia,serif;font-size:1.05rem;font-weight:700;color:var(--ast-global-color-2,#313131);text-align:left;transition:color .2s}
.dff-ct-faq-q:hover,.dff-ct-faq-q[aria-expanded="true"]{color:var(--ast-global-color-0,#ff5f42)}
.dff-ct-faq-icon{font-size:1.4rem;font-weight:300;flex-shrink:0;transition:transform .25s;line-height:1}
.dff-ct-faq-q[aria-expanded="true"] .dff-ct-faq-icon{transform:rotate(45deg)}
.dff-ct-faq-a{padding:0 24px 20px;border-top:1px solid rgba(0,0,0,.06)}
.dff-ct-faq-a p{margin:.8rem 0 0;line-height:1.7;font-size:.93rem;color:var(--ast-global-color-1,#3a3a3a)}
/* CTA BAND */
.dff-ct-cta{padding:72px clamp(20px,4vw,48px);background:var(--ast-global-color-2,#313131)}
.dff-ct-cta-inner{max-width:1200px;margin:0 auto;display:flex;align-items:center;justify-content:space-between;gap:32px;flex-wrap:wrap}
.dff-ct-cta-h2{color:#fff !important;margin-bottom:.3em}
.dff-ct-cta-sub{color:rgba(255,255,255,.75);font-size:1rem;margin:0}
.dff-ct-cta-btns{display:flex;gap:16px;flex-wrap:wrap}
/* MOBILE */
@media(max-width:900px){
  .dff-ct-body-inner{grid-template-columns:1fr;gap:40px}
  .dff-ct-cta-inner{flex-direction:column;align-items:flex-start}
  .dff-cf-row{grid-template-columns:1fr}
}
</style>"""


def _js() -> str:
    return f"""<script>
(function(){{
  var form=document.getElementById('dff-cf');
  var btn=document.getElementById('dff-cf-btn');
  var status=document.getElementById('dff-cf-status');

  function showMsg(text,ok){{
    status.textContent=text;
    status.className=ok?'dff-cf-ok':'dff-cf-err';
    status.hidden=false;
    status.scrollIntoView({{behavior:'smooth',block:'nearest'}});
  }}

  form.addEventListener('submit',function(e){{
    e.preventDefault();
    var fname=document.getElementById('dff-cf-fname').value.trim();
    var lname=document.getElementById('dff-cf-lname').value.trim();
    var email=document.getElementById('dff-cf-email').value.trim();
    var body=document.getElementById('dff-cf-body').value.trim();
    if(!fname||!email||!body){{showMsg('Please fill in all required fields.',false);return;}}
    btn.textContent='Sending…';btn.disabled=true;status.hidden=true;
    var fd=new FormData();
    fd.append('comment_post_ID','{_PAGE_ID}');
    fd.append('comment',body);
    fd.append('author',fname+(lname?' '+lname:''));
    fd.append('email',email);
    fd.append('redirect_to',window.location.href);
    fetch('/wp-comments-post.php',{{method:'POST',body:fd,redirect:'manual'}})
    .then(function(r){{
      // wp-comments-post.php always 302-redirects on success; opaqueredirect = success
      if(r.type==='opaqueredirect'||r.status===0||r.status===302){{
        form.reset();
        showMsg("Message sent! I’ll reply within 24–48 hours. 🐾",true);
      }}else{{
        throw new Error('status '+r.status);
      }}
    }})
    .catch(function(){{
      showMsg('Something went wrong. Email info@dogfoodandfun.com directly.',false);
    }})
    .finally(function(){{btn.textContent='Send Message';btn.disabled=false;}});
  }});

  document.querySelectorAll('.dff-ct-faq-q').forEach(function(b){{
    b.addEventListener('click',function(){{
      var open=this.getAttribute('aria-expanded')==='true';
      this.setAttribute('aria-expanded',String(!open));
      this.nextElementSibling.hidden=open;
    }});
  }});
}})();
</script>"""
