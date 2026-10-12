"""Questions typees, identiques pour Jev et Opus, et construction de l'etat (le meme pour les deux).

Deux appels par lead et par modele :
  1. `contact` (choice) : quel lien interne ouvrir pour trouver un email (ou aucun) ;
  2. qualification pour l'offre d'Alan : `fit` (noul), `stage` (choice), `has_video` (noul), `priority` (score 0-3).

L'offre d'Alan : demarcher des TPE / PME, indie makers et startups qui ont leur propre application ou produit
numerique, pour leur faire une video en motion design (promo, lancement, explication) de cette application.
"""

OFFER = ("Alan is a motion designer. He prospects small businesses (TPE/PME: small and mid-sized companies, indie "
         "makers, startups) that have their own app or digital product, to sell them a short motion-design video "
         "(animated promo, launch or explainer video) that presents that app.")

LINK_INSTR = ("`site` is the homepage of a lead found on GitHub. Which of its internal links (the options) is the most "
              "likely to show a way to email the person or team behind the site (contact page, about or team page, press "
              "kit, legal notice / imprint / privacy policy listing an email)? Answer \"none\" if no link is likely to.")

STAGES = {
    "prelaunch": "Pre-launch: waitlist, beta, early access or 'coming soon' for their own app or product",
    "just_launched": "Their own app, SaaS or product is live but still new or small (indie or startup product, early users)",
    "small_business": "Established small or mid-sized business (TPE/PME, a few people to a few dozen) with its own live "
                      "app or digital product and real customers",
    "big_company": "Large or well-known company: big user base, many employees, many years on the market",
    "agency": "Agency, studio, freelancer or consultant selling services to clients (no product of their own)",
    "no_product": "Personal site, portfolio, blog, CV, or open-source library without a product to promote",
    "unclear": "Not enough information to tell",
}

QUAL_Q = {
    "fit": {
        "type": "noul",
        "instructions": OFFER + " Is `lead` a good prospect for Alan? True only if `lead` is a small business, indie "
                        "maker or startup (not a large company) that has its own app, SaaS or digital product shown on "
                        "`site`, which a short motion-design promo or launch video could help promote: about to launch, "
                        "recently launched, or live and still small, and not already promoted with a polished video. "
                        "False for agencies, studios, freelancers or consultants selling services, personal sites, "
                        "portfolios, blogs or CVs without a product, students, job seekers, employees, large or "
                        "well-known companies and pure open-source libraries.",
    },
    "stage": {
        "type": "choice",
        "instructions": "What best describes `lead` and the product shown on `site`?",
        "criteria": STAGES,
    },
    "has_video": {
        "type": "noul",
        "instructions": "Does `site` already show a polished promo, demo or launch video of the product "
                        "(see `site.signals.video`)?",
    },
    "priority": {
        "type": "score",
        "instructions": "How urgently should Alan contact `lead` to offer a motion-design video for their app? Consider "
                        "the fit (small business with its own product), the launch timing, a missing promo video and "
                        "whether `contact` shows a way to reach them.",
        "criteria": [
            "Do not contact",
            "Low priority",
            "Good prospect: contact this week",
            "Hot prospect: small business or startup with its own app, launching or just launched, no promo video, reachable",
        ],
    },
}

MAX_LINKS = 8


def pick_candidates(links: list[dict]) -> list[dict]:
    """Coupe structurelle, sans mots-cles (pour ne pas faire le travail des modeles a leur place) :
    les premiers liens (menu du haut) et les derniers (pied de page)."""
    if len(links) <= MAX_LINKS:
        return links
    half = MAX_LINKS // 2
    return links[:MAX_LINKS - half] + links[-half:]


def link_question(cands: dict) -> dict:
    crit = {k: f"'{c['text'] or '(no text)'}' -> {c['path']}" for k, c in cands.items()}
    crit["none"] = "None of these links is likely to show contact details"
    return {"contact": {"type": "choice", "instructions": LINK_INSTR, "criteria": crit}}


def link_state(host: str, page: dict, cands: dict) -> dict:
    # les liens ne sont que dans les options de la question (pas en double dans l'etat : moins de jetons pour Opus)
    return {"site": {"host": host, "title": page.get("title", "")[:120], "description": page.get("description", "")[:200]}}


def qual_state(profile: dict, host: str, page: dict, shared_emails: list[str]) -> dict:
    sig = dict(page.get("signals") or {})
    return {
        "lead": {
            "github_bio": (profile.get("bio") or "")[:240],
            "company": (profile.get("company") or "")[:60],
            "location": (profile.get("location") or "")[:60],
            "github_followers": profile.get("follower_count", 0),
            "public_repos": profile.get("public_repos", 0),
            "open_to_hire": bool(profile.get("is_hireable")),
        },
        "site": {
            "host": host,
            "title": page.get("title", "")[:120],
            "description": page.get("description", "")[:220],
            "headings": page.get("headings", [])[:5],
            "text": page.get("text", "")[:500],
            "signals": sig,
        },
        "contact": {
            "public_email_on_github": bool(profile.get("email")),
            "emails_on_homepage": len(shared_emails),
        },
    }
